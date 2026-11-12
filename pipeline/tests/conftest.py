"""Shared fixtures: the committed ECB snapshot and a small, fast configuration.

The small configuration is the shipped ``generator.yaml`` with 20 clients instead of 200 and a
two-month window (March and April 2025) inside the committed snapshot. Everything else (pairs,
segments, pricing, tiers) is the real default, so a test on the small book exercises the same
pricing rules as the full run in a fraction of the time. Full-size tests are marked
``@pytest.mark.slow`` and run with ``make test-slow``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from fxengine.config import DEFAULT_CONFIG_PATH, GeneratorConfig, load_config
from fxengine.ecb import DEFAULT_SNAPSHOT_DIR, read_snapshot, window_from_snapshot

# The committed snapshot, named explicitly so a later `make refresh-ecb` cannot move the tests.
COMMITTED_SNAPSHOT = DEFAULT_SNAPSHOT_DIR / "ecb_rates_2024-11-01_2026-10-31.csv"
SMALL_WINDOW = (date(2025, 3, 1), date(2025, 4, 30))
SMALL_N_CLIENTS = 20


@pytest.fixture(scope="session")
def snapshot_path() -> Path:
    return COMMITTED_SNAPSHOT


@pytest.fixture(scope="session")
def ecb(snapshot_path: Path) -> pd.DataFrame:
    return read_snapshot(snapshot_path)


@pytest.fixture(scope="session")
def full_cfg() -> GeneratorConfig:
    return load_config(DEFAULT_CONFIG_PATH)


@pytest.fixture(scope="session")
def full_window(snapshot_path: Path) -> tuple[date, date]:
    return window_from_snapshot(snapshot_path)


@pytest.fixture(scope="session")
def small_cfg(full_cfg: GeneratorConfig) -> GeneratorConfig:
    """The default config scaled to 20 clients over 2 of the 24 months."""
    scale = SMALL_N_CLIENTS / full_cfg.n_clients * 2 / 24
    return full_cfg.model_copy(
        update={
            "n_clients": SMALL_N_CLIENTS,
            "target_trades": max(1, round(full_cfg.target_trades * scale)),
        }
    )


@pytest.fixture(scope="session")
def small_window() -> tuple[date, date]:
    return SMALL_WINDOW


@pytest.fixture(scope="session")
def small_raw_tables(
    small_cfg: GeneratorConfig, ecb: pd.DataFrame, small_window: tuple[date, date]
) -> dict[str, pd.DataFrame]:
    """The seven raw tables for the small book, as ``run-all`` would assemble them."""
    from fxengine.load import assemble_raw_tables

    return assemble_raw_tables(small_cfg, ecb, small_window)
