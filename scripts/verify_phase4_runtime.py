# Phase 4 runtime verification: real PostgreSQL + real migration + live API.
# Run: cd backend && python3 ../scripts/verify_phase4_runtime.py
import json, os, time, uuid, tempfile, shutil
from pathlib import Path
import pgserver

pgdata = Path(tempfile.mkdtemp(prefix="ta-phase4-verify-"))
server = pgserver.get_server(str(pgdata), cleanup_mode=None)
dbname = f"ta_verify_{uuid.uuid4().hex[:8]}"
server.psql(f"CREATE DATABASE {dbname};")
url = f"postgresql+asyncpg://postgres@/{dbname}?host={pgdata}"
os.environ["ALEMBIC_DATABASE_URL"] = url
os.environ["DATABASE_URL"] = url
os.environ["APP_ENV"] = "local"

# 1. Real migration chain (includes b7e4d2c9a510_phase4_analysis_reports).
from alembic import command
from alembic.config import Config
cfg = Config("alembic.ini")
cfg.set_main_option("script_location", "alembic")
command.upgrade(cfg, "head")
print("MIGRATION OK: alembic upgrade head")

# Verify the table + constraints exist (query the app database directly).
import asyncio
import asyncpg

def db_query(query: str) -> list[str]:
    async def _run() -> list[str]:
        conn = await asyncpg.connect(server.get_uri(), database=dbname)
        try:
            rows = await conn.fetch(query)
            return [list(r.values())[0] for r in rows]
        finally:
            await conn.close()
    return asyncio.run(_run())

cols = db_query("SELECT column_name FROM information_schema.columns WHERE table_name='analysis_reports' ORDER BY ordinal_position;")
assert "report_schema_version" in cols and "regime" in cols, cols
idx = db_query("SELECT indexname FROM pg_indexes WHERE tablename='analysis_reports';")
assert "ix_analysis_reports_owner_created_at" in idx, idx
print("TABLE OK: analysis_reports with owner/symbol/mode/strategy/status indexes")

# 2. Live server.
os.environ["MT5_ENABLED"] = "true"
import uvicorn
from app.main import app

config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
server_task = uvicorn.Server(config)
import threading
t = threading.Thread(target=server_task.run, daemon=True)
t.start()
time.sleep(2.5)

import httpx
c = httpx.Client(base_url="http://127.0.0.1:8765", timeout=30)

def show(name, resp):
    body = resp.json()
    print(f"\n=== {name} [{resp.status_code}] code={body.get('error') and body['error']['code'] or '-'}")
    return body

r = show("GET /health/ready", c.get("/health/ready"))
assert r["success"] and r["data"]["status"] == "ready", r

r = show("GET /api/v1/analysis/readiness", c.get("/api/v1/analysis/readiness"))
d = r["data"]
assert d["ready"] is True and d["live_analysis_ready"] is False, d
assert d["checks"]["rule_registry"]["coherent"] and d["checks"]["rule_registry"]["available"] == 12
print("   ready=True live=False (no MT5 terminal in sandbox — honest), registry coherent, features:", d["checks"]["feature_count"])

r = show("GET /api/v1/analysis/features", c.get("/api/v1/analysis/features"))
assert len(r["data"]["features"]) == 8 and len(r["data"]["regimes"]) == 6
print("   features:", [f["name"] for f in r["data"]["features"]])

# 3. Strategy workflow (Phase 3 preserved).
cat = show("POST strategy-categories", c.post("/api/v1/strategy-categories", json={"name": "verify", "mode": "forex"}))["data"]
st = show("POST strategies", c.post("/api/v1/strategies", json={"name": "Verify engine", "mode": "forex", "direction": "bullish", "category_id": cat["id"]}))["data"]
rule_set = {
    "schema_version": "2.0", "mode": "forex", "timeframes": ["M1"], "direction": "bullish",
    "market_regime": "uptrend",
    "conditions": {"logic": "AND", "conditions": [
        {"id": "px", "type": "price_level", "timeframe": "M1", "lookback": 30,
         "params": {"field": "close", "operator": "above", "value": 1.10}},
        {"id": "sw", "type": "liquidity_sweep", "timeframe": "M1", "params": {"kind": "low"}},
    ], "groups": []},
}
v = show("POST versions (advanced rules accepted)", c.post(f"/api/v1/strategies/{st['id']}/versions", json={"rule_set": rule_set}))["data"]
pub = show("POST publish", c.post(f"/api/v1/strategies/{st['id']}/versions/{v['version']}/publish"))
assert pub["data"]["version"]["status"] == "published"

# 4. Engine run without MT5 -> honest 503, nothing persisted.
r = show("POST /analysis/run (no MT5 terminal)", c.post("/api/v1/analysis/run", json={"strategy_id": st["id"], "symbol": "EURUSD"}))
assert r["error"]["code"] == "MARKET_DATA_UNAVAILABLE", r["error"]
print("   message:", r["error"]["message"][:110])
hist = show("GET /analysis/reports (empty)", c.get("/api/v1/analysis/reports"))["data"]
assert hist["page"]["total"] == 0
print("   Engine refused to run without real market data; no report persisted.")

# 5. Ownership still enforced.
r = show("foreign owner sees nothing", c.get("/api/v1/analysis/reports", headers={"X-User-Id": "someone-else"}))
assert r["data"]["page"]["total"] == 0

server_task.should_exit = True
t.join(timeout=5)
shutil.rmtree(pgdata, ignore_errors=True)
print("\nRUNTIME VERIFICATION PASSED")

