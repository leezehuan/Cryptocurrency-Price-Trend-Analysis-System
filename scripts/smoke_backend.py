from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.database import init_db
from backend.app.main import app


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
]

POST_ENDPOINTS = [
    "/api/predictions/verify-due",
]


def assert_ok(path: str, status_code: int) -> None:
    if status_code < 200 or status_code >= 300:
        raise AssertionError(f"{path} returned HTTP {status_code}")


def main() -> None:
    init_db()
    client = TestClient(app)
    analysts_response = client.get("/api/analysts")
    assert_ok("/api/analysts", analysts_response.status_code)
    for path in GET_ENDPOINTS:
        response = client.get(path)
        assert_ok(path, response.status_code)
        print(f"GET {path} -> {response.status_code}")
    for path in POST_ENDPOINTS:
        response = client.post(path)
        assert_ok(path, response.status_code)
        print(f"POST {path} -> {response.status_code}")
    print("backend smoke ok")


if __name__ == "__main__":
    main()
