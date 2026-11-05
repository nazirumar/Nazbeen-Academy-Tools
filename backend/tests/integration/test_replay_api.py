"""Integration tests: historical market replay (Phase 11).

The replay engine is exercised against a real PostgreSQL database and the
normal FakeMT5 candle-ingest path. The most important property is asserted
throughout: a chart response may contain only candles whose open time is at
or before the persisted session cursor. Simulated execution is intentionally
outside this first vertical slice.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.integration

REPLAY = "/api/v1/replay"
SESSIONS = f"{REPLAY}/sessions"


def candles_json(
    count: int, base_epoch: int, step: int = 60, start: float = 1.10
) -> list[dict]:
    out = []
    price = start
    for i in range(count):
        t = base_epoch - (count - 1 - i) * step
        o = round(price, 5)
        c = round(price + 0.0002, 5)
        out.append(
            {
                "time": t,
                "open": o,
                "high": round(c + 0.0001, 5),
                "low": round(o - 0.0001, 5),
                "close": c,
            }
        )
        price = c
    return out


def window_for(count: int, step: int = 60) -> dict[str, str]:
    base = int(time.time()) // 60 * 60
    start = datetime.fromtimestamp(base - (count - 1) * step, tz=timezone.utc)
    end = datetime.fromtimestamp(base, tz=timezone.utc)
    return {"start": start.isoformat(), "end": end.isoformat()}


async def seed_cache(client, fake_mt5, *, symbol: str = "EURUSD", count: int = 60) -> None:
    base = int(time.time()) // 60 * 60
    fake_mt5.rates[(symbol, fake_mt5.TIMEFRAME_M1)] = candles_json(count, base)
    fake_mt5.base_epoch = base
    resp = await client.get(
        "/api/v1/market/candles",
        params={"symbol": symbol, "timeframe": "M1", "limit": count},
    )
    assert resp.status_code == 200, resp.text


async def create_session(
    client,
    *,
    symbol: str = "EURUSD",
    timeframe: str = "M1",
    count: int = 60,
    mode: str = "forex",
    headers: dict[str, str] | None = None,
) -> dict:
    resp = await client.post(
        SESSIONS,
        json={
            "mode": mode,
            "symbol": symbol,
            "timeframe": timeframe,
            "playback_speed": 1.0,
            **window_for(count),
        },
        headers=headers or {},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def get_candles(client, session_id: str, **params) -> dict:
    resp = await client.get(f"{SESSIONS}/{session_id}/candles", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestReplayCreation:
    async def test_create_paused_session_and_initial_candle_only(
        self, app_client, fake_mt5
    ):
        await seed_cache(app_client, fake_mt5, count=60)
        data = await create_session(app_client)

        assert data["status"] == "paused"
        assert data["symbol"] == "EURUSD"
        assert data["timeframe"] == "M1"
        assert data["visible_candles"] == 1
        assert data["total_candles"] >= 2
        assert data["cursor_index"] == 0
        assert data["progress"] > 0
        assert data["at_end"] is False
        assert data["no_lookahead"] is True
        assert data["data_quality"]["closed_candles_only"] is True
        assert data["data_quality"]["forming_candles_used"] is False
        assert data["config"]["assumptions"]["gaps_filled"] is False

        series = await get_candles(app_client, data["id"])
        assert series["count"] == 1
        assert series["visible_candles"] == 1
        assert series["no_lookahead"] is True
        assert series["future_candles_excluded"] is True
        assert len(series["candles"]) == 1
        assert series["candles"][0]["time"] <= datetime.fromisoformat(
            data["cursor_time"]
        ).timestamp()
        assert series["candles"][0]["forming"] is False

    async def test_no_history_is_rejected(self, app_client, fake_mt5):
        await seed_cache(app_client, fake_mt5, count=10)
        resp = await app_client.post(
            SESSIONS,
            json={
                "mode": "forex",
                "symbol": "EURUSD",
                "timeframe": "M1",
                "start": "2005-01-03T00:00:00Z",
                "end": "2005-01-04T00:00:00Z",
            },
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "NO_HISTORICAL_DATA"

    async def test_binary_replay_is_m1_only(self, app_client, fake_mt5):
        await seed_cache(app_client, fake_mt5, count=10)
        resp = await app_client.post(
            SESSIONS,
            json={
                "mode": "binary_options",
                "symbol": "EURUSD",
                "timeframe": "M5",
                **window_for(10),
            },
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "MODE_TIMEFRAME_MISMATCH"

    async def test_inverted_window_is_rejected(self, app_client, fake_mt5):
        await seed_cache(app_client, fake_mt5, count=10)
        window = window_for(10)
        resp = await app_client.post(
            SESSIONS,
            json={
                "mode": "forex",
                "symbol": "EURUSD",
                "timeframe": "M1",
                "start": window["end"],
                "end": window["start"],
            },
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "INVALID_TIMEWINDOW"

    async def test_coverage_endpoint_is_read_only(self, app_client, fake_mt5):
        await seed_cache(app_client, fake_mt5, count=30)
        resp = await app_client.get(
            f"{REPLAY}/data-coverage",
            params={"symbol": "EURUSD", "timeframe": "M1", **window_for(30)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["candles_in_range"] >= 1
        assert data["data_quality"]["closed_candles_only"] is True
        assert data["data_quality"]["forming_candles_used"] is False
        # Checking coverage never creates a session.
        listed = await app_client.get(SESSIONS)
        assert listed.json()["data"]["items"] == []


class TestReplayCursorLifecycle:
    async def test_step_never_exposes_future_candles(self, app_client, fake_mt5):
        await seed_cache(app_client, fake_mt5, count=40)
        data = await create_session(app_client)
        total = data["total_candles"]

        first = await app_client.post(
            f"{SESSIONS}/{data['id']}/step", json={"count": 2}
        )
        assert first.status_code == 200, first.text
        stepped = first.json()["data"]
        assert stepped["visible_candles"] == 3
        assert stepped["cursor_index"] == 2
        assert stepped["status"] == "paused"

        series = await get_candles(app_client, data["id"], limit=500)
        cursor_epoch = datetime.fromisoformat(stepped["cursor_time"]).timestamp()
        assert series["count"] == 3
        assert series["candles"][-1]["time"] == int(cursor_epoch)
        assert all(c["time"] <= int(cursor_epoch) for c in series["candles"])
        assert series["count"] < total

        # Advance past the end in one bounded request. The cursor stops at the
        # last stored closed candle and the session truthfully completes.
        finish = await app_client.post(
            f"{SESSIONS}/{data['id']}/step", json={"count": 100}
        )
        assert finish.status_code == 200, finish.text
        done = finish.json()["data"]
        assert done["status"] == "completed"
        assert done["visible_candles"] == total
        assert done["at_end"] is True
        assert done["remaining_candles"] == 0

        full = await get_candles(app_client, data["id"], limit=500)
        assert full["count"] == total
        end_epoch = datetime.fromisoformat(done["cursor_time"]).timestamp()
        assert full["candles"][-1]["time"] == int(end_epoch)
        assert all(c["time"] <= int(end_epoch) for c in full["candles"])

        # Stepping an already complete session is idempotent, not an error.
        again = await app_client.post(
            f"{SESSIONS}/{data['id']}/step", json={"count": 1}
        )
        assert again.status_code == 200
        assert again.json()["data"]["status"] == "completed"
        assert again.json()["data"]["visible_candles"] == total

    async def test_pause_resume_reset_and_speed(self, app_client, fake_mt5):
        await seed_cache(app_client, fake_mt5, count=30)
        data = await create_session(app_client)
        sid = data["id"]

        resumed = await app_client.post(f"{SESSIONS}/{sid}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["data"]["status"] == "active"

        paused = await app_client.post(f"{SESSIONS}/{sid}/pause")
        assert paused.status_code == 200
        assert paused.json()["data"]["status"] == "paused"

        speed = await app_client.patch(
            f"{SESSIONS}/{sid}", json={"playback_speed": 4.0}
        )
        assert speed.status_code == 200, speed.text
        assert speed.json()["data"]["playback_speed"] == 4.0

        bad_speed = await app_client.patch(
            f"{SESSIONS}/{sid}", json={"playback_speed": 0}
        )
        assert bad_speed.status_code == 422

        await app_client.post(f"{SESSIONS}/{sid}/step", json={"count": 5})
        reset = await app_client.post(f"{SESSIONS}/{sid}/reset")
        assert reset.status_code == 200
        reset_data = reset.json()["data"]
        assert reset_data["status"] == "paused"
        assert reset_data["visible_candles"] == 1
        assert reset_data["cursor_time"] == data["cursor_time"]

    async def test_seek_uses_latest_candle_at_or_before_target(
        self, app_client, fake_mt5
    ):
        await seed_cache(app_client, fake_mt5, count=30)
        data = await create_session(app_client)
        first = datetime.fromisoformat(data["first_candle_time"])
        target = first.timestamp() + 10 * 60 + 37  # deliberately between bars

        resp = await app_client.post(
            f"{SESSIONS}/{data['id']}/seek",
            json={"target_time": datetime.fromtimestamp(target, tz=timezone.utc).isoformat()},
        )
        assert resp.status_code == 200, resp.text
        sought = resp.json()["data"]
        assert sought["visible_candles"] == 11
        assert sought["status"] == "paused"
        cursor = datetime.fromisoformat(sought["cursor_time"])
        assert cursor.timestamp() <= target
        assert first.timestamp() + 10 * 60 == cursor.timestamp()

        series = await get_candles(app_client, data["id"])
        assert series["count"] == 11
        assert all(c["time"] <= int(cursor.timestamp()) for c in series["candles"])

        too_early = await app_client.post(
            f"{SESSIONS}/{data['id']}/seek",
            json={"target_time": "2005-01-03T00:00:00Z"},
        )
        assert too_early.status_code == 422
        assert too_early.json()["error"]["code"] == "SEEK_OUT_OF_WINDOW"

    async def test_resume_after_end_conflicts_but_reset_recovers(
        self, app_client, fake_mt5
    ):
        await seed_cache(app_client, fake_mt5, count=12)
        data = await create_session(app_client)
        done = await app_client.post(
            f"{SESSIONS}/{data['id']}/step", json={"count": 100}
        )
        assert done.json()["data"]["status"] == "completed"

        conflict = await app_client.post(f"{SESSIONS}/{data['id']}/resume")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "REPLAY_AT_END"

        reset = await app_client.post(f"{SESSIONS}/{data['id']}/reset")
        assert reset.status_code == 200
        resume = await app_client.post(f"{SESSIONS}/{data['id']}/resume")
        assert resume.status_code == 200
        assert resume.json()["data"]["status"] == "active"


class TestReplayOwnershipAndHistory:
    async def test_foreign_owner_gets_404_everywhere(self, app_client, fake_mt5):
        await seed_cache(app_client, fake_mt5, count=20)
        owner = {"X-User-Id": "replay-owner"}
        data = await create_session(app_client, headers=owner)
        sid = data["id"]

        foreign = {"X-User-Id": "replay-foreign"}
        get_resp = await app_client.get(f"{SESSIONS}/{sid}", headers=foreign)
        assert get_resp.status_code == 404
        candles_resp = await app_client.get(
            f"{SESSIONS}/{sid}/candles", headers=foreign
        )
        assert candles_resp.status_code == 404
        step_resp = await app_client.post(
            f"{SESSIONS}/{sid}/step", json={"count": 1}, headers=foreign
        )
        assert step_resp.status_code == 404
        delete_resp = await app_client.delete(f"{SESSIONS}/{sid}", headers=foreign)
        assert delete_resp.status_code == 404

        own_list = await app_client.get(SESSIONS, headers=owner)
        assert own_list.json()["data"]["page"]["total"] == 1
        foreign_list = await app_client.get(SESSIONS, headers=foreign)
        assert foreign_list.json()["data"]["items"] == []

    async def test_history_filters_and_delete(self, app_client, fake_mt5):
        await seed_cache(app_client, fake_mt5, count=20)
        first = await create_session(app_client)
        second = await create_session(app_client, mode="binary_options")

        all_resp = await app_client.get(SESSIONS)
        assert all_resp.status_code == 200
        assert all_resp.json()["data"]["page"]["total"] == 2

        binary = await app_client.get(SESSIONS, params={"mode": "binary_options"})
        assert [item["id"] for item in binary.json()["data"]["items"]] == [
            second["id"]
        ]

        paused = await app_client.get(SESSIONS, params={"status": "paused"})
        assert paused.json()["data"]["page"]["total"] == 2

        deleted = await app_client.delete(f"{SESSIONS}/{first['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["data"]["deleted"] is True
        gone = await app_client.get(f"{SESSIONS}/{first['id']}")
        assert gone.status_code == 404
