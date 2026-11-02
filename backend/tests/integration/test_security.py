"""Phase 9 security & reliability integration tests.

Covers (spec §13 mapping):

1. Unauthenticated requests to protected endpoints when API auth is enabled
   (401 + ``WWW-Authenticate``), valid-token success, invalid token, and the
   no-op behaviour when auth is disabled (backwards compatibility).
2. Owner-binding enforcement: a bound token pins the owner; a spoofed
   ``X-User-Id`` for a different owner is ignored.
3. Cross-user resource isolation (strategies + categories as the shared
   representative resource; the 404 no-existence-leak contract).
4. Security headers on API responses.
5. Backtest creation rate limiting (429).

External dependencies are mocked by the existing fixtures; auth is exercised
through real configuration (``API_AUTH_TOKEN(S)`` env via indirect
parametrization of the ``app_client`` fixture).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

TOKEN = "phase9-integration-token-0123456789"
TOKEN_ALICE = "phase9-token-alice-0123456789"
TOKEN_BOB = "phase9-token-bob-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# 1. Authentication gate
# --------------------------------------------------------------------------- #


class TestAuthenticationGate:
    @pytest.mark.parametrize(
        "app_client",
        [{"API_AUTH_TOKEN": TOKEN, "API_AUTH_TOKENS": ""}],
        indirect=True,
    )
    async def test_unauthenticated_request_rejected_401(self, app_client) -> None:
        resp = await app_client.get("/api/v1/meta")
        assert resp.status_code == 401
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "UNAUTHORIZED"
        # Standard challenge, no internals leaked.
        assert resp.headers["www-authenticate"] == "Bearer"
        # The error message must not echo configuration or stack details.
        assert "phase9" not in resp.text

    @pytest.mark.parametrize(
        "app_client",
        [{"API_AUTH_TOKEN": TOKEN, "API_AUTH_TOKENS": ""}],
        indirect=True,
    )
    async def test_invalid_token_rejected_401(self, app_client) -> None:
        resp = await app_client.get(
            "/api/v1/meta", headers={"Authorization": "Bearer not-the-token-123456"}
        )
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        "app_client",
        [{"API_AUTH_TOKEN": TOKEN, "API_AUTH_TOKENS": ""}],
        indirect=True,
    )
    async def test_wrong_scheme_rejected_401(self, app_client) -> None:
        resp = await app_client.get(
            "/api/v1/meta", headers={"Authorization": f"Basic {TOKEN}"}
        )
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        "app_client",
        [{"API_AUTH_TOKEN": TOKEN, "API_AUTH_TOKENS": ""}],
        indirect=True,
    )
    async def test_valid_token_grants_access(self, app_client) -> None:
        resp = await app_client.get("/api/v1/meta", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.parametrize(
        "app_client",
        [{"API_AUTH_TOKEN": TOKEN, "API_AUTH_TOKENS": ""}],
        indirect=True,
    )
    async def test_health_endpoints_stay_open(self, app_client) -> None:
        """Infrastructure probes must not require API credentials."""
        live = await app_client.get("/health/live")
        assert live.status_code == 200
        ready = await app_client.get("/health/ready")
        assert ready.status_code == 200

    async def test_auth_disabled_keeps_previous_behaviour(self, app_client) -> None:
        """No tokens configured → every endpoint behaves as in Phase 1-8."""
        resp = await app_client.get("/api/v1/meta")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.parametrize(
        "app_client",
        [{"API_AUTH_TOKENS": f"{TOKEN_ALICE}:alice, {TOKEN_BOB}:bob"}],
        indirect=True,
    )
    async def test_csv_token_list_accepted(self, app_client) -> None:
        for token in (TOKEN_ALICE, TOKEN_BOB):
            resp = await app_client.get(
                "/api/v1/meta", headers=_auth_header(token)
            )
            assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# 2. Owner binding: bound tokens cannot be spoofed via X-User-Id
# --------------------------------------------------------------------------- #


class TestOwnerBinding:
    @pytest.mark.parametrize(
        "app_client",
        [{"API_AUTH_TOKENS": f"{TOKEN_ALICE}:alice, {TOKEN_BOB}:bob"}],
        indirect=True,
    )
    async def test_bound_token_pins_owner_and_ignores_spoof(self, app_client) -> None:
        """Alice creates a category; Bob's bound token cannot see it even when
        he claims alice's owner id via X-User-Id."""
        created = await app_client.post(
            "/api/v1/strategy-categories",
            json={"name": "AlicePrivate", "mode": "forex"},
            headers={**_auth_header(TOKEN_ALICE), "X-User-Id": "bob"},
        )
        assert created.status_code == 201, created.text

        # Bob with a spoofed X-User-Id=alice sees NOTHING of alice's scope —
        # the binding wins, so the request runs as bob.
        listing = await app_client.get(
            "/api/v1/strategy-categories",
            headers={**_auth_header(TOKEN_BOB), "X-User-Id": "alice"},
        )
        assert listing.status_code == 200
        names = [c["name"] for c in listing.json()["data"]]
        assert "AlicePrivate" not in names

        # Alice's own (bound) token sees it — X-User-Id spoofing is irrelevant.
        mine = await app_client.get(
            "/api/v1/strategy-categories",
            headers={**_auth_header(TOKEN_ALICE), "X-User-Id": "bob"},
        )
        names = [c["name"] for c in mine.json()["data"]]
        assert "AlicePrivate" in names


# --------------------------------------------------------------------------- #
# 3. Cross-user isolation (X-User-Id scoping, no auth configured)
# --------------------------------------------------------------------------- #


class TestCrossUserIsolation:
    async def test_foreign_category_is_404_not_403(self, app_client) -> None:
        """Foreign resources are indistinguishable from non-existent ones."""
        created = await app_client.post(
            "/api/v1/strategy-categories",
            json={"name": "OwnerAResearch", "mode": "forex"},
            headers={"X-User-Id": "alice"},
        )
        assert created.status_code == 201
        category_id = created.json()["data"]["id"]

        foreign_get = await app_client.get(
            f"/api/v1/strategy-categories/{category_id}",
            headers={"X-User-Id": "mallory"},
        )
        assert foreign_get.status_code == 404

        foreign_patch = await app_client.patch(
            f"/api/v1/strategy-categories/{category_id}",
            json={"name": "Hijacked"},
            headers={"X-User-Id": "mallory"},
        )
        assert foreign_patch.status_code == 404

        # Owner still sees it.
        own = await app_client.get(
            f"/api/v1/strategy-categories/{category_id}",
            headers={"X-User-Id": "alice"},
        )
        assert own.status_code == 200

    async def test_unscoped_requests_use_default_owner(self, app_client) -> None:
        """No header → default 'local' owner (backwards compatibility)."""
        created = await app_client.post(
            "/api/v1/strategy-categories",
            json={"name": "DefaultOwnerCat", "mode": "forex"},
        )
        assert created.status_code == 201
        listing = await app_client.get("/api/v1/strategy-categories")
        names = [c["name"] for c in listing.json()["data"]]
        assert "DefaultOwnerCat" in names


# --------------------------------------------------------------------------- #
# 4. Security headers
# --------------------------------------------------------------------------- #


class TestSecurityHeaders:
    async def test_api_response_has_security_headers(self, app_client) -> None:
        resp = await app_client.get("/api/v1/meta")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
        assert resp.headers["cache-control"] == "no-store"

    async def test_health_response_has_security_headers(self, app_client) -> None:
        resp = await app_client.get("/health/live")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["cache-control"] == "no-store"

    async def test_error_responses_keep_headers(self, app_client) -> None:
        resp = await app_client.get("/api/v1/strategy-categories/nonexistent-id")
        # 404 path must still carry the security headers.
        assert resp.headers["x-content-type-options"] == "nosniff"


# --------------------------------------------------------------------------- #
# 5. Backtest creation rate limit
# --------------------------------------------------------------------------- #


class TestBacktestCreationRateLimit:
    async def test_flood_is_rate_limited_429(self, app_client, monkeypatch) -> None:
        """A per-owner limit of 2 allows two creations, then returns 429.

        The payload passes request-body validation (so it reaches the
        handler) but references a non-existent strategy — service-level
        validation rejects it. The limiter runs BEFORE that validation,
        which is exactly the flood-protection contract.
        """
        import uuid as uuid_module

        from app.core.rate_limit import reset_rate_limits

        reset_rate_limits()
        monkeypatch.setenv("BACKTEST_MAX_CREATES_PER_MINUTE", "2")
        from app.core.config import get_settings

        get_settings.cache_clear()

        payload = {
            "mode": "forex",
            "strategy_id": str(uuid_module.uuid4()),  # well-formed, unknown
            "strategy_version": 1,
            "symbol": "EURUSD",
            "start": "2024-01-02T00:00:00+00:00",
            "end": "2024-01-03T00:00:00+00:00",
        }
        statuses: list[int] = []
        try:
            for _ in range(3):
                resp = await app_client.post(
                    "/api/v1/backtests",
                    json=payload,
                    headers={"X-User-Id": "ratelimit-user"},
                )
                statuses.append(resp.status_code)
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()
            reset_rate_limits()

        assert statuses[:2] == [404, 404]  # reached the handler (unknown strategy)
        assert statuses[2] == 429
        # After the window reset the owner is unblocked (per-owner limiter).
        body = await app_client.post(
            "/api/v1/backtests",
            json=payload,
            headers={"X-User-Id": "ratelimit-user"},
        )
        assert body.status_code == 404
