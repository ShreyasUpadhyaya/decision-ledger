import pytest
from unittest.mock import patch

from tests.conftest import user_headers as make_user_headers


def test_async_decision_flow(app_client):
    user_headers = make_user_headers()
    # 1. Start a job
    req = {
        "request_id": "test-req-123",
        "market": "DE",
        "requested_at": "2026-07-24T12:00:00Z",
        "customer": {
            "customer_id": "cust-1",
            "date_of_birth": "1990-01-01",
            "is_existing": False,
            "tenure_months": 0
        },
        "order": {
            "total_amount": 1000.0,
            "financed_amount": 1000.0,
            "term_months": 24,
            "device_sku": "TEST-SKU",
            "tariff_code": "TEST-TARIFF",
            "payment_method": "INVOICE"
        },
        "context": {
            "ip_country": "DE",
            "shipping_country": "DE",
            "device_fingerprint": "abc"
        }
    }
    
    resp = app_client.post("/v1/decisions/async", json=req, headers=user_headers)
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "PENDING"
    
    job_id = data["job_id"]
    
    resp = app_client.get(f"/v1/decisions/async/{job_id}", headers=user_headers)
    assert resp.status_code == 200
    job_data = resp.json()
    assert job_data["job_id"] == job_id
    # Starlette's TestClient runs BackgroundTasks synchronously before the response is
    # even returned, so by the time we poll, the job has very likely already finished —
    # assert it reached a valid state rather than assuming PENDING specifically.
    assert job_data["status"] in ("PENDING", "COMPLETED")
    if job_data["status"] == "COMPLETED":
        assert job_data["result"] is not None
    
    resp = app_client.get("/v1/decisions/async", headers=user_headers)
    assert resp.status_code == 200
    list_data = resp.json()
    assert len(list_data["jobs"]) >= 1
    assert any(j["job_id"] == job_id for j in list_data["jobs"])
