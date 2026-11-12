"""Integration tests for the raw-schema load. They need the Postgres ``db`` service.

Run with ``make test-integration``. Every test writes to a dedicated ``raw_test`` schema, never to
``raw``: setup and teardown drop ``raw_test`` only, so the book ``make pipeline`` loaded into
``raw`` and the dbt views built on it are left exactly as they were.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import psycopg
import pytest
import sqlalchemy as sa

from fxengine.load import RAW_SCHEMA, RAW_TABLES, engine_from_env, write_raw

pytestmark = pytest.mark.integration

SCHEMA = "raw_test"
TEST_VIEW = f"{SCHEMA}.tmp_view_on_trades"


@pytest.fixture(scope="module")
def engine():
    engine = engine_from_env()
    yield engine
    engine.dispose()


def _drop_test_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


@pytest.fixture(autouse=True)
def clean_test_schema(engine):
    """Start and finish every test with no ``raw_test`` schema; ``raw`` is never touched."""
    _drop_test_schema(engine)
    yield
    _drop_test_schema(engine)


def _relations(engine, schema: str) -> dict[str, int | None]:
    """Every table and view in ``schema``, with the row count of each table (views: None)."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = :schema ORDER BY table_name"
            ),
            {"schema": schema},
        ).all()
        return {
            name: (
                conn.execute(sa.text(f'SELECT count(*) FROM "{schema}"."{name}"')).scalar_one()
                if kind == "BASE TABLE"
                else None
            )
            for name, kind in rows
        }


def _scalar(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql)).scalar_one()


def test_write_raw_creates_tables_with_counts(engine, small_raw_tables):
    counts = write_raw(engine, small_raw_tables, schema=SCHEMA)

    assert set(counts) == set(RAW_TABLES) == set(small_raw_tables)
    assert counts == {name: len(frame) for name, frame in small_raw_tables.items()}
    assert counts["trades"] > 500
    for name, expected in counts.items():
        assert _scalar(engine, f"SELECT count(*) FROM {SCHEMA}.{name}") == expected

    with engine.connect() as conn:
        types = {
            (r.table_name, r.column_name): (r.data_type, r.numeric_precision, r.numeric_scale)
            for r in conn.execute(
                sa.text(
                    "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale "
                    "FROM information_schema.columns WHERE table_schema = :schema"
                ),
                {"schema": SCHEMA},
            )
        }

    # pair_weights (a dict per client) stays out of the raw table.
    client_columns = {column for table, column in types if table == "clients"}
    assert "pair_weights" not in client_columns
    assert "negotiation_factor" in client_columns

    # Explicit column types, not whatever the frames would infer.
    for column in ("client_rate", "cover_rate", "mid_rate", "benchmark_rate"):
        assert types["trades", column] == ("numeric", 18, 8)
    assert types["ecb_rates", "rate"] == ("numeric", 18, 8)
    assert types["trades", "notional_base"] == ("numeric", 20, 2)
    assert types["trades", "trade_ts"][0] == "timestamp with time zone"
    assert types["trades", "trade_date"][0] == "date"
    assert types["trades", "markup_bps_applied"][0] == "double precision"
    assert types["pairs", "cls_settled"][0] == "boolean"
    assert types["ecb_rates", "currency"][0] == "text"

    # NaN is NULL: the top ticket band has no upper limit.
    open_band = f"SELECT count(*) FROM {SCHEMA}.ticket_bands WHERE max_ticket_eur IS NULL"
    assert _scalar(engine, open_band) == 1


def test_load_is_idempotent(engine, small_raw_tables):
    expected = len(small_raw_tables["trades"])
    for _ in range(2):
        write_raw(engine, small_raw_tables, schema=SCHEMA)
        assert _scalar(engine, f"SELECT count(*) FROM {SCHEMA}.trades") == expected
        assert _scalar(engine, f"SELECT count(*) FROM {SCHEMA}.clients") == len(
            small_raw_tables["clients"]
        )


def test_numeric_precision_preserved(engine, small_raw_tables):
    # A JPY rate to 3 dp and a USD rate to 5 dp, which float arithmetic must not disturb.
    trades = small_raw_tables["trades"].head(2).copy()
    trades["trade_id"] = ["T9000001", "T9000002"]
    trades["currency_pair"] = ["EURJPY", "EURUSD"]
    for column, values in {
        "client_rate": [163.412, 1.08342],
        "cover_rate": [163.4, 1.08301],
        "mid_rate": [163.405, 1.08317],
        "benchmark_rate": [163.41, 1.0832],
    }.items():
        trades[column] = values
    trades["notional_base"] = [1_250_000, 80_000]

    write_raw(engine, {"trades": trades}, schema=SCHEMA)

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT client_rate, cover_rate, mid_rate, benchmark_rate, notional_base "
                f"FROM {SCHEMA}.trades ORDER BY trade_id"
            )
        ).all()
    assert all(isinstance(value, Decimal) for row in rows for value in row)
    assert [tuple(str(v) for v in row[:4]) for row in rows] == [
        ("163.41200000", "163.40000000", "163.40500000", "163.41000000"),
        ("1.08342000", "1.08301000", "1.08317000", "1.08320000"),
    ]
    assert rows[0][0] == Decimal("163.412")
    assert rows[1][0] == Decimal("1.08342")
    assert [row[4] for row in rows] == [Decimal("1250000.00"), Decimal("80000.00")]


def test_trade_timestamps_round_trip_in_utc(engine, small_raw_tables):
    write_raw(engine, small_raw_tables, schema=SCHEMA)
    trades = small_raw_tables["trades"]
    with engine.connect() as conn:
        stored = pd.read_sql(
            sa.text(f"SELECT trade_id, trade_ts FROM {SCHEMA}.trades ORDER BY trade_id"), conn
        )
    assert (stored["trade_id"].to_numpy() == trades["trade_id"].to_numpy()).all()
    assert (
        stored["trade_ts"].dt.tz_convert("UTC").to_numpy()
        == trades["trade_ts"].dt.tz_convert("UTC").to_numpy()
    ).all()


def test_failed_load_rolls_back_and_keeps_previous_tables(engine, small_raw_tables):
    write_raw(engine, small_raw_tables, schema=SCHEMA)
    before = _scalar(engine, f"SELECT count(*) FROM {SCHEMA}.trades")

    # The first four tables load; the poisoned trades frame fails part-way through COPY because
    # one rate overflows numeric(18,8). Nothing of the failed load may remain.
    poisoned = small_raw_tables["trades"].head(10).copy()
    poisoned.loc[poisoned.index[-1], "client_rate"] = 1e15
    with pytest.raises(psycopg.Error):
        write_raw(
            engine,
            {"clients": small_raw_tables["clients"].head(3), "trades": poisoned},
            schema=SCHEMA,
        )

    assert _scalar(engine, f"SELECT count(*) FROM {SCHEMA}.trades") == before
    clients = _scalar(engine, f"SELECT count(*) FROM {SCHEMA}.clients")
    assert clients == len(small_raw_tables["clients"])


def test_reload_replaces_tables_that_views_depend_on(engine, small_raw_tables):
    """dbt staging views select from raw.*, so a re-run must still be able to replace them."""
    write_raw(engine, small_raw_tables, schema=SCHEMA)
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE VIEW {TEST_VIEW} AS SELECT * FROM {SCHEMA}.trades"))

    counts = write_raw(engine, small_raw_tables, schema=SCHEMA)

    assert counts["trades"] == len(small_raw_tables["trades"])
    assert _scalar(engine, f"SELECT to_regclass('{TEST_VIEW}') IS NULL")


def test_writing_the_test_schema_leaves_raw_and_its_views_alone(engine, small_raw_tables):
    """The raw book from ``make pipeline`` and dbt's staging views on it survive the tests."""
    raw_before = _relations(engine, RAW_SCHEMA)
    staging_before = _relations(engine, "staging")

    write_raw(engine, small_raw_tables, schema=SCHEMA)
    write_raw(engine, small_raw_tables, schema=SCHEMA)  # a reload cascades inside raw_test only
    _drop_test_schema(engine)

    assert _relations(engine, RAW_SCHEMA) == raw_before
    assert _relations(engine, "staging") == staging_before
