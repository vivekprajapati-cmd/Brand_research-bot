import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from main import api  # noqa: E402

client = TestClient(api)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_slack_events_route_exists():
    response = client.post(
        "/slack/events",
        json={"type": "url_verification", "challenge": "xyz"},
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 401)
