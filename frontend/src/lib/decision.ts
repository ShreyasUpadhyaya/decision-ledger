import type { Verdict } from "./types";

export type Severity = "clear" | "caution" | "refer" | "block";

// Four severity buckets, independent of which decision type produced the verdict —
// mirrors the composite aggregator's own classification (app/core/aggregator.py).
export const SEVERITY: Record<Verdict, Severity> = {
  APPROVE: "clear",
  PASS: "clear",
  CLEAR: "clear",
  ANY: "clear",
  APPROVE_WITH_DEPOSIT: "caution",
  SEPA_ONLY: "caution",
  STEP_UP_KYC: "caution",
  DOWNGRADE_OFFER: "caution",
  REFER: "refer",
  REVIEW: "refer",
  PREPAY_ONLY: "refer",
  DECLINE: "block",
  FAIL: "block",
  BLOCK: "block",
};

export function severityOf(verdict: Verdict): Severity {
  return SEVERITY[verdict] ?? "refer";
}

// CSS custom-property token per severity — resolves to the theme-aware verdict palette
// defined in index.css, so a single source of truth drives badges, gauges and the hero.
export const SEVERITY_VAR: Record<Severity, string> = {
  clear: "var(--clear)",
  caution: "var(--caution)",
  refer: "var(--refer)",
  block: "var(--block)",
};

export function colorOf(verdict: Verdict): string {
  return SEVERITY_VAR[severityOf(verdict)];
}

// Tailwind utility bundles for chips/badges, per severity.
export const SEVERITY_BADGE: Record<Severity, string> = {
  clear:
    "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30",
  caution:
    "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/30",
  refer:
    "bg-orange-100 text-orange-800 border-orange-300 dark:bg-orange-500/15 dark:text-orange-300 dark:border-orange-500/30",
  block:
    "bg-red-100 text-red-800 border-red-300 dark:bg-red-500/15 dark:text-red-300 dark:border-red-500/30",
};

export const DECISION_HEADLINE: Record<Severity, string> = {
  clear: "Approved",
  caution: "Approved with conditions",
  refer: "Referred for review",
  block: "Declined",
};

export const DECISION_LABELS: Record<string, string> = {
  DEVICE_FINANCING: "Device financing",
  FRAUD: "Fraud",
  IDENTITY: "Identity",
  TARIFF_ELIGIBILITY: "Tariff eligibility",
  PAYMENT_POLICY: "Payment policy",
};
