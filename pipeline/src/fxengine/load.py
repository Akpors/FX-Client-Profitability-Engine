"""Load the generated tables into the Postgres ``raw`` schema.

``assemble_raw_tables`` runs the generators in their fixed order and returns the seven raw
tables. ``write_raw`` replaces those tables in one transaction, so a re-run replaces the data
rather than adding to it, and a failed run leaves the previous tables as they were.

The column types of every table are declared below, not inferred from the frames. Rates are
``numeric(18,8)`` and notional is ``numeric(20,2)``, so no binary float ends up in a column a
price comparison or a sum depends on; the frames hold the rates as ``float64`` rounded to the
pair's quoting precision, and Postgres turns the shortest round-trip text of each value into an
exact decimal. Other measurements (markup in bps, multipliers, weights, costs) are
``double precision``.

Raw tables are loaded as they come: no primary keys and no business rules. The only constraint
is ``NOT NULL`` on every column that must not be empty, so a NaN that slips into a frame fails the
load instead of becoming a NULL that dbt discovers later. The one nullable column is the
open-ended top ticket band's ``max_ticket_eur``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date

import numpy as np
import pandas as pd
import sqlalchemy as sa
from psycopg import sql

from fxengine.clients import generate_clients
from fxengine.config import GeneratorConfig
from fxengine.reference import (
    cost_assumptions_table,
    pairs_table,
    parameters_table,
    ticket_bands_table,
)
from fxengine.trades import generate_trades

RAW_SCHEMA = "raw"

_RATE = "numeric(18,8)"
_NOTIONAL = "numeric(20,2)"
_FLOAT = "double precision"

# Column name -> SQL type, in table column order. This is the contract with dbt's sources.
RAW_COLUMNS: dict[str, dict[str, str]] = {
    "ecb_rates": {
        "rate_date": "date",
        "currency": "text",
        "rate": _RATE,
    },
    "clients": {
        "client_id": "text",
        "client_name": "text",
        "segment": "text",
        "home_country": "text",
        "onboarded_date": "date",
        "legacy_deal": "boolean",
        "trades_per_month": _FLOAT,
        "ticket_mu": _FLOAT,
        "ticket_sigma": _FLOAT,
        "expected_annual_volume_eur": _FLOAT,
        "volume_tier_discount_pct": _FLOAT,
        "negotiation_factor": _FLOAT,
    },
    "trades": {
        "trade_id": "text",
        "client_id": "text",
        "trade_ts": "timestamptz",
        "trade_date": "date",
        "currency_pair": "text",
        "client_side": "text",
        "notional_base": _NOTIONAL,
        "client_rate": _RATE,
        "cover_rate": _RATE,
        "mid_rate": _RATE,
        "benchmark_rate": _RATE,
        "markup_bps_applied": _FLOAT,
    },
    "pairs": {
        "currency_pair": "text",
        "base_ccy": "text",
        "quote_ccy": "text",
        "liquidity_tier": "text",
        "price_decimals": "integer",
        "cls_settled": "boolean",
        "settlement_weight": _FLOAT,
    },
    "ticket_bands": {
        "label": "text",
        "min_ticket_eur": _FLOAT,
        "max_ticket_eur": _FLOAT,
        "markup_multiplier": _FLOAT,
    },
    "cost_assumptions": {
        "segment": "text",
        "ops_cost_per_ticket_eur": _FLOAT,
        "coverage_cost_eur_per_month": _FLOAT,
        "credit_weight": _FLOAT,
    },
    "parameters": {
        "name": "text",
        "value": _FLOAT,
        "description": "text",
    },
}
RAW_TABLES: tuple[str, ...] = tuple(RAW_COLUMNS)

# The top ticket band has no upper limit: NaN in the frame, NULL in the table.
NULLABLE_COLUMNS: frozenset[tuple[str, str]] = frozenset({("ticket_bands", "max_ticket_eur")})


class LoadError(Exception):
    """The raw load cannot run: bad connection settings or frames that do not match the schema."""


# --- assembling the tables --------------------------------------------------------------------


def assemble_raw_tables(
    cfg: GeneratorConfig, ecb: pd.DataFrame, window: tuple[date, date]
) -> dict[str, pd.DataFrame]:
    """Generate the client book and trades, and build the reference tables, for ``cfg``.

    The generator is seeded from ``cfg.seed`` and used in a fixed order (clients, then trades), so
    the same config, snapshot and window give identical tables. ``ecb`` goes in whole, including
    the buffer days either side of ``window``. ``pair_weights`` (a dict per client) is generator
    state, not data, and stays out of ``raw.clients``.
    """
    rng = np.random.default_rng(cfg.seed)
    clients = generate_clients(cfg, window, rng)
    trades = generate_trades(cfg, clients, ecb, window, rng)
    return {
        "ecb_rates": ecb,
        "clients": clients.drop(columns=["pair_weights"]),
        "trades": trades,
        "pairs": pairs_table(cfg),
        "ticket_bands": ticket_bands_table(cfg),
        "cost_assumptions": cost_assumptions_table(cfg),
        "parameters": parameters_table(cfg),
    }


# --- connection -------------------------------------------------------------------------------


def engine_from_env(environ: Mapping[str, str] | None = None) -> sa.Engine:
    """A SQLAlchemy engine from ``FX_DB_*`` (host defaults to ``db``, port to 5432)."""
    env = os.environ if environ is None else environ
    missing = [name for name in ("FX_DB_NAME", "FX_DB_USER", "FX_DB_PASSWORD") if not env.get(name)]
    if missing:
        raise LoadError(f"database settings missing from the environment: {', '.join(missing)}")
    try:
        port = int(env.get("FX_DB_PORT") or 5432)
    except ValueError:
        raise LoadError(f"FX_DB_PORT must be a number, not {env['FX_DB_PORT']!r}") from None
    url = sa.URL.create(
        "postgresql+psycopg",
        username=env["FX_DB_USER"],
        password=env["FX_DB_PASSWORD"],
        host=env.get("FX_DB_HOST") or "db",
        port=port,
        database=env["FX_DB_NAME"],
    )
    return sa.create_engine(url)


# --- validation -------------------------------------------------------------------------------


def _dtype_problem(sql_type: str, series: pd.Series) -> str | None:
    """Why ``series`` cannot be loaded into a ``sql_type`` column, or None if it can."""
    dtype = series.dtype
    if sql_type == "timestamptz":
        if not isinstance(dtype, pd.DatetimeTZDtype):
            return "must be timezone-aware (a naive timestamp would be read as local time)"
    elif sql_type == "boolean":
        if dtype.kind != "b":
            return "must be bool"
    elif sql_type in ("integer", "bigint"):
        if dtype.kind not in "iu":
            return "must be integers"
    elif sql_type == "text":
        if not (pd.api.types.is_string_dtype(dtype) or dtype.kind == "O"):
            return "must be strings"
    elif sql_type == "date":
        if dtype.kind != "O":
            return "must be datetime.date values"
    elif dtype.kind not in "iuf":  # numeric(p,s), double precision
        return "must be numbers"
    return None


def _check_frame(name: str, frame: pd.DataFrame) -> None:
    """Raise ``LoadError`` unless ``frame`` has exactly the declared columns and dtypes."""
    declared = RAW_COLUMNS[name]
    columns = list(frame.columns)
    missing = [column for column in declared if column not in columns]
    unexpected = [column for column in columns if column not in declared]
    if missing or unexpected:
        parts = []
        if missing:
            parts.append(f"missing columns: {', '.join(missing)}")
        if unexpected:
            parts.append(f"unexpected columns: {', '.join(map(str, unexpected))}")
        raise LoadError(f"raw.{name} does not match its declared schema ({'; '.join(parts)})")
    for column, sql_type in declared.items():
        problem = _dtype_problem(sql_type, frame[column])
        if problem:
            raise LoadError(f"raw.{name}.{column} is {frame[column].dtype} but {problem}")


def _python_values(series: pd.Series) -> list:
    """The column as plain Python objects for psycopg, with missing values as None."""
    values = series.tolist()
    if series.hasnans:
        return [None if pd.isna(value) else value for value in values]
    return values


# --- writing ----------------------------------------------------------------------------------


def _create_table(name: str) -> sql.Composed:
    columns = [
        sql.SQL("{} {}{}").format(
            sql.Identifier(column),
            sql.SQL(sql_type),
            sql.SQL("" if (name, column) in NULLABLE_COLUMNS else " NOT NULL"),
        )
        for column, sql_type in RAW_COLUMNS[name].items()
    ]
    return sql.SQL("CREATE TABLE {} ({})").format(
        sql.Identifier(RAW_SCHEMA, name), sql.SQL(", ").join(columns)
    )


def write_raw(engine: sa.Engine, tables: Mapping[str, pd.DataFrame]) -> dict[str, int]:
    """Replace the given ``raw`` tables with ``tables`` and return the rows loaded per table.

    Everything runs in one transaction: ``CREATE SCHEMA IF NOT EXISTS raw``, then for each table
    ``DROP TABLE IF EXISTS``, ``CREATE TABLE`` with the declared types and a ``COPY`` of the rows.
    On any error the transaction rolls back and the previous tables are untouched. The frames are
    checked against the declared schema before the database is contacted.

    The drop cascades to views that select from the table (dbt staging models, when they are
    views), which a re-run of ``make pipeline`` rebuilds with ``dbt build``. Without the cascade
    the second run would fail on its first dependent view.
    """
    unknown = [name for name in tables if name not in RAW_COLUMNS]
    if unknown:
        raise LoadError(
            f"unknown raw tables: {', '.join(unknown)} (known: {', '.join(RAW_TABLES)})"
        )
    for name, frame in tables.items():
        _check_frame(name, frame)

    counts: dict[str, int] = {}
    # The raw DBAPI connection gives access to psycopg's COPY; SQLAlchemy only pools it.
    connection = engine.raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(RAW_SCHEMA))
            )
            for name, frame in tables.items():
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        sql.Identifier(RAW_SCHEMA, name)
                    )
                )
                cursor.execute(_create_table(name))
                columns = list(RAW_COLUMNS[name])
                copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
                    sql.Identifier(RAW_SCHEMA, name),
                    sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                )
                with cursor.copy(copy_sql) as copy:
                    for row in zip(*(_python_values(frame[c]) for c in columns), strict=True):
                        copy.write_row(row)
                if cursor.rowcount != len(frame):
                    raise LoadError(
                        f"raw.{name}: {len(frame)} rows sent, {cursor.rowcount} loaded"
                    )
                counts[name] = cursor.rowcount
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return counts
