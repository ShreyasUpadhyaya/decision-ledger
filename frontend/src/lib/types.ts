export type Verdict =
  | "APPROVE"
  | "APPROVE_WITH_DEPOSIT"
  | "REFER"
  | "DECLINE"
  | "PASS"
  | "STEP_UP_KYC"
  | "FAIL"
  | "CLEAR"
  | "REVIEW"
  | "BLOCK"
  | "DOWNGRADE_OFFER"
  | "ANY"
  | "SEPA_ONLY"
  | "PREPAY_ONLY";

export type SignalStatus = "OK" | "DEGRADED" | "STALE";

export type TraceOutcome = "TRUE" | "FALSE" | "INDETERMINATE" | "SKIPPED" | "NOT_EVALUATED";

export interface TraceEntry {
  rule_id: string;
  phase: "GATE" | "SCORING" | "TERMS" | "OVERLAY";
  outcome: TraceOutcome;
  score_delta?: number | null;
  skipped_by?: string;
  reason?: string;
}

export interface SubDecision {
  type: string;
  verdict: Verdict;
  pre_overlay_verdict: Verdict | null;
  terms: Record<string, number | string> | null;
  reason_codes: string[];
  matched_rules: string[];
  score: number | null;
  review_task?: { sla_hours: number };
}

export interface Explanation {
  text: string;
  // "heuristic" is the vector-search fallback's non-LLM mode (app/llm/recommender.py's
  // _heuristic()) — distinct from "template", the deterministic-explanation mode
  // (app/llm/explainer.py). Both are real, honest "no live model call" states.
  mode: "llm" | "template" | "heuristic" | "unavailable";
  degraded: boolean;
}

// A similar historical rule surfaced by the vector-search fallback (see ai_assisted below).
export interface SimilarRule {
  ruleset_id: string;
  rule_id: string;
  phase: string | null;
  verdict: string | null;
  reason_codes: string[];
  facts: string[];
  status: string;
  score: number | null;
}

export interface DecisionResponse {
  request_id: string;
  composite_verdict: Verdict;
  terms: Record<string, number | string> | null;
  decisions: SubDecision[];
  signal_confidence: number;
  signal_health: Record<string, SignalStatus>;
  ruleset_versions: Record<string, number>;
  traces: Record<string, TraceEntry[]>;
  decision_id: string;
  replayed: boolean;
  explanation?: Explanation;
  // Present only when the JSON rule engine found no matching rule anywhere for this
  // request and the decision came from the vector-search + LLM fallback instead of a
  // deterministic rule match.
  ai_assisted?: boolean;
  confidence?: number;
  similar_rules?: SimilarRule[];
}

// --- Platform readiness / capabilities -------------------------------------
export interface ReadyResponse {
  status: string;
  rulesets_loaded: number;
  rules_loaded: number;
  llm_enabled: boolean;
  rag_backend: "memory" | "mongodb";
}

// --- LLM: NL -> rule generation --------------------------------------------
export type RuleCondition =
  | { fact: string; op: string; value?: unknown }
  | { all: RuleCondition[] }
  | { any: RuleCondition[] }
  | { none: RuleCondition[] };

export interface GeneratedRule {
  id: string;
  phase: "GATE" | "SCORING" | "TERMS" | "OVERLAY";
  priority: number;
  enabled: boolean;
  when: RuleCondition;
  then: Record<string, unknown>;
}

export interface RuleGenerateResponse {
  rule: GeneratedRule;
  mode: "llm" | "heuristic";
  confidence: number;
  warnings: string[];
  valid: boolean;
  validation_errors: string[];
  source_text: string;
  persisted: boolean;
  ruleset_id: string;
}

export const RULESET_IDS = [
  "de.device-financing",
  "de.fraud",
  "de.identity",
  "de.tariff-eligibility",
  "de.payment-policy",
] as const;

// --- RAG: semantic rule search ---------------------------------------------
export interface RuleSearchResult {
  ruleset_id: string;
  rule_id: string;
  phase: string | null;
  verdict: string | null;
  reason_codes: string[];
  facts: string[];
  score: number | null;
}

export interface RuleSearchResponse {
  query: string;
  backend: "memory" | "mongodb";
  count: number;
  results: RuleSearchResult[];
}

export interface Customer {
  customer_id: string;
  date_of_birth: string;
  is_existing: boolean;
  tenure_months: number;
  segment?: string;
}

export interface Order {
  total_amount: number;
  financed_amount: number;
  term_months: number;
  device_sku: string;
  line_count?: number;
  tariff_code: string;
  payment_method: string;
}

export interface RequestContext {
  ip_country: string;
  shipping_country: string;
  device_fingerprint: string;
  session_age_seconds?: number;
}

export interface DecisionRequest {
  request_id: string;
  market: string;
  channel?: string;
  requested_at: string;
  customer: Customer;
  order: Order;
  context: RequestContext;
  test_signals?: Record<string, Record<string, unknown>>;
}

export type ProviderName = "bureau" | "account_history" | "fraud" | "identity" | "device_catalog";
export type ProviderMode = "OK" | "TIMEOUT" | "ERROR" | "SLOW" | "STALE";

// --- Risk-tiered autonomous rule generation (risk_tiered_architecture.md) --
// Tier 3: a HIGH-risk generated rule waiting on a human before it can ever reach the
// evaluator. Tier 2 (shadow) rules are just ordinary rules with is_shadow: true — no
// separate type, they show up wherever GeneratedRule/rules already do.
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";
export type CandidateStatus = "PENDING_REVIEW" | "APPROVED" | "DISCARDED";

export interface CandidateRule {
  candidate_id: string;
  ruleset_id: string;
  rule: GeneratedRule;
  risk_level: RiskLevel;
  reasoning: string;
  source_text: string | null;
  status: CandidateStatus;
  created_at: string;
  updated_at: string;
}

export interface CandidateRulesResponse {
  candidates: CandidateRule[];
}

// Tier 2: per-rule aggregate of how often a shadow rule *would* have fired, and with
// what verdict, across real traffic it never actually affected.
export interface ShadowRuleMetric {
  rule_id: string;
  total_hits: number;
  by_verdict: Record<string, number>;
}

export interface ShadowMetricsResponse {
  ruleset_id: string;
  rules: ShadowRuleMetric[];
}

// --- Auth ------------------------------------------------------------------
export type Role = "admin" | "user";

export interface PublicUser {
  username: string;
  role: Role;
}

export interface AuthResponse {
  token: string;
  user: PublicUser;
}
