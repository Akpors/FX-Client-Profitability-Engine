"""Tests for the ECB reference-rate client, validation and snapshot I/O."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from fxengine import ecb
from fxengine.cli import main
from fxengine.ecb import (
    CURRENCIES,
    EcbDataGapError,
    EcbFetchError,
    build_url,
    fetch_rates,
    latest_snapshot,
    parse_ecb_csv,
    read_snapshot,
    validate_complete,
    window_for,
    window_from_snapshot,
    write_snapshot,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ecb_sample.csv"
CSV_HEADER = "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS"


def _fixture_df() -> pd.DataFrame:
    return parse_ecb_csv(FIXTURE.read_text())


def _weekdays(start: date, end: date) -> list[date]:
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5]


def _ecb_csv(
    start: date, end: date, currencies=CURRENCIES, skip: frozenset[tuple[date, str]] = frozenset()
) -> str:
    """A csvdata response in the ECB's column layout: one row per weekday and currency."""
    lines = [CSV_HEADER]
    for ccy_index, ccy in enumerate(currencies):
        for d in _weekdays(start, end):
            if (d, ccy) in skip:
                continue
            rate = 1.0 + ccy_index
            lines.append(f"EXR.D.{ccy}.EUR.SP00.A,D,{ccy},EUR,SP00,A,{d.isoformat()},{rate},A")
    return "\n".join(lines) + "\n"


def _client_returning(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- window and URL -------------------------------------------------------------------------


def test_window_for_mid_month():
    assert window_for(date(2026, 11, 12)) == (date(2024, 11, 1), date(2026, 10, 31))


def test_window_for_first_of_month():
    assert window_for(date(2026, 11, 1)) == (date(2024, 11, 1), date(2026, 10, 31))


def test_window_for_january_crosses_year_boundary():
    assert window_for(date(2026, 1, 15)) == (date(2024, 1, 1), date(2025, 12, 31))


def test_build_url():
    url = build_url(["USD", "JPY"], date(2025, 3, 3), date(2025, 3, 14))
    assert url == (
        "https://data-api.ecb.europa.eu/service/data/EXR/D.USD+JPY.EUR.SP00.A"
        "?startPeriod=2025-03-03&endPeriod=2025-03-14&format=csvdata"
    )


def test_currencies_are_the_fifteen_pairs():
    assert len(CURRENCIES) == 15
    assert len(set(CURRENCIES)) == 15
    assert "EUR" not in CURRENCIES


# --- parsing --------------------------------------------------------------------------------


def test_parse_fixture():
    df = _fixture_df()
    assert list(df.columns) == ["rate_date", "currency", "rate"]
    assert sorted(df["currency"].unique()) == ["JPY", "TRY", "USD"]
    assert (df["rate"] > 0).all()
    assert isinstance(df["rate_date"].iloc[0], date)
    assert df["rate"].dtype == "float64"
    wide = df.pivot(index="rate_date", columns="currency", values="rate")
    assert len(wide) == 10  # 2025-03-03 -> 2025-03-14 is ten TARGET business days
    assert (wide["JPY"] > wide["USD"]).all()


def test_parse_sorts_by_date_then_currency():
    df = _fixture_df()
    expected = df.sort_values(["rate_date", "currency"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(df, expected)


def test_parse_drops_empty_obs_value():
    lines = FIXTURE.read_text().splitlines()
    # Blank out the USD rate on 2025-03-05, as the ECB does when an observation is missing.
    text = "\n".join(
        line.replace(",2025-03-05,1.0694,A", ",2025-03-05,,A") if "USD" in line else line
        for line in lines
    )
    df = parse_ecb_csv(text)
    assert len(df) == 29
    assert not ((df["rate_date"] == date(2025, 3, 5)) & (df["currency"] == "USD")).any()


def test_parse_rejects_missing_columns():
    with pytest.raises(ValueError):
        parse_ecb_csv("A,B\n1,2\n")


def test_parse_rejects_non_positive_rate():
    text = f"{CSV_HEADER}\nEXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2025-03-03,0,A\n"
    with pytest.raises(ValueError, match="non-positive"):
        parse_ecb_csv(text)


# --- validation -----------------------------------------------------------------------------


def test_validate_complete_passes_on_fixture():
    validate_complete(_fixture_df(), ["USD", "JPY", "TRY"])


def test_validate_complete_raises_on_gap():
    df = _fixture_df()
    gap_day = date(2025, 3, 6)
    df = df[~((df["rate_date"] == gap_day) & (df["currency"] == "TRY"))]
    with pytest.raises(EcbDataGapError) as excinfo:
        validate_complete(df, ["USD", "JPY", "TRY"])
    assert "2025-03-06" in str(excinfo.value)
    assert "TRY" in str(excinfo.value)


def test_validate_complete_names_every_missing_currency_on_a_date():
    df = _fixture_df()
    gap_day = date(2025, 3, 6)
    df = df[~((df["rate_date"] == gap_day) & (df["currency"].isin(["TRY", "JPY"])))]
    with pytest.raises(EcbDataGapError) as excinfo:
        validate_complete(df, ["USD", "JPY", "TRY"])
    message = str(excinfo.value)
    assert "2025-03-06" in message
    assert "JPY" in message and "TRY" in message


def test_validate_complete_raises_when_a_currency_is_absent_entirely():
    df = _fixture_df()
    with pytest.raises(EcbDataGapError, match="GBP"):
        validate_complete(df, ["USD", "JPY", "TRY", "GBP"])


def test_validate_complete_raises_on_empty_frame():
    empty = parse_ecb_csv(f"{CSV_HEADER}\n")
    with pytest.raises(EcbDataGapError, match="no ECB rates"):
        validate_complete(empty, ["USD"])


def test_validate_complete_requires_a_fix_on_each_side_of_the_window():
    df = _fixture_df()  # 2025-03-03 -> 2025-03-14
    ccys = ["USD", "JPY", "TRY"]
    validate_complete(df, ccys, window=(date(2025, 3, 5), date(2025, 3, 12)))
    with pytest.raises(EcbDataGapError, match="on or before 2025-03-01"):
        validate_complete(df, ccys, window=(date(2025, 3, 1), date(2025, 3, 12)))
    with pytest.raises(EcbDataGapError, match="on or after 2025-03-20"):
        validate_complete(df, ccys, window=(date(2025, 3, 5), date(2025, 3, 20)))


# --- fetching -------------------------------------------------------------------------------


def test_fetch_rates_parses_a_200_response():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=FIXTURE.read_text())

    client = _client_returning(handler)
    df = fetch_rates(["USD", "JPY", "TRY"], date(2025, 3, 3), date(2025, 3, 14), client=client)
    assert len(df) == 30
    assert str(seen[0].url) == build_url(["USD", "JPY", "TRY"], date(2025, 3, 3), date(2025, 3, 14))


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503, text="Service Unavailable"),
        lambda request: httpx.Response(404, text="No results found"),
        lambda request: httpx.Response(200, text=""),
        lambda request: httpx.Response(200, text="<html>not csv</html>"),
    ],
    ids=["503", "404", "empty-200", "garbage-200"],
)
def test_fetch_rates_raises_ecb_fetch_error_on_bad_responses(handler):
    with pytest.raises(EcbFetchError):
        fetch_rates(["USD"], date(2025, 3, 3), date(2025, 3, 14), client=_client_returning(handler))


def test_fetch_rates_wraps_network_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(EcbFetchError, match="connection refused"):
        fetch_rates(["USD"], date(2025, 3, 3), date(2025, 3, 14), client=_client_returning(handler))


# --- snapshot I/O ---------------------------------------------------------------------------


def test_snapshot_roundtrip(tmp_path):
    df = _fixture_df()
    path = write_snapshot(df, tmp_path, date(2025, 3, 3), date(2025, 3, 14))
    assert path == tmp_path / "ecb_rates_2025-03-03_2025-03-14.csv"
    pd.testing.assert_frame_equal(read_snapshot(path), df)
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]  # no temp file left behind


def test_write_snapshot_creates_missing_directory(tmp_path):
    target = tmp_path / "nested" / "snapshot"
    path = write_snapshot(_fixture_df(), target, date(2025, 3, 3), date(2025, 3, 14))
    assert path.exists()


def test_write_snapshot_overwrites_same_window_atomically(tmp_path):
    first = write_snapshot(_fixture_df(), tmp_path, date(2025, 3, 3), date(2025, 3, 14))
    df = _fixture_df()
    df["rate"] = df["rate"] * 2
    second = write_snapshot(df, tmp_path, date(2025, 3, 3), date(2025, 3, 14))
    assert first == second
    pd.testing.assert_frame_equal(read_snapshot(second), df)
    assert len(list(tmp_path.iterdir())) == 1


def test_window_from_snapshot_parses_the_filename():
    path = Path("/anywhere/ecb_rates_2024-11-01_2026-10-31.csv")
    assert window_from_snapshot(path) == (date(2024, 11, 1), date(2026, 10, 31))


@pytest.mark.parametrize(
    "name",
    [
        "ecb_rates.csv",
        "ecb_rates_2024-11-01.csv",
        "ecb_rates_2024-11-01_2026-10-31.txt",
        "rates_2024-11-01_2026-10-31.csv",
        "ecb_rates_2024-13-01_2026-10-31.csv",
        "ecb_rates_2026-10-31_2024-11-01.csv",
    ],
)
def test_window_from_snapshot_rejects_bad_names(name):
    with pytest.raises(ValueError, match=name):
        window_from_snapshot(Path(name))


def test_latest_snapshot_picks_the_latest_window_end(tmp_path):
    for name in (
        "ecb_rates_2023-10-01_2025-09-30.csv",
        "ecb_rates_2024-11-01_2026-10-31.csv",
        "ecb_rates_2024-10-01_2026-09-30.csv",
        "notes.txt",
        "ecb_rates_scratch.csv",
    ):
        (tmp_path / name).write_text("x")
    assert latest_snapshot(tmp_path).name == "ecb_rates_2024-11-01_2026-10-31.csv"


def test_latest_snapshot_raises_when_none_exist(tmp_path):
    with pytest.raises(FileNotFoundError, match="make refresh-ecb"):
        latest_snapshot(tmp_path)
    with pytest.raises(FileNotFoundError):
        latest_snapshot(tmp_path / "does_not_exist")


# --- refresh and CLI ------------------------------------------------------------------------


def test_refresh_snapshot_requests_a_ten_day_buffer_and_names_the_file_by_window(tmp_path):
    requests: list[httpx.Request] = []
    buffered_start, buffered_end = date(2024, 10, 22), date(2026, 11, 10)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=_ecb_csv(buffered_start, buffered_end))

    path = ecb.refresh_snapshot(date(2026, 11, 12), tmp_path, client=_client_returning(handler))

    assert path.name == "ecb_rates_2024-11-01_2026-10-31.csv"
    assert requests[0].url.params["startPeriod"] == "2024-10-22"
    assert requests[0].url.params["endPeriod"] == "2026-11-10"
    df = read_snapshot(path)
    assert df["rate_date"].min() == buffered_start
    assert df["rate_date"].max() == buffered_end
    assert sorted(df["currency"].unique()) == sorted(CURRENCIES)
    assert window_from_snapshot(path) == (date(2024, 11, 1), date(2026, 10, 31))


def test_cli_fetch_ecb_writes_snapshot_and_reports_it(tmp_path, monkeypatch, capsys):
    buffered_start, buffered_end = date(2024, 10, 22), date(2026, 11, 10)
    monkeypatch.setattr(
        ecb,
        "make_client",
        lambda: _client_returning(
            lambda request: httpx.Response(200, text=_ecb_csv(buffered_start, buffered_end))
        ),
    )
    code = main(["fetch-ecb", "--build-date", "2026-11-12", "--snapshot-dir", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "ecb_rates_2024-11-01_2026-10-31.csv").exists()
    assert "ecb_rates_2024-11-01_2026-10-31.csv" in capsys.readouterr().out


def test_fetch_failure_leaves_snapshot_untouched(tmp_path, monkeypatch, capsys):
    snapshot = tmp_path / "ecb_rates_2024-11-01_2026-10-31.csv"
    original = b"rate_date,currency,rate\n2026-10-30,USD,1.16\n"
    snapshot.write_bytes(original)
    monkeypatch.setattr(
        ecb,
        "make_client",
        lambda: _client_returning(lambda request: httpx.Response(503, text="Service Unavailable")),
    )

    code = main(["fetch-ecb", "--build-date", "2026-11-12", "--snapshot-dir", str(tmp_path)])

    assert code != 0
    assert snapshot.read_bytes() == original
    assert [p.name for p in tmp_path.iterdir()] == [snapshot.name]
    err = capsys.readouterr().err.strip()
    assert "503" in err
    assert len(err.splitlines()) == 1


def test_cli_gap_in_downloaded_data_fails_loudly_and_writes_nothing(tmp_path, monkeypatch, capsys):
    buffered_start, buffered_end = date(2024, 10, 22), date(2026, 11, 10)
    gap = frozenset({(date(2025, 6, 4), "TRY")})
    monkeypatch.setattr(
        ecb,
        "make_client",
        lambda: _client_returning(
            lambda request: httpx.Response(
                200, text=_ecb_csv(buffered_start, buffered_end, skip=gap)
            )
        ),
    )

    code = main(["fetch-ecb", "--build-date", "2026-11-12", "--snapshot-dir", str(tmp_path)])

    assert code != 0
    assert list(tmp_path.iterdir()) == []
    err = capsys.readouterr().err
    assert "2025-06-04" in err and "TRY" in err


def test_cli_rejects_a_malformed_build_date(tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["fetch-ecb", "--build-date", "12/11/2026", "--snapshot-dir", str(tmp_path)])
    assert excinfo.value.code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err
