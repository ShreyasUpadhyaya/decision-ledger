import pytest
import httpx
from unittest.mock import patch

from app.adapters.bureau_adapter import fetch_bureau
from app.config import settings

def test_fetch_bureau_success():
    settings.bureau_api_url = "https://mock.bureau/api/v1/score"
    settings.bureau_api_key = "test_key"
    
    with patch("httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"score": 750}
        
        result = fetch_bureau("cust-123")
        
        assert result["status"] == "OK"
        assert result["data"]["score"] == 750
        assert not result["fallback_applied"]

def test_fetch_bureau_timeout():
    settings.bureau_api_url = "https://mock.bureau/api/v1/score"
    
    with patch("httpx.get", side_effect=httpx.TimeoutException("Timeout")):
        result = fetch_bureau("cust-123")
        
        assert result["status"] == "TIMEOUT"
        assert result["data"]["score"] is None
        assert result["fallback_applied"]

def test_fetch_bureau_no_url_configured():
    settings.bureau_api_url = ""
    
    result = fetch_bureau("cust-123")
    assert result is None
