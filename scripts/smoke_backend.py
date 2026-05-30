from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import database
from backend.app.agent_tools import list_agent_tools
from backend.app.config_loader import load_prompts_config, load_skills_config
from backend.app.llm_client import get_node_prompt, standard_node_output


GET_ENDPOINTS = [
    "/api/health",
    "/api/dashboard",
    "/api/market?interval=1h&limit=5",
    "/api/market/summary?interval=1h",
    "/api/market/live-price",
    "/api/settings",
    "/api/analysts",
    "/api/opinions",
    "/api/predictions",
    "/api/agent/runs",
    "/api/agent/stream-events?limit=5",
    "/api/reviews?status=pending",
    "/api/reports",
    "/api/verification-results",
    "/api/scheduler/status",
    "/api/scheduler/runs?limit=5",
    "/api/sources/gate/status",
    "/api/sources/gate/accounts",
    "/api/market/btc-contract",
    "/api/square/hot",
    "/api/square/user-opinions",
    "/api/sentiment/market",
    "/api/memory",
    "/api/mock-trade/account",
    "/api/mock-trade/positions",
    "/api/mock-trade/trades",
]

POST_ENDPOINTS = [
    "/api/predictions/verify-due",
    "/api/mock-trade/advice",
    "/api/mock-trade/sync",
]

REQUIRED_ROUTES = [
    "/api/sources/gate/status",
    "/api/sources/gate/accounts",
    "/api/agent/runs/{run_id}/evidence",
    "/api/agent/runs/{run_id}/reflection",
]

REQUIRED_SKILLS = {
    "market_sentiment_analysis",
    "evidence_conflict_judgement",
    "memory_summarization",
    "reflection_critique",
}

REQUIRED_TOOLS = {
    "gate_market_research",
    "gate_info_research",
    "gate_news_research",
    "gate_square_research",
    "market_memory_search",
}


def assert_ok(path: str, status_code: int) -> None:
    if status_code < 200 or status_code >= 300:
        raise AssertionError(f"{path} returned HTTP {status_code}")


def assert_true(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)


def fake_llm_node(graph_name: str, node_name: str, values: dict[str, object]) -> dict[str, object]:
    return standard_node_output(node_name, success=False, errors=["smoke llm disabled"])


def validate_prompts_and_tools() -> None:
    prompts = load_prompts_config()
    skills = load_skills_config()
    assert_true("skills loaded", REQUIRED_SKILLS.issubset(set(skills)))
    assert_true("skills removed from prompts", "skills" not in (prompts.get("graphs") or {}))
    assert_true("skill prompt lookup", bool(get_node_prompt("skills", "reflection_critique").get("output_schema")))
    tools = list_agent_tools()
    tool_names = {item.get("name") for item in tools}
    assert_true("agent tools metadata", REQUIRED_TOOLS.issubset(tool_names))
    assert_true("tool prompts present", all(item.get("prompt") for item in tools))


def validate_routes(app: object) -> None:
    routes = {route.path for route in app.routes}
    for path in REQUIRED_ROUTES:
        assert_true(f"route exists {path}", path in routes)


def patch_external_calls() -> None:
    from backend.app import graph_nodes
    from backend.app import main as main_module

    graph_nodes.call_llm_node = fake_llm_node
    main_module.live_market_price = lambda conn, symbol="BTCUSDT", market_type="perpetual": {
        "symbol": symbol,
        "market_type": market_type,
        "price": 65000.0,
        "funding_rate": None,
        "source": "smoke_stub",
        "fetched_at": database.utc_now(),
    }


def validate_agent_and_reports(client: TestClient) -> None:
    response = client.post("/api/agent/run", json={"trigger": "smoke"})
    assert_ok("/api/agent/run", response.status_code)
    payload = response.json()
    run_id = payload.get("id")
    assert_true("agent run id", isinstance(run_id, int))
    output = payload.get("output") or {}
    assert_true("agent evidence conflict output", "evidence_conflict" in output)
    assert_true("agent reflection output", "reflection" in output)
    for suffix in ("replay", "evidence", "reflection"):
        path = f"/api/agent/runs/{run_id}/{suffix}"
        detail = client.get(path)
        assert_ok(path, detail.status_code)
    report = client.post("/api/reports/daily")
    assert_ok("/api/reports/daily", report.status_code)
    report_data = (report.json().get("data") or {})
    assert_true("daily report contract field", "contract_status" in report_data)


def validate_mock_trade(client: TestClient) -> None:
    account = client.get("/api/mock-trade/account")
    assert_ok("/api/mock-trade/account", account.status_code)
    account_data = account.json()
    assert_true("mock account id", isinstance(account_data.get("account", {}).get("id"), int))
    execute = client.post("/api/mock-trade/execute", json={"direction": "long", "size": 1, "price_type": "market"})
    assert_ok("/api/mock-trade/execute", execute.status_code)
    print("mock_trade smoke ok")


def validate_http_endpoints(client: TestClient) -> None:
    for path in GET_ENDPOINTS:
        response = client.get(path)
        assert_ok(path, response.status_code)
        print(f"GET {path} -> {response.status_code}")
    for path in POST_ENDPOINTS:
        response = client.post(path)
        assert_ok(path, response.status_code)
        print(f"POST {path} -> {response.status_code}")


def main() -> None:
    validate_prompts_and_tools()
    with tempfile.TemporaryDirectory() as temp_dir:
        database.DB_PATH = Path(temp_dir) / "smoke.sqlite3"
        database.init_db()
        from backend.app.main import app

        patch_external_calls()
        validate_routes(app)
        client = TestClient(app)
        validate_http_endpoints(client)
        validate_agent_and_reports(client)
        validate_mock_trade(client)
    print("backend smoke ok")


if __name__ == "__main__":
    main()
