import httpx

from ..config import settings
from ..logging_config import get_logger

logger = get_logger("decision_ledger.adapters.bureau")

FALLBACK = {"score": None}


def fetch_bureau(customer_id: str) -> dict:
    """Call the external credit bureau API."""
    if not settings.bureau_api_url:
        return None  # No URL configured -> fall back to mock in provider

    try:
        resp = httpx.get(
            settings.bureau_api_url,
            params={"customer_id": customer_id},
            headers={"Authorization": f"Bearer {settings.bureau_api_key}"},
            timeout=settings.bureau_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "adapter.bureau.success",
            extra={"event": "adapter.bureau.success", "customer_id": customer_id}
        )
        return {"status": "OK", "data": {"score": data.get("score")}, "fallback_applied": False}
    except httpx.TimeoutException:
        logger.warning(
            "adapter.bureau.timeout",
            extra={"event": "adapter.bureau.timeout", "customer_id": customer_id}
        )
        return {"status": "TIMEOUT", "data": dict(FALLBACK), "fallback_applied": True}
    except Exception as exc:
        logger.warning(
            "adapter.bureau.error",
            extra={"event": "adapter.bureau.error", "customer_id": customer_id, "error": str(exc)}
        )
        return {"status": "ERROR", "data": dict(FALLBACK), "fallback_applied": True}
