"""Integration tests: Phase 3 strategy builder workflows.

Covers: rule-type registry endpoints, category/subcategory archiving,
strategy pagination, draft -> publish -> supersede lifecycle (immutable
published definitions), duplication, archiving, ownership/authorization and
the per-strategy validation audit trail.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

ALICE = {"X-User-Id": "alice"}
BOB = {"X-User-Id": "bob"}


def rule_set_v2(**overrides) -> dict:
    base = {
        "schema_version": "2.0",
        "mode": "forex",
        "timeframes": ["H1"],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "c1",
                    "type": "price_level",
                    "timeframe": "H1",
                    "params": {"field": "close", "operator": "above", "value": 1.08},
                }
            ],
            "groups": [],
        },
    }
    base.update(overrides)
    return base


async def make_strategy(client, mode="forex", name="Test Setup", direction="bullish", headers=None, category_id=None):
    res = await client.post(
        "/api/v1/strategies",
        json={
            "name": name,
            "mode": mode,
            "direction": direction,
            **({"category_id": category_id} if category_id else {}),
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def save_version(client, strategy_id: str, rule_set: dict, changelog: str | None = None, headers=None) -> dict:
    res = await client.post(
        f"/api/v1/strategies/{strategy_id}/versions",
        json={"rule_set": rule_set, **({"changelog": changelog} if changelog else {})},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


# --------------------------------------------------------------------------- #
# Rule types registry endpoints
# --------------------------------------------------------------------------- #


class TestRuleTypesEndpoints:
    async def test_list_rule_types(self, app_client) -> None:
        res = await app_client.get("/api/v1/rule-types")
        assert res.status_code == 200
        types = res.json()["data"]["rule_types"]
        by_type = {t["type"]: t for t in types}
        assert by_type["price_level"]["available"] is True
        assert by_type["moving_average"]["available"] is True
        # Phase 4: promoted types are operational with documented semantics;
        # order_block/breaker_block/support_resistance_reaction stay planned.
        assert by_type["mss_choch"]["available"] is True
        assert by_type["mss_choch"]["status"] == "operational"
        assert by_type["bos"]["available"] is True
        assert by_type["liquidity_sweep"]["available"] is True
        assert by_type["fvg"]["available"] is True
        assert by_type["order_block"]["available"] is False
        assert by_type["order_block"]["status"] == "planned"
        # Every available type documents its semantics.
        for t in by_type.values():
            if t["available"]:
                assert t["semantics"]
                assert t["params_schema"] or t["type"] == "price_level"

    async def test_rule_type_detail(self, app_client) -> None:
        res = await app_client.get("/api/v1/rule-types/swing_point")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["required_params"] == ["kind", "strength"]
        assert data["params_schema"]["strength"]["minimum"] == 1

    async def test_rule_type_404(self, app_client) -> None:
        res = await app_client.get("/api/v1/rule-types/nonexistent")
        assert res.status_code == 404


# --------------------------------------------------------------------------- #
# Category archive / subcategories
# --------------------------------------------------------------------------- #


class TestCategoryArchive:
    async def test_archive_hides_from_default_list(self, app_client) -> None:
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Old", "mode": "forex"}
            )
        ).json()["data"]
        res = await app_client.post(f"/api/v1/strategy-categories/{cat['id']}/archive")
        assert res.status_code == 200
        assert res.json()["data"]["is_archived"] is True

        listed = (await app_client.get("/api/v1/strategy-categories?mode=forex")).json()["data"]
        assert all(c["id"] != cat["id"] for c in listed)
        listed_all = (
            await app_client.get("/api/v1/strategy-categories?mode=forex&include_archived=true")
        ).json()["data"]
        assert any(c["id"] == cat["id"] for c in listed_all)

    async def test_archive_archives_subcategories(self, app_client) -> None:
        parent = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "P", "mode": "forex"}
            )
        ).json()["data"]
        await app_client.post(
            "/api/v1/strategy-categories",
            json={"name": "Sub", "mode": "forex", "parent_id": parent["id"]},
        )
        res = await app_client.post(f"/api/v1/strategy-categories/{parent['id']}/archive")
        assert res.json()["data"]["archived_subcategories"] == 1
        subs = (
            await app_client.get(
                "/api/v1/strategy-categories?mode=forex&include_archived=true"
            )
        ).json()["data"][0]["children"]
        assert subs[0]["is_archived"] is True

    async def test_archived_blocks_subcategory_creation(self, app_client) -> None:
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Arc", "mode": "forex"}
            )
        ).json()["data"]
        await app_client.post(f"/api/v1/strategy-categories/{cat['id']}/archive")
        res = await app_client.post(
            "/api/v1/strategy-categories",
            json={"name": "Sub", "mode": "forex", "parent_id": cat["id"]},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "CATEGORY_ARCHIVED"

    async def test_archived_blocks_strategy_creation(self, app_client) -> None:
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Arc2", "mode": "forex"}
            )
        ).json()["data"]
        await app_client.post(f"/api/v1/strategy-categories/{cat['id']}/archive")
        res = await app_client.post(
            "/api/v1/strategies",
            json={
                "name": "S",
                "mode": "forex",
                "direction": "bullish",
                "category_id": cat["id"],
            },
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "CATEGORY_ARCHIVED"

    async def test_unarchive(self, app_client) -> None:
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Back", "mode": "forex"}
            )
        ).json()["data"]
        await app_client.post(f"/api/v1/strategy-categories/{cat['id']}/archive")
        res = await app_client.post(f"/api/v1/strategy-categories/{cat['id']}/unarchive")
        assert res.json()["data"]["is_archived"] is False
        listed = (await app_client.get("/api/v1/strategy-categories?mode=forex")).json()["data"]
        assert any(c["id"] == cat["id"] for c in listed)

    async def test_delete_still_blocked_with_strategies(self, app_client) -> None:
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Busy", "mode": "forex"}
            )
        ).json()["data"]
        await make_strategy(app_client, category_id=cat["id"])
        res = await app_client.delete(f"/api/v1/strategy-categories/{cat['id']}")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "CATEGORY_NOT_EMPTY"


class TestSubcategories:
    async def test_nested_listing_and_rename(self, app_client) -> None:
        parent = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Root", "mode": "forex"}
            )
        ).json()["data"]
        sub = (
            await app_client.post(
                "/api/v1/strategy-categories",
                json={"name": "Breakouts", "mode": "forex", "parent_id": parent["id"]},
            )
        ).json()["data"]
        listed = (await app_client.get("/api/v1/strategy-categories?mode=forex")).json()["data"]
        assert listed[0]["children"][0]["id"] == sub["id"]

        renamed = (
            await app_client.patch(
                f"/api/v1/strategy-categories/{sub['id']}", json={"name": "London breakouts"}
            )
        ).json()["data"]
        assert renamed["name"] == "London breakouts"
        assert renamed["parent_id"] == parent["id"]

    async def test_delete_sub_then_parent(self, app_client) -> None:
        parent = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "Root2", "mode": "forex"}
            )
        ).json()["data"]
        sub = (
            await app_client.post(
                "/api/v1/strategy-categories",
                json={"name": "Sub", "mode": "forex", "parent_id": parent["id"]},
            )
        ).json()["data"]
        assert (
            await app_client.delete(f"/api/v1/strategy-categories/{parent['id']}")
        ).status_code == 409
        assert (
            await app_client.delete(f"/api/v1/strategy-categories/{sub['id']}")
        ).status_code == 204
        assert (
            await app_client.delete(f"/api/v1/strategy-categories/{parent['id']}")
        ).status_code == 204


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #


class TestPagination:
    async def test_pages_and_meta(self, app_client) -> None:
        for i in range(25):
            await make_strategy(app_client, name=f"S{i:02d}")
        res = await app_client.get("/api/v1/strategies?page=2&page_size=10")
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data["items"]) == 10
        assert data["page"] == {"page": 2, "page_size": 10, "total": 25, "pages": 3}
        # No overlap between pages
        page1 = (await app_client.get("/api/v1/strategies?page=1&page_size=10")).json()["data"]
        ids1 = {s["id"] for s in page1["items"]}
        assert ids1.isdisjoint({s["id"] for s in data["items"]})

    async def test_page_size_clamped(self, app_client) -> None:
        await make_strategy(app_client, name="Only")
        res = await app_client.get("/api/v1/strategies?page_size=500")
        assert res.status_code == 422  # > 100 rejected by query validation

    async def test_status_filter(self, app_client) -> None:
        await make_strategy(app_client, name="D1")
        res = await app_client.get("/api/v1/strategies?status=draft")
        assert res.json()["data"]["page"]["total"] == 1
        res = await app_client.get("/api/v1/strategies?status=active")
        assert res.json()["data"]["page"]["total"] == 0


# --------------------------------------------------------------------------- #
# Draft -> publish -> supersede lifecycle
# --------------------------------------------------------------------------- #


class TestPublishWorkflow:
    async def test_full_lifecycle(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Lifecycle")

        # Draft v1: strategy stays draft, nothing published yet.
        v1 = await save_version(app_client, strat["id"], rule_set_v2(), "initial")
        assert v1["status"] == "draft"
        detail = (await app_client.get(f"/api/v1/strategies/{strat['id']}")).json()["data"]
        assert detail["published_version_number"] is None
        assert detail["status"] == "draft"

        # Publish v1.
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions/1/publish"
        )
        assert res.status_code == 200
        pub = res.json()["data"]
        assert pub["version"]["status"] == "published"
        assert pub["strategy"]["status"] == "active"
        assert pub["strategy"]["published_version_number"] == 1
        assert pub["summary"]

        # Editing published strategy -> new DRAFT version; v1 stays published.
        v2_rule = rule_set_v2()
        v2_rule["conditions"]["conditions"][0]["params"]["value"] = 1.11
        v2 = await save_version(app_client, strat["id"], v2_rule, "tighter level")
        assert v2["status"] == "draft"
        detail = (await app_client.get(f"/api/v1/strategies/{strat['id']}")).json()["data"]
        assert detail["current_version"] == 2
        assert detail["published_version_number"] == 1

        # Publish v2: v1 becomes superseded but is never mutated.
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions/2/publish"
        )
        assert res.status_code == 200
        versions = (
            await app_client.get(f"/api/v1/strategies/{strat['id']}/versions")
        ).json()["data"]
        by_n = {v["version"]: v for v in versions}
        assert by_n[1]["status"] == "superseded"
        assert by_n[2]["status"] == "published"
        assert by_n[1]["rule_set"]["conditions"]["conditions"][0]["params"]["value"] == 1.08

        # Republishing is refused with precise codes.
        res = await app_client.post(f"/api/v1/strategies/{strat['id']}/versions/2/publish")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "ALREADY_PUBLISHED"
        res = await app_client.post(f"/api/v1/strategies/{strat['id']}/versions/1/publish")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "VERSION_SUPERSEDED"

        # Historical analyses can still read the superseded definition.
        old = (
            await app_client.get(f"/api/v1/strategies/{strat['id']}/versions/1")
        ).json()["data"]
        assert old["rule_set"]["conditions"]["conditions"][0]["params"]["value"] == 1.08

    async def test_publish_missing_version_404(self, app_client) -> None:
        strat = await make_strategy(app_client, name="NoVer")
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions/3/publish"
        )
        assert res.status_code == 404

    async def test_detail_includes_summary(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Summary")
        await save_version(app_client, strat["id"], rule_set_v2())
        detail = (await app_client.get(f"/api/v1/strategies/{strat['id']}")).json()["data"]
        assert detail["summary"]
        assert any("Bullish" in line or "BULLISH" in line for line in detail["summary"])

    async def test_rule_set_mode_mismatch_still_enforced(self, app_client) -> None:
        strat = await make_strategy(app_client, mode="binary_options", name="BO")
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set_v2(mode="forex")},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "MODE_MISMATCH"


# --------------------------------------------------------------------------- #
# Duplication
# --------------------------------------------------------------------------- #


class TestDuplication:
    async def test_duplicate_copies_metadata_and_latest_rules(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Original", direction="bearish")
        await save_version(app_client, strat["id"], rule_set_v2(direction="bearish"))
        await save_version(app_client, strat["id"], rule_set_v2(direction="bearish"))

        res = await app_client.post(f"/api/v1/strategies/{strat['id']}/duplicate")
        assert res.status_code == 201
        data = res.json()["data"]
        assert data["strategy"]["name"] == "Copy of Original"
        assert data["strategy"]["direction"] == "bearish"
        assert data["strategy"]["status"] == "draft"
        assert data["strategy"]["current_version"] == 1
        assert data["strategy"]["published_version_number"] is None
        assert data["version"]["version"] == 1
        assert data["version"]["status"] == "draft"
        assert "Duplicated from 'Original' v2" in data["version"]["changelog"]
        # The copy carries the LATEST rule set, as a fresh draft.
        copy_detail = (
            await app_client.get(f"/api/v1/strategies/{data['strategy']['id']}")
        ).json()["data"]
        assert (
            copy_detail["rule_set"]["conditions"]["conditions"][0]["params"]["value"]
            == 1.08
        )

    async def test_duplicate_custom_name_and_no_rules(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Src")
        await save_version(app_client, strat["id"], rule_set_v2())
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/duplicate",
            json={"name": "My Variant", "include_rule_set": False},
        )
        data = res.json()["data"]
        assert data["strategy"]["name"] == "My Variant"
        assert data["strategy"]["current_version"] == 0
        assert data["version"] is None

    async def test_duplicate_name_conflict(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Taken")
        await make_strategy(app_client, name="Copy of Taken")
        res = await app_client.post(f"/api/v1/strategies/{strat['id']}/duplicate")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "STRATEGY_NAME_TAKEN"


# --------------------------------------------------------------------------- #
# Strategy archive
# --------------------------------------------------------------------------- #


class TestStrategyArchive:
    async def test_archive_unarchive_lifecycle(self, app_client) -> None:
        strat = await make_strategy(app_client, name="ToArchive")
        await save_version(app_client, strat["id"], rule_set_v2())
        await app_client.post(f"/api/v1/strategies/{strat['id']}/versions/1/publish")

        res = await app_client.post(f"/api/v1/strategies/{strat['id']}/archive")
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "archived"

        # Hidden from the default listing, visible via filters.
        default = (await app_client.get("/api/v1/strategies")).json()["data"]
        assert all(s["id"] != strat["id"] for s in default["items"])
        explicit = (
            await app_client.get("/api/v1/strategies?status=archived")
        ).json()["data"]
        assert any(s["id"] == strat["id"] for s in explicit["items"])
        incl = (
            await app_client.get("/api/v1/strategies?include_archived=true")
        ).json()["data"]
        assert any(s["id"] == strat["id"] for s in incl["items"])

        # Publish blocked while archived.
        v2 = await save_version(app_client, strat["id"], rule_set_v2())
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions/{v2['version']}/publish"
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "STRATEGY_ARCHIVED"

        # Unarchive returns to active (a published definition exists).
        res = await app_client.post(f"/api/v1/strategies/{strat['id']}/unarchive")
        assert res.json()["data"]["status"] == "active"

    async def test_unarchive_without_publication_returns_draft(self, app_client) -> None:
        strat = await make_strategy(app_client, name="NeverPublished")
        await app_client.post(f"/api/v1/strategies/{strat['id']}/archive")
        res = await app_client.post(f"/api/v1/strategies/{strat['id']}/unarchive")
        assert res.json()["data"]["status"] == "draft"


# --------------------------------------------------------------------------- #
# Direction "both"
# --------------------------------------------------------------------------- #


class TestDirectionBoth:
    async def test_both_strategy_accepts_directional_rule_set(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Flexible", direction="both")
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set_v2(direction="bearish")},
        )
        assert res.status_code == 201

    async def test_directional_strategy_rejects_both_rule_set(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Strict", direction="bullish")
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set_v2(direction="both")},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "DIRECTION_MISMATCH"

    async def test_invalid_direction_rejected(self, app_client) -> None:
        res = await app_client.post(
            "/api/v1/strategies",
            json={"name": "X", "mode": "forex", "direction": "sideways"},
        )
        assert res.status_code == 422


# --------------------------------------------------------------------------- #
# New rule types through the API
# --------------------------------------------------------------------------- #


class TestNewRuleTypesViaApi:
    async def test_save_version_with_new_rule_types(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Structure")
        rs = rule_set_v2(
            timeframes=["H1", "M15"],
            conditions={
                "logic": "AND",
                "conditions": [
                    {
                        "id": "ma",
                        "type": "moving_average",
                        "timeframe": "H1",
                        "lookback": 60,
                        "params": {
                            "ma_type": "ema",
                            "period": 20,
                            "compare_to": "price_field",
                            "price_field": "close",
                            "operator": "above",
                        },
                    },
                    {
                        "id": "hh",
                        "type": "structure_pattern",
                        "timeframe": "M15",
                        "optional": True,
                        "params": {"pattern": "higher_highs", "count": 2},
                    },
                ],
                "groups": [],
            },
            invalidation={
                "logic": "OR",
                "conditions": [
                    {
                        "id": "brk",
                        "type": "market_structure_break",
                        "timeframe": "M15",
                        "params": {"side": "bearish", "strength": 2},
                    }
                ],
                "groups": [],
            },
            symbols=["EURUSD"],
            market_regime="trending",
            evaluation={"on_close_only": True},
        )
        version = await save_version(app_client, strat["id"], rs, "phase 3 rules")
        stored = version["rule_set"]
        assert stored["conditions"]["conditions"][1]["optional"] is True
        assert stored["symbols"] == ["EURUSD"]
        assert stored["market_regime"] == "trending"
        assert stored["evaluation"] == {"on_close_only": True}

    async def test_planned_rule_type_rejected_via_api(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Planned")
        rs = rule_set_v2()
        rs["conditions"]["conditions"][0] = {
            "id": "ob",
            "type": "order_block",  # still PLANNED in Phase 4
            "timeframe": "H1",
            "params": {},
        }
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions", json={"rule_set": rs}
        )
        assert res.status_code == 422
        err = res.json()["error"]
        assert any(
            d["code"] == "rule_type_not_available" for d in err["details"]
        )

    async def test_param_error_paths_point_at_field(self, app_client) -> None:
        strat = await make_strategy(app_client, name="ParamPaths")
        rs = rule_set_v2()
        rs["conditions"]["conditions"][0] = {
            "id": "sw",
            "type": "swing_point",
            "timeframe": "H1",
            "params": {"kind": "diagonal", "strength": 99},
        }
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions", json={"rule_set": rs}
        )
        assert res.status_code == 422
        paths = {d["path"] for d in res.json()["error"]["details"]}
        assert ".conditions.conditions[0].params.kind" in paths
        assert ".conditions.conditions[0].params.strength" in paths


# --------------------------------------------------------------------------- #
# Ownership / authorization
# --------------------------------------------------------------------------- #


class TestOwnership:
    async def test_strategies_are_owner_scoped(self, app_client) -> None:
        strat = await make_strategy(app_client, name="AliceOnly", headers=ALICE)

        # Bob sees an empty list and cannot access Alice's strategy.
        bob_list = (await app_client.get("/api/v1/strategies", headers=BOB)).json()["data"]
        assert bob_list["page"]["total"] == 0
        assert (
            await app_client.get(f"/api/v1/strategies/{strat['id']}", headers=BOB)
        ).status_code == 404
        assert (
            await app_client.patch(
                f"/api/v1/strategies/{strat['id']}", json={"name": "Hacked"}, headers=BOB
            )
        ).status_code == 404
        assert (
            await app_client.delete(f"/api/v1/strategies/{strat['id']}", headers=BOB)
        ).status_code == 404
        assert (
            await app_client.get(
                f"/api/v1/strategies/{strat['id']}/versions", headers=BOB
            )
        ).status_code == 404
        assert (
            await app_client.post(
                f"/api/v1/strategies/{strat['id']}/versions/1/publish", headers=BOB
            )
        ).status_code == 404
        assert (
            await app_client.post(
                f"/api/v1/strategies/{strat['id']}/duplicate", headers=BOB
            )
        ).status_code == 404
        # Validation against a foreign strategy is refused too.
        assert (
            await app_client.post(
                f"/api/v1/strategies/{strat['id']}/validate",
                json={"rule_set": rule_set_v2()},
                headers=BOB,
            )
        ).status_code == 404

        # Alice still sees everything.
        alice_list = (await app_client.get("/api/v1/strategies", headers=ALICE)).json()["data"]
        assert alice_list["page"]["total"] == 1

    async def test_default_owner_is_separate(self, app_client) -> None:
        await make_strategy(app_client, name="LocalDefault")
        alice_list = (await app_client.get("/api/v1/strategies", headers=ALICE)).json()["data"]
        assert alice_list["page"]["total"] == 0
        local_list = (await app_client.get("/api/v1/strategies")).json()["data"]
        assert local_list["page"]["total"] == 1

    async def test_categories_are_owner_scoped(self, app_client) -> None:
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories",
                json={"name": "AliceCat", "mode": "forex"},
                headers=ALICE,
            )
        ).json()["data"]
        bob_list = (
            await app_client.get("/api/v1/strategy-categories", headers=BOB)
        ).json()["data"]
        assert bob_list == []
        assert (
            await app_client.get(
                f"/api/v1/strategy-categories/{cat['id']}", headers=BOB
            )
        ).status_code == 404
        # Same-name categories in different owners do not conflict.
        res = await app_client.post(
            "/api/v1/strategy-categories", json={"name": "AliceCat", "mode": "forex"}
        )
        assert res.status_code == 201

    async def test_same_name_different_owners_allowed(self, app_client) -> None:
        await make_strategy(app_client, name="Shared", headers=ALICE)
        res = await app_client.post(
            "/api/v1/strategies",
            json={"name": "Shared", "mode": "forex", "direction": "bullish"},
            headers=BOB,
        )
        assert res.status_code == 201

    async def test_invalid_owner_header_rejected(self, app_client) -> None:
        res = await app_client.get(
            "/api/v1/strategies", headers={"X-User-Id": "bad owner!"}
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "INVALID_OWNER_ID"

    async def test_owner_scoped_duplicate_names_same_owner_conflict(
        self, app_client
    ) -> None:
        await make_strategy(app_client, name="Dup", headers=ALICE)
        res = await app_client.post(
            "/api/v1/strategies",
            json={"name": "Dup", "mode": "forex", "direction": "bullish"},
            headers=ALICE,
        )
        assert res.status_code == 409


# --------------------------------------------------------------------------- #
# Validation audit trail
# --------------------------------------------------------------------------- #


class TestStrategyValidationEndpoint:
    async def test_valid_rule_set_recorded(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Audit")
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/validate",
            json={"rule_set": rule_set_v2()},
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["valid"] is True
        assert data["validation_id"]
        assert data["normalized"]["schema_version"] == "2.0"
        assert data["summary"]

    async def test_invalid_rule_set_recorded_with_errors(self, app_client) -> None:
        strat = await make_strategy(app_client, name="AuditBad")
        bad = rule_set_v2()
        bad["conditions"]["conditions"][0]["params"] = {"field": "close"}
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/validate", json={"rule_set": bad}
        )
        data = res.json()["data"]
        assert data["valid"] is False
        assert any(
            e["code"] == "missing_param" for e in data["errors"]
        )
        assert data["validation_id"]

    async def test_direction_mismatch_reported(self, app_client) -> None:
        strat = await make_strategy(app_client, name="AuditDir", direction="bearish")
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/validate",
            json={"rule_set": rule_set_v2(direction="bullish")},
        )
        data = res.json()["data"]
        assert data["valid"] is False
        assert any(e["code"] == "strategy_mismatch" for e in data["errors"])

    async def test_validate_does_not_create_versions(self, app_client) -> None:
        strat = await make_strategy(app_client, name="NoVersion")
        await app_client.post(
            f"/api/v1/strategies/{strat['id']}/validate",
            json={"rule_set": rule_set_v2()},
        )
        detail = (await app_client.get(f"/api/v1/strategies/{strat['id']}")).json()["data"]
        assert detail["current_version"] == 0

    async def test_validation_of_foreign_strategy_404(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Hidden", headers=ALICE)
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/validate",
            json={"rule_set": rule_set_v2()},
            headers=BOB,
        )
        assert res.status_code == 404


# --------------------------------------------------------------------------- #
# Regression: Phase 1/2 strategy behaviour
# --------------------------------------------------------------------------- #


class TestRegression:
    async def test_legacy_v1_rule_set_still_saves_and_reads(self, app_client) -> None:
        strat = await make_strategy(app_client, name="Legacy")
        legacy = {
            "schema_version": "1.0",
            "mode": "forex",
            "timeframes": ["H1"],
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
                        "timeframe": "H1",
                    }
                ],
                "groups": [],
            },
        }
        version = await save_version(app_client, strat["id"], legacy)
        assert version["rule_set"]["schema_version"] == "1.0"
        assert "field" in version["rule_set"]["conditions"]["conditions"][0]
        # ... and evaluates through the analysis endpoint.
        candles = [
            {"time": 1700000000 + i * 3600, "open": 1.08, "high": 1.09, "low": 1.079, "close": 1.085}
            for i in range(3)
        ]
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={"strategy_id": strat["id"], "candles": {"H1": candles}},
        )
        assert res.status_code == 200
        assert res.json()["data"]["evaluation"]["setup_status"] == "conditions_met"

    async def test_unknown_strategy_404(self, app_client) -> None:
        res = await app_client.get(f"/api/v1/strategies/{uuid.uuid4()}")
        assert res.status_code == 404
