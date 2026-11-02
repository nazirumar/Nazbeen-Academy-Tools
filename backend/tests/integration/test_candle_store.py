"""Integration tests: candle persistence (real PostgreSQL).

Verifies the store's transactional upsert, duplicate protection, read
policies and freshness logic against a real database.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models.candle import CandleRow
from app.db.session import get_session_factory
from app.domains.market_data.store import (
    is_series_fresh,
    load_candles,
    series_state,
    upsert_candles,
)
from tests.fakes.fake_mt5 import candles_until

pytestmark = pytest.mark.integration

NOW_EPOCH = 1_700_000_000


def candle_dicts(count: int, end_epoch: int = NOW_EPOCH, step: int = 60) -> list[dict]:
    rows = candles_until(end_epoch, count, step=step)
    for i, row in enumerate(rows):
        row["forming"] = i == count - 1
    return rows


@pytest.fixture
async def db_session(database_url: str):
    """Session bound to the MIGRATED per-session test cluster.

    Depends on ``database_url`` explicitly: the engine must point at the
    freshly-migrated temporary PostgreSQL, never a leftover .env database.
    The candles table is truncated before each test for isolation.
    """

    from sqlalchemy import text

    from app.core.config import get_settings
    from app.db.session import dispose_engine, get_engine, init_engine

    get_settings.cache_clear()
    init_engine(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("TRUNCATE TABLE candles"))
        yield session
    await dispose_engine()
    get_settings.cache_clear()


class TestUpsertAndLoad:
    async def test_upsert_then_load_roundtrip(self, db_session) -> None:
        rows = candle_dicts(5)
        written = await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        assert written == 5
        loaded = await load_candles(db_session, "EURUSD", "M1", 5)
        assert [c["time"] for c in loaded] == [r["time"] for r in rows]
        assert loaded[0]["close"] == pytest.approx(rows[0]["close"])

    async def test_repeated_upsert_creates_no_duplicates(self, db_session) -> None:
        rows = candle_dicts(5)
        await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        loaded = await load_candles(db_session, "EURUSD", "M1", 100)
        assert len(loaded) == 5

    async def test_forming_candle_updated_in_place(self, db_session) -> None:
        rows = candle_dicts(3)
        await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        # The forming candle evolves (close moves) then finally closes.
        evolved = [dict(rows[-1], close=9.999, forming=False), *rows[:-1]]
        await upsert_candles(db_session, "EURUSD", "M1", evolved)
        await db_session.commit()
        loaded = await load_candles(db_session, "EURUSD", "M1", 10)
        assert len(loaded) == 3
        last = [c for c in loaded if c["time"] == rows[-1]["time"]][0]
        assert last["close"] == pytest.approx(9.999)
        assert last["forming"] is False

    async def test_primary_key_blocks_raw_duplicates(self, db_session) -> None:
        from sqlalchemy import insert

        rows = candle_dicts(2)
        await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        open_time = datetime.fromtimestamp(rows[0]["time"], tz=timezone.utc)
        with pytest.raises(Exception):  # IntegrityError at the DB level
            await db_session.execute(
                insert(CandleRow).values(
                    symbol="EURUSD", timeframe="M1", open_time=open_time,
                    open=1, high=1, low=1, close=1, is_closed=True,
                )
            )
            await db_session.commit()

    async def test_load_limit_and_ordering(self, db_session) -> None:
        rows = candle_dicts(20)
        await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        loaded = await load_candles(db_session, "EURUSD", "M1", 5)
        assert len(loaded) == 5
        assert [c["time"] for c in loaded] == sorted(c["time"] for c in loaded)
        # Latest N, not oldest N:
        assert loaded[-1]["time"] == rows[-1]["time"]

    async def test_closed_only_filter(self, db_session) -> None:
        rows = candle_dicts(5)  # last one forming
        await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        loaded = await load_candles(db_session, "EURUSD", "M1", 10, closed_only=True)
        assert len(loaded) == 4
        assert all(c["forming"] is False for c in loaded)

    async def test_series_isolated_by_symbol_and_timeframe(self, db_session) -> None:
        await upsert_candles(db_session, "EURUSD", "M1", candle_dicts(3))
        await upsert_candles(db_session, "XAUUSD", "M1", candle_dicts(4))
        await upsert_candles(db_session, "EURUSD", "H1", candle_dicts(2, step=3600))
        await db_session.commit()
        assert len(await load_candles(db_session, "EURUSD", "M1", 100)) == 3
        assert len(await load_candles(db_session, "XAUUSD", "M1", 100)) == 4
        assert len(await load_candles(db_session, "EURUSD", "H1", 100)) == 2


class TestSeriesStateAndFreshness:
    async def test_series_state_facts(self, db_session) -> None:
        rows = candle_dicts(6)
        await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        state = await series_state(db_session, "EURUSD", "M1")
        assert state is not None
        assert state["newest_open_time"] == rows[-1]["time"]
        assert state["newest_is_closed"] is False  # last row forming
        assert state["cached_rows"] == 6
        assert state["source"] == "mt5"

    async def test_series_state_none_when_unknown(self, db_session) -> None:
        assert await series_state(db_session, "NOPE", "M1") is None

    async def test_freshness_policy(self, db_session) -> None:
        rows = candle_dicts(3, end_epoch=NOW_EPOCH)
        await upsert_candles(db_session, "EURUSD", "M1", rows)
        await db_session.commit()
        state = await series_state(db_session, "EURUSD", "M1")
        # Recent open (within grace) -> fresh.
        assert is_series_fresh(
            state, "M1", now_epoch=NOW_EPOCH + 30, cache_max_age_seconds=90
        )
        # Old open -> stale.
        assert not is_series_fresh(
            state, "M1", now_epoch=NOW_EPOCH + 3600, cache_max_age_seconds=90
        )
        # Unknown series never fresh.
        assert not is_series_fresh(None, "M1", now_epoch=NOW_EPOCH)

    async def test_large_batch_upsert(self, db_session) -> None:
        rows = candle_dicts(1200, step=60)
        written = await upsert_candles(db_session, "BTCUSD", "M1", rows)
        await db_session.commit()
        assert written == 1200
        loaded = await load_candles(db_session, "BTCUSD", "M1", 2000)
        assert len(loaded) == 1200
