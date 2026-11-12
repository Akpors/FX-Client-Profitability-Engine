"""Unit tests for table assembly, schema checks and the ``run-all`` command. No database needed."""

from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest
import yaml

from fxengine import cli
from fxengine.config import GeneratorConfig
from fxengine.load import (
    RAW_COLUMNS,
    RAW_SCHEMA,
    RAW_TABLES,
    LoadError,
    assemble_raw_tables,
    write_raw,
)


class NoDatabase:
    """Stands in for an engine: any attempt to connect is a failure of the test."""

    def raw_connection(self):
        raise AssertionError("write_raw must validate the frames before it connects")


# --- table assembly ---------------------------------------------------------------------------


def test_assemble_returns_the_seven_raw_tables(small_raw_tables, ecb):
    assert list(small_raw_tables) == list(RAW_TABLES) == list(RAW_COLUMNS)
    assert set(RAW_TABLES) == {
        "ecb_rates",
        "clients",
        "trades",
        "pairs",
        "ticket_bands",
        "cost_assumptions",
        "parameters",
    }
    for name, frame in small_raw_tables.items():
        assert list(frame.columns) == list(RAW_COLUMNS[name]), name
    assert "pair_weights" not in small_raw_tables["clients"].columns
    # The whole snapshot goes in, buffer days included.
    assert len(small_raw_tables["ecb_rates"]) == len(ecb)
    assert len(small_raw_tables["clients"]) == 20
    assert len(small_raw_tables["pairs"]) == 15


def test_assemble_is_reproducible(small_cfg, ecb, small_window, small_raw_tables):
    again = assemble_raw_tables(small_cfg, ecb, small_window)
    for name, frame in small_raw_tables.items():
        pd.testing.assert_frame_equal(frame, again[name], check_exact=True)


# --- schema checks ----------------------------------------------------------------------------


def test_write_raw_writes_the_raw_schema_unless_told_otherwise():
    assert RAW_SCHEMA == "raw"
    assert inspect.signature(write_raw).parameters["schema"].default == RAW_SCHEMA


def test_write_raw_rejects_extra_columns(small_raw_tables):
    clients = small_raw_tables["clients"].assign(pair_weights=[{}] * 20)
    with pytest.raises(LoadError, match=r"clients.*unexpected.*pair_weights"):
        write_raw(NoDatabase(), {"clients": clients})


def test_write_raw_rejects_missing_columns(small_raw_tables):
    trades = small_raw_tables["trades"].drop(columns=["cover_rate"])
    with pytest.raises(LoadError, match=r"trades.*missing.*cover_rate"):
        write_raw(NoDatabase(), {"trades": trades})


def test_write_raw_rejects_unknown_tables(small_raw_tables):
    with pytest.raises(LoadError, match=r"unknown.*widgets"):
        write_raw(NoDatabase(), {"widgets": small_raw_tables["pairs"]})


def test_write_raw_rejects_naive_timestamps(small_raw_tables):
    trades = small_raw_tables["trades"].copy()
    trades["trade_ts"] = trades["trade_ts"].dt.tz_localize(None)
    with pytest.raises(LoadError, match=r"trades\.trade_ts.*timezone"):
        write_raw(NoDatabase(), {"trades": trades})


def test_write_raw_rejects_wrong_dtype(small_raw_tables):
    pairs = small_raw_tables["pairs"].assign(cls_settled="yes")
    with pytest.raises(LoadError, match=r"pairs\.cls_settled.*must be bool"):
        write_raw(NoDatabase(), {"pairs": pairs})


# --- the command ------------------------------------------------------------------------------


@pytest.fixture
def small_config_path(small_cfg: GeneratorConfig, tmp_path: Path) -> Path:
    path = tmp_path / "small.yaml"
    path.write_text(yaml.safe_dump(small_cfg.model_dump(mode="json")))
    return path


def run_all(config: Path, snapshot: Path, *flags: str) -> int:
    return cli.main(["run-all", *flags, "--config", str(config), "--snapshot", str(snapshot)])


def test_run_all_dry_run_prints_counts_and_writes_nothing(
    small_config_path, snapshot_path, monkeypatch, capsys
):
    def fail(*args, **kwargs):
        raise AssertionError("--dry-run must not touch the database")

    monkeypatch.setattr(cli, "write_raw", fail)
    monkeypatch.setattr(cli, "engine_from_env", fail)

    code = run_all(small_config_path, snapshot_path, "--dry-run")

    out = capsys.readouterr().out
    assert code == 0
    for name in RAW_TABLES:
        assert f"raw.{name}" in out
    assert "2024-11-01 to 2026-10-31" in out  # window comes from the snapshot name
    assert "dry run" in out


def test_run_all_writes_through_write_raw(small_config_path, snapshot_path, monkeypatch, capsys):
    seen = {}

    def fake_write(engine, tables):
        seen.update(tables)
        return {name: len(frame) for name, frame in tables.items()}

    monkeypatch.setattr(cli, "write_raw", fake_write)
    monkeypatch.setattr(cli, "engine_from_env", lambda: object())

    code = run_all(small_config_path, snapshot_path)

    assert code == 0
    assert list(seen) == list(RAW_TABLES)
    assert "dry run" not in capsys.readouterr().out


def test_run_all_rejects_a_snapshot_with_no_window_in_its_name(tmp_path, small_config_path, capsys):
    bad = tmp_path / "rates.csv"
    bad.write_text("rate_date,currency,rate\n")

    assert run_all(small_config_path, bad, "--dry-run") == 1
    assert "rates.csv" in capsys.readouterr().err


def test_run_all_reports_a_missing_config(tmp_path, snapshot_path, capsys):
    assert run_all(tmp_path / "nope.yaml", snapshot_path, "--dry-run") == 1
    assert "nope.yaml" in capsys.readouterr().err


def test_run_all_reports_malformed_yaml_in_one_line(tmp_path, snapshot_path, capsys):
    broken = tmp_path / "broken.yaml"
    broken.write_text("seed: 42\nsegments: [unclosed\n")

    assert run_all(broken, snapshot_path, "--dry-run") == 1
    err = capsys.readouterr().err
    assert err.startswith("fxengine run-all: error:")
    assert err.count("\n") == 1, err  # one line, no traceback


def test_run_all_reports_an_invalid_config_in_one_line(tmp_path, snapshot_path, capsys):
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("seed: 42\nn_clients: -5\n")

    assert run_all(invalid, snapshot_path, "--dry-run") == 1
    err = capsys.readouterr().err
    assert err.startswith("fxengine run-all: error:")
    assert err.count("\n") == 1, err


@pytest.mark.parametrize("command", ["generate", "load"])
def test_retired_stub_commands_are_gone(command, capsys):
    # run-all generates and loads in one go; the old placeholders must not pretend to work.
    with pytest.raises(SystemExit) as excinfo:
        cli.main([command])
    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
