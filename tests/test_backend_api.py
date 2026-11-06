"""Backend service contract: boots, serves forecasts, history removed."""

import pytest
from fastapi.testclient import TestClient

from firesky.api import create_app


@pytest.fixture(scope="module")
def client():
    # Server exceptions (job failures) must surface as HTTP 500 here.
    return TestClient(create_app(), raise_server_exceptions=False)


def test_health(client):
    response = client.get("/api/v1/firesky/health")
    assert response.status_code == 200


def test_regional_contract_matches_engine_version(client):
    response = client.get("/api/v1/firesky/forecasts/national/contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "regional"
    assert "2026.11.04.87" in payload["algorithm"]
    assert payload["summary_version"] == 55
    assert payload["cache_version"] == 125


def test_history_and_feedback_endpoints_are_gone(client):
    # The API surface no longer exposes any history/feedback route; unmatched
    # GETs fall through to the SPA, so check the OpenAPI path table.
    paths = set(client.get("/openapi.json").json()["paths"])
    assert not any("history" in path or "feedback" in path for path in paths)
    # POST to an unknown path hits the GET-only SPA catch-all → 405.
    assert client.post("/api/v1/firesky/feedback", json={"forecast_id": 1}).status_code in {404, 405}


def test_outdoor_endpoints_are_gone(client):
    # 404/405 both prove the route is gone (405 = GET-only SPA fallback).
    assert client.post("/api/v1/outdoor/forecast", json={}).status_code in {404, 405}


def test_point_forecast_start_is_async(client):
    """Empty payload starts a job (200 + job_id); the job itself fails later."""
    import time

    response = client.post("/api/v1/firesky/forecasts", json={})
    assert response.status_code == 200
    job_id = response.json().get("job_id")
    assert job_id
    result = None
    for _ in range(100):
        result = client.get(f"/api/v1/firesky/forecasts/results/{job_id}")
        # 404 = job not finished yet; the terminal failure maps to 400/500.
        if result.status_code != 404:
            break
        time.sleep(0.1)
    assert result is not None and result.status_code in {400, 500}
    assert "target_date" in result.json().get("error", "")
