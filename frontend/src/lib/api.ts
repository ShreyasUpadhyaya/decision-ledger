import type {
  AuthResponse,
  CandidateRulesResponse,
  CandidateStatus,
  DecisionRequest,
  DecisionResponse,
  ProviderMode,
  ProviderName,
  PublicUser,
  ReadyResponse,
  Role,
  RuleGenerateResponse,
  RuleSearchResponse,
  ShadowMetricsResponse,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

// --- Session token ---------------------------------------------------------
// The JWT from /v1/auth/{login,signup} lives in localStorage and is attached as a
// Bearer header on every protected call. A 401 clears it (see asJson) so a stale or
// expired token can't wedge the app.
const TOKEN_KEY = "decision_ledger_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function asJson<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    // The token is missing/expired/invalid — drop it so the UI falls back to /signin.
    clearToken();
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? body.title ?? `Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

// --- Auth ------------------------------------------------------------------
export function signup(username: string, password: string, role: Role): Promise<AuthResponse> {
  return fetch(`${BASE_URL}/v1/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, role }),
  }).then((r) => asJson<AuthResponse>(r));
}

export function login(username: string, password: string): Promise<AuthResponse> {
  return fetch(`${BASE_URL}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  }).then((r) => asJson<AuthResponse>(r));
}

export function fetchMe(): Promise<PublicUser> {
  return fetch(`${BASE_URL}/v1/auth/me`, { headers: authHeaders() }).then((r) =>
    asJson<PublicUser>(r),
  );
}

export function postDecision(request: DecisionRequest): Promise<DecisionResponse> {
  return fetch(`${BASE_URL}/v1/decisions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
      ...authHeaders(),
    },
    body: JSON.stringify(request),
  }).then((r) => asJson<DecisionResponse>(r));
}

export function listDecisions(limit = 20): Promise<{ decisions: DecisionResponse[] }> {
  return fetch(`${BASE_URL}/v1/decisions?limit=${limit}`, { headers: authHeaders() }).then((r) =>
    asJson<{ decisions: DecisionResponse[] }>(r)
  );
}

export function setProviderMode(provider: ProviderName, mode: ProviderMode): Promise<void> {
  return fetch(`${BASE_URL}/v1/_test/providers/${provider}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ mode }),
  }).then((r) => asJson(r));
}

export function resetProviders(): Promise<void> {
  return fetch(`${BASE_URL}/v1/_test/providers/reset`, {
    method: "POST",
    headers: authHeaders(),
  }).then((r) => asJson(r));
}

export function checkHealth(): Promise<{ status: string }> {
  return fetch(`${BASE_URL}/health`).then((r) => asJson(r));
}

export function checkReady(): Promise<ReadyResponse> {
  return fetch(`${BASE_URL}/ready`).then((r) => asJson<ReadyResponse>(r));
}

export function generateRule(
  text: string,
  opts: { ruleset_id?: string; persist?: boolean } = {},
): Promise<RuleGenerateResponse> {
  return fetch(`${BASE_URL}/v1/rules/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ text, ...opts }),
  }).then((r) => asJson<RuleGenerateResponse>(r));
}

export function searchRules(q: string, k = 6): Promise<RuleSearchResponse> {
  const params = new URLSearchParams({ q, k: String(k) });
  return fetch(`${BASE_URL}/v1/rules/search?${params}`, { headers: authHeaders() }).then((r) =>
    asJson<RuleSearchResponse>(r),
  );
}

// --- Risk-tiered autonomous rule generation --------------------------------
// Tier 3 queue: rules the anomaly trigger classified HIGH risk, waiting on a human.
export function listCandidateRules(status: CandidateStatus | null = "PENDING_REVIEW"): Promise<CandidateRulesResponse> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  return fetch(`${BASE_URL}/v1/candidate-rules?${params}`, { headers: authHeaders() }).then((r) =>
    asJson<CandidateRulesResponse>(r),
  );
}

export function approveCandidateRule(
  candidateId: string,
): Promise<{ candidate_id: string; status: string; ruleset_id: string; rule_id: string }> {
  return fetch(`${BASE_URL}/v1/candidate-rules/${candidateId}/approve`, {
    method: "POST",
    headers: authHeaders(),
  }).then((r) => asJson(r));
}

export function discardCandidateRule(candidateId: string): Promise<{ candidate_id: string; status: string }> {
  return fetch(`${BASE_URL}/v1/candidate-rules/${candidateId}/discard`, {
    method: "POST",
    headers: authHeaders(),
  }).then((r) => asJson(r));
}

// Tier 2: shadow-mode hit counts for one ruleset, and publishing a shadow rule live.
export function getShadowMetrics(rulesetId: string): Promise<ShadowMetricsResponse> {
  return fetch(`${BASE_URL}/v1/rulesets/${rulesetId}/shadow-metrics`, { headers: authHeaders() }).then((r) =>
    asJson<ShadowMetricsResponse>(r),
  );
}

export function publishShadowRule(
  rulesetId: string,
  ruleId: string,
): Promise<{ ruleset_id: string; rule_id: string; is_shadow: boolean }> {
  return fetch(`${BASE_URL}/v1/rulesets/${rulesetId}/rules/${ruleId}/publish`, {
    method: "POST",
    headers: authHeaders(),
  }).then((r) => asJson(r));
}
