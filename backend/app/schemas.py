"""Request schema at the REST boundary (spec §4.1). Validation happens here, before
anything reaches the orchestrator — a malformed request never gets close to a signal
provider or the evaluator (spec §6.1 of verdict-example-scenarios.md)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Customer(BaseModel):
    customer_id: str
    date_of_birth: str
    is_existing: bool
    tenure_months: int
    segment: str = "CONSUMER"


class Order(BaseModel):
    total_amount: float
    financed_amount: float
    term_months: int
    device_sku: str
    line_count: int = 1
    tariff_code: str
    payment_method: str


class RequestContext(BaseModel):
    ip_country: str
    shipping_country: str
    device_fingerprint: str
    session_age_seconds: int = 0


class DecisionRequest(BaseModel):
    request_id: str
    market: str
    channel: str = "ONESHOP_WEB"
    requested_at: str
    customer: Customer
    order: Order
    context: RequestContext
    test_signals: Optional[dict] = Field(
        default=None,
        description="Test-only per-request signal overrides, e.g. {'bureau': {'score': 618}}.",
    )


class AsyncDecisionRequest(DecisionRequest):
    webhook_url: Optional[str] = None
    callback_headers: Optional[dict] = None


class BatchRequest(BaseModel):
    # Deliberately untyped items: each is validated individually in the route handler
    # so one malformed request can't 422 the whole batch (spec §6.3 / examples doc §6.3).
    requests: list[dict]


class ProviderModeRequest(BaseModel):
    mode: str
    latency_ms: Optional[int] = None


class RulesetVersionRequest(BaseModel):
    """Body for POST /v1/rulesets/{ruleset_id}/versions — everything about a ruleset
    envelope except the server-assigned bookkeeping fields (ruleset_id, version, status,
    content_hash, published_at/by), which the store computes itself so a client can
    never spoof or collide them."""

    decision_type: str
    market: Optional[str] = None
    score_fact: Optional[str] = None
    verdict_severity: list[str] = Field(default_factory=list)
    default_outcome: dict
    composite_class: dict[str, str]
    rules: list[dict]
    default_terms_by_verdict: Optional[dict] = None


class RuleGenerateRequest(BaseModel):
    """NL -> rule generation (spec §14.2).

    When ``persist`` is true (the default) the generated rule is written into the vector
    store under ``ruleset_id`` and goes live immediately — searchable and used in the next
    decision. Set ``persist`` false to preview a rule without storing it.
    """

    text: str = Field(..., min_length=3, description="Business policy in plain English.")
    ruleset_id: str = Field(
        default="de.device-financing",
        description="Target ruleset the generated rule is stored into and evaluated within.",
    )
    persist: bool = Field(default=True, description="Store the rule in the vector DB and make it live.")


# --- Auth (spec: MongoDB-backed users + JWT sessions) ------------------------
class SignupRequest(BaseModel):
    username: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    role: Literal["admin", "user"]


class LoginRequest(BaseModel):
    username: str
    password: str


class PublicUser(BaseModel):
    """The only user shape ever returned to a client — never the hash or Mongo _id."""

    username: str
    role: str


class AuthResponse(BaseModel):
    token: str
    user: PublicUser
