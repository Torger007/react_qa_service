from __future__ import annotations


def test_health_endpoint_returns_service_status(client, admin_token):
    resp = client.get(
        "/api/v1/ops/health",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "service" in data
    assert "environment" in data


def test_readiness_endpoint_reports_checks_and_warnings(client, admin_token):
    resp = client.get(
        "/api/v1/ops/readiness",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"ready", "degraded"}
    assert data["checks"]["redis"] == "ok"
    assert data["checks"]["vector_store"] == "ok"
    assert isinstance(data["warnings"], list)
