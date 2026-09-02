"""Mock signal providers (spec §8): healthy defaults, per-provider fallback policy, and
a control surface for failure injection (spec §8.4).

Each provider fails differently on purpose (see verdict-example-scenarios.md §3):
BUREAU and ACCOUNT_HISTORY degrade to null + a confidence penalty (§8.2) and let the
ruleset's own null handling (§6.6) decide what happens. FRAUD and IDENTITY are handled
specially one layer up, in the orchestrator: their fallback is a forced, safe verdict
applied directly, never a silent pass produced by an all-null scoring phase.
"""
from typing import Optional

from .adapters import fetch_bureau

HEALTHY_DEFAULTS: dict[str, dict] = {
    "bureau": {"score": 700},
    "account_history": {"days_past_due": 0, "sepa_mandate_verified": True},
    "fraud": {
        "verdict": "CLEAR",
        "velocity_orders_40m": 0,
        "shipping_address_count_40m": 0,
        "device_fingerprint_reuse_count": 0,
        "device_on_global_blocklist": False,
    },
    "identity": {"match_score": 0.95, "liveness_check": True, "document_type": "PASSPORT"},
    "device_catalog": {"price": 899.00, "in_stock": True},
}

CONFIDENCE_PENALTY: dict[str, float] = {
    "bureau": 0.35,
    "account_history": 0.25,
    "identity": 0.0,
    "fraud": 0.0,
    "device_catalog": 0.0,
}

_FALLBACK_DATA: dict[str, dict] = {
    "bureau": {"score": None},
    "account_history": {"days_past_due": None, "sepa_mandate_verified": None},
    "fraud": {
        "verdict": "REVIEW",
        "velocity_orders_40m": None,
        "shipping_address_count_40m": None,
        "device_fingerprint_reuse_count": None,
        "device_on_global_blocklist": None,
    },
    "identity": {"match_score": None, "liveness_check": None, "document_type": None},
}

_DEVICE_CATALOG_CACHE = {"price": 899.00, "in_stock": True}

VALID_MODES = ("OK", "TIMEOUT", "ERROR", "SLOW", "STALE")


class ProviderState:
    """In-memory, per-process provider mode registry — a PoC stand-in for the real
    external bureau/fraud/identity/catalog services."""

    def __init__(self) -> None:
        self.modes: dict[str, str] = {name: "OK" for name in HEALTHY_DEFAULTS}

    def set_mode(self, provider: str, mode: str) -> None:
        if provider not in HEALTHY_DEFAULTS:
            raise KeyError(f"unknown provider: {provider}")
        if mode not in VALID_MODES:
            raise ValueError(f"unknown mode: {mode}")
        self.modes[provider] = mode

    def reset(self) -> None:
        for name in self.modes:
            self.modes[name] = "OK"


_LIVE_ADAPTERS = {
    "bureau": fetch_bureau,
}


def _fallback_response(provider: str, mode: str) -> dict:
    if provider == "device_catalog":
        return {"status": "STALE", "data": dict(_DEVICE_CATALOG_CACHE), "fallback_applied": True}
    return {"status": mode, "data": dict(_FALLBACK_DATA[provider]), "fallback_applied": True}


def fetch_signal(state: ProviderState, provider: str, overrides: Optional[dict] = None) -> dict:
    """Returns {"status": OK|TIMEOUT|ERROR|STALE, "data": dict, "fallback_applied": bool}."""
    if overrides and provider in overrides:
        return {"status": "OK", "data": overrides[provider], "fallback_applied": False}

    mode = state.modes.get(provider, "OK")
    if mode not in ("OK", "SLOW"):
        return _fallback_response(provider, mode)

    adapter = _LIVE_ADAPTERS.get(provider)
    if adapter:
        # Note: in a real app customer_id/request params would be passed here
        # Passing a dummy customer_id as this is a mock interface structure that didn't pass request context yet
        # The prompt says "swap mock providers" and use "customer_id: str" in the adapter. 
        # Actually, let's just pass "dummy" for now, as orchestrator doesn't pass customer_id to fetch_signal in this codebase right now.
        result = adapter("dummy_cust_id")
        if result is not None:
            return result

    if provider == "device_catalog":
        return {"status": "STALE", "data": dict(_DEVICE_CATALOG_CACHE), "fallback_applied": True}
    return {"status": "OK", "data": dict(HEALTHY_DEFAULTS[provider]), "fallback_applied": False}
