from __future__ import annotations


def test_live_health_does_not_require_auth(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
