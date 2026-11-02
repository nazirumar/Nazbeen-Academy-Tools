"""Integration tests: strategy & category CRUD + immutable versioning."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def rule_set(mode: str = "forex", **overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "mode": mode,
        "timeframes": ["H1"] if mode == "forex" else ["M1"],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "c1",
                    "type": "price_level",
                    "field": "close",
                    "operator": "above",
                    "value": 1.08,
                    "timeframe": "H1" if mode == "forex" else "M1",
                }
            ],
            "groups": [],
        },
    }
    base.update(overrides)
    return base


async def make_strategy(client, mode: str = "forex", name: str = "Test Setup") -> dict:
    res = await client.post(
        "/api/v1/strategies",
        json={"name": name, "mode": mode, "direction": "bullish"},
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


class TestCategories:
    async def test_create_and_list(self, app_client) -> None:
        res = await app_client.post(
            "/api/v1/strategy-categories",
            json={"name": "Trend", "mode": "forex", "description": "Trend setups"},
        )
        assert res.status_code == 201
        cat = res.json()["data"]
        assert cat["name"] == "Trend"
        assert cat["strategy_count"] == 0

        listed = (await app_client.get("/api/v1/strategy-categories?mode=forex")).json()["data"]
        assert any(c["id"] == cat["id"] for c in listed)

    async def test_duplicate_sibling_name_conflict(self, app_client) -> None:
        await app_client.post(
            "/api/v1/strategy-categories", json={"name": "Dup", "mode": "forex"}
        )
        res = await app_client.post(
            "/api/v1/strategy-categories", json={"name": "Dup", "mode": "forex"}
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "CATEGORY_NAME_TAKEN"

    async def test_subcategory_mode_must_match_parent(self, app_client) -> None:
        parent = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "P", "mode": "forex"}
            )
        ).json()["data"]
        res = await app_client.post(
            "/api/v1/strategy-categories",
            json={"name": "Sub", "mode": "binary_options", "parent_id": parent["id"]},
        )
        assert res.status_code == 422

    async def test_subcategory_depth_limited(self, app_client) -> None:
        parent = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Root", "mode": "forex"}
            )
        ).json()["data"]
        sub = (
            await app_client.post(
                "/api/v1/strategy-categories",
                json={"name": "Sub", "mode": "forex", "parent_id": parent["id"]},
            )
        ).json()["data"]
        res = await app_client.post(
            "/api/v1/strategy-categories",
            json={"name": "SubSub", "mode": "forex", "parent_id": sub["id"]},
        )
        assert res.status_code == 422

    async def test_delete_non_empty_category_conflict(self, app_client) -> None:
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Busy", "mode": "forex"}
            )
        ).json()["data"]
        await app_client.post(
            "/api/v1/strategies",
            json={"name": "S", "mode": "forex", "direction": "bullish", "category_id": cat["id"]},
        )
        res = await app_client.delete(f"/api/v1/strategy-categories/{cat['id']}")
        assert res.status_code == 409

    async def test_missing_category_404(self, app_client) -> None:
        res = await app_client.get(f"/api/v1/strategy-categories/{uuid.uuid4()}")
        assert res.status_code == 404


class TestStrategies:
    async def test_create_list_filter(self, app_client) -> None:
        await make_strategy(app_client, mode="forex", name="F1")
        await make_strategy(app_client, mode="binary_options", name="B1")

        # Phase 3: the listing is paginated (items + page meta).
        forex = (await app_client.get("/api/v1/strategies?mode=forex")).json()["data"]
        bo = (await app_client.get("/api/v1/strategies?mode=binary_options")).json()["data"]
        assert [s["name"] for s in forex["items"]] == ["F1"]
        assert [s["name"] for s in bo["items"]] == ["B1"]
        assert forex["page"]["total"] == 1
        assert forex["page"]["page"] == 1

    async def test_mode_separation_category_mismatch(self, app_client) -> None:
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "ForexCat", "mode": "forex"}
            )
        ).json()["data"]
        res = await app_client.post(
            "/api/v1/strategies",
            json={
                "name": "WrongMode",
                "mode": "binary_options",
                "direction": "bullish",
                "category_id": cat["id"],
            },
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "MODE_MISMATCH"

    async def test_duplicate_name_conflict(self, app_client) -> None:
        await make_strategy(app_client, name="Unique Setup")
        res = await app_client.post(
            "/api/v1/strategies",
            json={"name": "Unique Setup", "mode": "forex", "direction": "bullish"},
        )
        assert res.status_code == 409

    async def test_update_and_delete(self, app_client) -> None:
        strat = await make_strategy(app_client, name="To Update")
        res = await app_client.patch(
            f"/api/v1/strategies/{strat['id']}", json={"status": "active"}
        )
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "active"

        res = await app_client.delete(f"/api/v1/strategies/{strat['id']}")
        assert res.status_code == 204
        res = await app_client.get(f"/api/v1/strategies/{strat['id']}")
        assert res.status_code == 404

    async def test_invalid_status_rejected(self, app_client) -> None:
        strat = await make_strategy(app_client, name="StatusProbe")
        res = await app_client.patch(
            f"/api/v1/strategies/{strat['id']}", json={"status": "gold"}
        )
        assert res.status_code == 422


class TestVersioning:
    async def test_save_and_read_versions(self, app_client) -> None:
        strat = await make_strategy(app_client)
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set(), "changelog": "v1 initial"},
        )
        assert res.status_code == 201
        assert res.json()["data"]["version"] == 1

        # second version bumps automatically
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set(), "changelog": "v2 tweak"},
        )
        assert res.json()["data"]["version"] == 2

        detail = (await app_client.get(f"/api/v1/strategies/{strat['id']}")).json()["data"]
        assert detail["current_version"] == 2
        assert detail["rule_set"]["schema_version"] == "1.0"

        versions = (
            await app_client.get(f"/api/v1/strategies/{strat['id']}/versions")
        ).json()["data"]
        assert [v["version"] for v in versions] == [2, 1]  # newest first
        assert versions[1]["changelog"] == "v1 initial"

    async def test_invalid_rule_set_rejected_with_paths(self, app_client) -> None:
        strat = await make_strategy(app_client)
        bad = rule_set()
        bad["conditions"]["conditions"][0]["operator"] = "nonexistent"
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions", json={"rule_set": bad}
        )
        assert res.status_code == 422
        err = res.json()["error"]
        assert err["code"] == "RULE_VALIDATION_FAILED"
        assert any("operator" in d["path"] for d in err["details"])

    async def test_mode_mismatch_between_strategy_and_rule_set(self, app_client) -> None:
        strat = await make_strategy(app_client, mode="binary_options")
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set(mode="forex")},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "MODE_MISMATCH"

    async def test_direction_mismatch_rejected(self, app_client) -> None:
        strat = await make_strategy(app_client, mode="forex")
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set(direction="bearish")},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "DIRECTION_MISMATCH"

    async def test_versions_are_immutable_snapshot(self, app_client) -> None:
        strat = await make_strategy(app_client)
        v1 = rule_set()
        v1["conditions"]["conditions"][0]["value"] = 1.11
        await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions", json={"rule_set": v1}
        )
        v2 = rule_set()
        v2["conditions"]["conditions"][0]["value"] = 2.22
        await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions", json={"rule_set": v2}
        )
        old = (
            await app_client.get(f"/api/v1/strategies/{strat['id']}/versions/1")
        ).json()["data"]
        assert old["rule_set"]["conditions"]["conditions"][0]["value"] == 1.11

    async def test_no_rule_set_flag(self, app_client) -> None:
        strat = await make_strategy(app_client)
        detail = (await app_client.get(f"/api/v1/strategies/{strat['id']}")).json()["data"]
        assert detail["current_version"] == 0
        assert detail["rule_set"] is None


class TestRuleSetValidationEndpoint:
    async def test_validate_only_ok(self, app_client) -> None:
        res = await app_client.post(
            "/api/v1/rule-sets/validate", json={"rule_set": rule_set()}
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["valid"] is True
        assert data["normalized"]["mode"] == "forex"

    async def test_validate_only_errors(self, app_client) -> None:
        bad = rule_set()
        bad["conditions"]["conditions"][0]["type"] = "moon_phase"
        res = await app_client.post(
            "/api/v1/rule-sets/validate", json={"rule_set": bad}
        )
        data = res.json()["data"]
        assert data["valid"] is False
        assert data["errors"]
