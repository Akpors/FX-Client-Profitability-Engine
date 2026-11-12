"""ECB euro reference rates: window, download, parse, validate and snapshot I/O.

The ECB publishes one reference rate per currency per TARGET working day, quoted as units of
quote currency per 1 EUR (series key ``D.{CCY}.EUR.SP00.A``, dataflow ``EXR``). The rates are
informational and not tradable. The rest of the pipeline reads a committed CSV snapshot instead
of calling the API, so a build is offline and repeatable. ``fetch-ecb`` is the only code that
talks to the network, and it never leaves a partial or unvalidated file behind.

Snapshot file name: ``ecb_rates_<window_start>_<window_end>.csv``. The name carries the 24-month
*window* the trade book covers. The contents cover a wider range (window start - 10 days to
window end + 10 days) so every window day has a fix on each side.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

CURRENCIES: tuple[str, ...] = (
    "USD",
    "GBP",
    "JPY",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "AUD",
    "CAD",
    "PLN",
    "CZK",
    "HUF",
    "ZAR",
    "TRY",
    "MXN",
)

BASE_URL = "https://data-api.ecb.europa.eu/service/data/EXR/"
WINDOW_MONTHS = 24
BUFFER_DAYS = 10
TIMEOUT_SECONDS = 30.0

# <repo>/pipeline/data/ecb_snapshot; /app/data/ecb_snapshot inside the container.
DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "ecb_snapshot"

_SNAPSHOT_NAME = re.compile(r"^ecb_rates_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.csv$")
_SNAPSHOT_COLUMNS = ["rate_date", "currency", "rate"]


class EcbError(Exception):
    """Base class for ECB ingestion failures."""


class EcbFetchError(EcbError):
    """The ECB API could not be reached or returned something unusable."""


class EcbDataGapError(EcbError):
    """The downloaded rates have a hole the pipeline must not paper over."""


# --- window and URL -------------------------------------------------------------------------


def window_for(build_date: date) -> tuple[date, date]:
    """First and last day of the 24 complete calendar months before ``build_date``."""
    end = date(build_date.year, build_date.month, 1) - timedelta(days=1)
    start_index = build_date.year * 12 + (build_date.month - 1) - WINDOW_MONTHS
    return date(start_index // 12, start_index % 12 + 1, 1), end


def build_url(currencies: Sequence[str], start: date, end: date) -> str:
    keys = "+".join(currencies)
    return (
        f"{BASE_URL}D.{keys}.EUR.SP00.A"
        f"?startPeriod={start.isoformat()}&endPeriod={end.isoformat()}&format=csvdata"
    )


# --- parsing and validation -----------------------------------------------------------------


def _tidy(dates: pd.Series, currencies: pd.Series, rates: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "rate_date": pd.to_datetime(dates, format="%Y-%m-%d").dt.date,
            "currency": currencies.astype("str"),
            "rate": rates.astype("float64"),
        }
    )
    return frame.sort_values(["rate_date", "currency"]).reset_index(drop=True)


def parse_ecb_csv(text: str) -> pd.DataFrame:
    """Parse an ECB ``csvdata`` response into ``rate_date, currency, rate``.

    Rows with an empty ``OBS_VALUE`` are dropped. Raises ``ValueError`` on a response that is not
    ECB daily csvdata (missing columns, non-ISO dates, non-numeric or non-positive rates).
    """
    raw = pd.read_csv(
        io.StringIO(text),
        usecols=["TIME_PERIOD", "CURRENCY", "OBS_VALUE"],
        dtype={"TIME_PERIOD": "str", "CURRENCY": "str"},
    )
    raw = raw.assign(OBS_VALUE=pd.to_numeric(raw["OBS_VALUE"])).dropna(subset=["OBS_VALUE"])
    if (raw["OBS_VALUE"] <= 0).any():
        raise ValueError("ECB response contains a non-positive rate")
    return _tidy(raw["TIME_PERIOD"], raw["CURRENCY"], raw["OBS_VALUE"])


def validate_complete(
    df: pd.DataFrame,
    currencies: Sequence[str],
    window: tuple[date, date] | None = None,
) -> None:
    """Fail loudly if any requested currency is missing on a date that others have.

    A date with a rate for some, but not all, of ``currencies`` is a gap. Dates with no rate at
    all are TARGET holidays and weekends and are legitimately absent. If ``window`` is given, the
    data must also reach a fix on or before its start and on or after its end.
    """
    if df.empty:
        raise EcbDataGapError("ECB data gap: no ECB rates were returned")

    wanted = set(currencies)
    present: dict[date, set[str]] = {}
    for rate_date, currency in zip(df["rate_date"], df["currency"], strict=True):
        present.setdefault(rate_date, set()).add(currency)

    gaps = {
        rate_date: sorted(wanted - have)
        for rate_date, have in sorted(present.items())
        if wanted - have
    }
    if gaps:
        shown = "; ".join(f"{d.isoformat()}: {', '.join(m)}" for d, m in list(gaps.items())[:5])
        more = f" (+{len(gaps) - 5} more)" if len(gaps) > 5 else ""
        raise EcbDataGapError(
            f"ECB data gap: rate missing on {len(gaps)} date(s), {shown}{more}"
        )

    if window is not None:
        start, end = window
        first, last = min(present), max(present)
        if first > start:
            raise EcbDataGapError(
                f"ECB data gap: no fix on or before {start.isoformat()} "
                f"(earliest fix is {first.isoformat()})"
            )
        if last < end:
            raise EcbDataGapError(
                f"ECB data gap: no fix on or after {end.isoformat()} "
                f"(latest fix is {last.isoformat()})"
            )


# --- download -------------------------------------------------------------------------------


def make_client() -> httpx.Client:
    """The HTTP client used for ECB calls. A module-level factory so tests can swap it."""
    return httpx.Client()


def fetch_rates(
    currencies: Sequence[str],
    start: date,
    end: date,
    client: httpx.Client | None = None,
) -> pd.DataFrame:
    """Download and parse ECB rates for ``start``..``end`` (inclusive).

    Raises ``EcbFetchError`` on network errors, any non-200 status, or an unparseable body.
    """
    url = build_url(currencies, start, end)
    owns_client = client is None
    client = client if client is not None else make_client()
    try:
        try:
            response = client.get(url, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise EcbFetchError(
                f"could not reach the ECB data API ({type(exc).__name__}: {exc})"
            ) from exc
    finally:
        if owns_client:
            client.close()

    if response.status_code != 200:
        raise EcbFetchError(f"ECB data API returned HTTP {response.status_code} for {url}")
    try:
        return parse_ecb_csv(response.text)
    except ValueError as exc:
        raise EcbFetchError(f"ECB data API response could not be parsed ({exc})") from exc


# --- snapshot I/O ---------------------------------------------------------------------------


def snapshot_name(start: date, end: date) -> str:
    return f"ecb_rates_{start.isoformat()}_{end.isoformat()}.csv"


def window_from_snapshot(path: Path) -> tuple[date, date]:
    """The 24-month window a snapshot covers, parsed from its file name.

    ``run-all`` derives the window from here and never from today's date, so the same config and
    snapshot always give the same output. Raises ``ValueError`` on a non-matching name.
    """
    match = _SNAPSHOT_NAME.match(Path(path).name)
    if match is None:
        raise ValueError(
            f"{Path(path).name!r} is not an ECB snapshot name (ecb_rates_<start>_<end>.csv)"
        )
    try:
        start, end = (date.fromisoformat(part) for part in match.groups())
    except ValueError as exc:
        raise ValueError(f"{Path(path).name!r} has an invalid date ({exc})") from exc
    if start > end:
        raise ValueError(f"{Path(path).name!r} has a window that ends before it starts")
    return start, end


def write_snapshot(df: pd.DataFrame, directory: Path, start: date, end: date) -> Path:
    """Write ``df`` to ``ecb_rates_<start>_<end>.csv`` atomically (temp file, then rename)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / snapshot_name(start, end)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".ecb_rates_", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", newline="") as handle:
            df[_SNAPSHOT_COLUMNS].to_csv(handle, index=False, lineterminator="\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(0o644)  # mkstemp creates 0600; the file is committed and read from the host
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def read_snapshot(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype={"rate_date": "str", "currency": "str"})
    return _tidy(raw["rate_date"], raw["currency"], raw["rate"])


def latest_snapshot(directory: Path) -> Path:
    """The snapshot with the latest window end. Files that are not snapshots are ignored."""
    directory = Path(directory)
    candidates: list[tuple[date, date, Path]] = []
    if directory.is_dir():
        for path in directory.iterdir():
            try:
                start, end = window_from_snapshot(path)
            except ValueError:
                continue
            if path.is_file():
                candidates.append((end, start, path))
    if not candidates:
        raise FileNotFoundError(
            f"no ECB snapshot (ecb_rates_<start>_<end>.csv) found in {directory}; "
            "run `make refresh-ecb` to create one"
        )
    return max(candidates)[2]


def refresh_snapshot(
    build_date: date, directory: Path, client: httpx.Client | None = None
) -> Path:
    """Download the window for ``build_date`` plus a 10-day buffer, validate, then write.

    Nothing is written unless the download succeeds and validates. The buffer gives the intraday
    mid model a fix on each side of the window.
    """
    start, end = window_for(build_date)
    buffer = timedelta(days=BUFFER_DAYS)
    df = fetch_rates(CURRENCIES, start - buffer, end + buffer, client=client)
    validate_complete(df, CURRENCIES, window=(start, end))
    return write_snapshot(df, directory, start, end)
