import type { DecisionRequest } from "./types";

const BASE_REQUEST: DecisionRequest = {
  request_id: "req_dashboard",
  market: "DE",
  channel: "ONESHOP_WEB",
  requested_at: "2026-07-24T09:00:00Z",
  customer: {
    customer_id: "cus_dashboard",
    date_of_birth: "1990-01-01",
    is_existing: true,
    tenure_months: 24,
    segment: "CONSUMER",
  },
  order: {
    total_amount: 899.0,
    financed_amount: 899.0,
    term_months: 24,
    device_sku: "SM-A556-128",
    line_count: 1,
    tariff_code: "MAGENTA_M",
    payment_method: "SEPA_DD",
  },
  context: {
    ip_country: "DE",
    shipping_country: "DE",
    device_fingerprint: "fp_dashboard",
    session_age_seconds: 300,
  },
};

// Deep-clone the base request and apply dotted-path overrides, mirroring
// tests/conftest.py's make_request helper so scenarios stay in sync with the test suite.
function build(overrides: Record<string, unknown> = {}): DecisionRequest {
  const request = structuredClone(BASE_REQUEST);
  for (const [dottedPath, value] of Object.entries(overrides)) {
    const parts = dottedPath.split(".");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let node: any = request;
    for (const part of parts.slice(0, -1)) node = node[part];
    node[parts.at(-1)!] = value;
  }
  return request;
}

export interface Scenario {
  id: string;
  label: string;
  description: string;
  request: DecisionRequest;
  testSignals?: Record<string, Record<string, unknown>>;
}

// The three demo scenarios, one per branch of app/decision_service.py's flow:
// demo_rule_hit (a deterministic rule matches directly), demo_llm_fallback (no rule
// matches, so vector search plus the LLM recommend a result), and demo_low_confidence
// (the LLM's confidence falls below the gate, so a safe default is returned). Kept first
// in the list so they're the obvious starting point; the rest of SCENARIOS below are
// general worked examples covering other rule and input combinations.
export const DEMO_SCENARIO_IDS = ["demo_rule_hit", "demo_llm_fallback", "demo_low_confidence"] as const;

export const SCENARIOS: Scenario[] = [
  {
    id: "demo_rule_hit",
    label: "DEMO 1 · Rule hit",
    description:
      "A clean approve the deterministic rule engine matches directly — composite verdict, per-rule trace, no AI involved.",
    request: build({ "customer.tenure_months": 51 }),
    testSignals: { bureau: { score: 780 } },
  },
  {
    id: "demo_llm_fallback",
    label: "DEMO 2 · LLM fallback",
    description:
      "Austria has no configured rules yet, so nothing matches. Vector search finds a similar historical precedent and the LLM recommends a verdict with a confidence score and rationale.",
    request: build({
      market: "AT",
      "order.device_sku": "LAUNCH-PILOT-DEVICE-2027",
      "order.tariff_code": "PILOT_LAUNCH_TARIFF",
      "order.payment_method": "INVOICE",
    }),
  },
  {
    id: "demo_low_confidence",
    label: "DEMO 3 · Safe default",
    description:
      "Same market gap, but this device/tariff combination has no close match in the vector index — the system returns the configured safe default instead of guessing.",
    request: build({ market: "AT", "order.payment_method": "INVOICE" }),
  },
  {
    id: "clean-approve",
    label: "§1.1 · Clean approve",
    description: "Returning customer, 51mo tenure, strong bureau score, modest device.",
    request: build({ "customer.tenure_months": 51 }),
    testSignals: { bureau: { score: 780 } },
  },
  {
    id: "approve-with-deposit",
    label: "§1.2 · Approve with deposit",
    description: "New customer, thin bureau band, high-ticket device — converts instead of losing the sale.",
    request: build({
      "customer.is_existing": false,
      "customer.tenure_months": 0,
      "order.financed_amount": 1099.0,
      "order.total_amount": 1099.0,
    }),
    testSignals: { bureau: { score: 618 }, account_history: { days_past_due: null, sepa_mandate_verified: true } },
  },
  {
    id: "decline-gate",
    label: "§1.3 · Decline — active delinquency",
    description: "78 days past due short-circuits every other rule at the gate.",
    request: build({ "order.financed_amount": 650.0 }),
    testSignals: { account_history: { days_past_due: 78, sepa_mandate_verified: true } },
  },
  {
    id: "decline-terms",
    label: "§1.4 · Decline — accumulated risk",
    description: "No gate fires, but the score still lands below the terms floor.",
    request: build({
      "customer.is_existing": false,
      "customer.tenure_months": 0,
      "order.financed_amount": 1099.0,
    }),
    testSignals: { bureau: { score: 480 }, account_history: { sepa_mandate_verified: false } },
  },
  {
    id: "young-adult-overlay-stack",
    label: "§4.4 · Overlays stack",
    description: "19-year-old, high value + medium risk: both the age cap and the DE deposit cap apply.",
    request: build({
      "customer.is_existing": false,
      "customer.tenure_months": 24,
      "customer.date_of_birth": "2007-03-01",
      "order.financed_amount": 1600.0,
    }),
    testSignals: { bureau: { score: 618 } },
  },
  {
    id: "fraud-review",
    label: "§1.9 · Fraud → review",
    description: "Velocity + multi-address anomaly force manual review, even though financing would approve.",
    request: build({ "customer.tenure_months": 51 }),
    testSignals: {
      bureau: { score: 780 },
      // verdict is set explicitly alongside the raw signals a real fraud
      // provider would return both together; omitting it would leave
      // fraud.verdict absent, which DF-140 (device financing) correctly
      // reads as INDETERMINATE rather than CLEAR — accurate, but a
      // distraction from what this scenario is meant to demonstrate.
      fraud: { verdict: "REVIEW", velocity_orders_40m: 3, shipping_address_count_40m: 2 },
    },
  },
  {
    id: "fraud-block",
    label: "§1.10 · Fraud → block",
    description: "Adds fingerprint reuse on top of the review scenario — crosses the BLOCK threshold.",
    request: build({ "customer.tenure_months": 51 }),
    testSignals: {
      bureau: { score: 780 },
      fraud: {
        verdict: "BLOCK",
        velocity_orders_40m: 3,
        shipping_address_count_40m: 2,
        device_fingerprint_reuse_count: 5,
      },
    },
  },
  {
    id: "identity-step-up",
    label: "§1.6 · Identity → step-up KYC",
    description: "Low face-match score alone isn't disqualifying, but it does require a second check.",
    testSignals: { identity: { match_score: 0.55 } },
    request: build({}),
  },
  {
    id: "identity-fail",
    label: "§1.7 · Identity → fail",
    description: "Low face match plus a failed liveness check crosses the FAIL threshold.",
    testSignals: { identity: { match_score: 0.3, liveness_check: false } },
    request: build({}),
  },
];
