import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/dashboard/theme-toggle";
import { postDecision } from "@/lib/api";
import { colorOf, severityOf, DECISION_HEADLINE } from "@/lib/decision";
import type { DecisionRequest, DecisionResponse } from "@/lib/types";
import { useAuth } from "@/lib/auth";

// Form-field shape mirrors ScenarioPicker's BASE_REQUEST (src/lib/scenarios.ts) so it
// stays aligned with the DecisionRequest the backend expects — no invented fields.
interface OrderForm {
  // customer
  customer_id: string;
  date_of_birth: string;
  is_existing: boolean;
  tenure_months: number;
  segment: string;
  // order
  total_amount: number;
  financed_amount: number;
  term_months: number;
  device_sku: string;
  line_count: number;
  tariff_code: string;
  payment_method: string;
  // context
  ip_country: string;
  shipping_country: string;
  device_fingerprint: string;
  session_age_seconds: number;
}

const DEFAULTS: OrderForm = {
  customer_id: "cus_order",
  date_of_birth: "1990-01-01",
  is_existing: true,
  tenure_months: 24,
  segment: "CONSUMER",
  total_amount: 899,
  financed_amount: 899,
  term_months: 24,
  device_sku: "SM-A556-128",
  line_count: 1,
  tariff_code: "MAGENTA_M",
  payment_method: "SEPA_DD",
  ip_country: "DE",
  shipping_country: "DE",
  device_fingerprint: "fp_order",
  session_age_seconds: 300,
};

function toRequest(form: OrderForm): DecisionRequest {
  return {
    request_id: `req_${crypto.randomUUID().slice(0, 8)}`,
    market: "DE",
    channel: "ONESHOP_WEB",
    requested_at: new Date().toISOString(),
    customer: {
      customer_id: form.customer_id,
      date_of_birth: form.date_of_birth,
      is_existing: form.is_existing,
      tenure_months: form.tenure_months,
      segment: form.segment,
    },
    order: {
      total_amount: form.total_amount,
      financed_amount: form.financed_amount,
      term_months: form.term_months,
      device_sku: form.device_sku,
      line_count: form.line_count,
      tariff_code: form.tariff_code,
      payment_method: form.payment_method,
    },
    context: {
      ip_country: form.ip_country,
      shipping_country: form.shipping_country,
      device_fingerprint: form.device_fingerprint,
      session_age_seconds: form.session_age_seconds,
    },
  };
}

// Friendly, non-technical copy per severity bucket.
const RESULT_COPY: Record<ReturnType<typeof severityOf>, string> = {
  clear: "Your order is approved. You're all set to continue.",
  caution: "Your order is approved with a few conditions — see the details below.",
  refer: "We need a quick manual review before we can confirm this order.",
  block: "We're unable to approve this order right now.",
};

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

export function OrderPage() {
  const { user, signout } = useAuth();
  const [form, setForm] = useState<OrderForm>(DEFAULTS);
  const [result, setResult] = useState<DecisionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof OrderForm>(key: K, value: OrderForm[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  // Keeps number inputs from producing NaN when cleared.
  function num(value: string): number {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await postDecision(toRequest(form));
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-svh cockpit-bg">
      {/* ---------------- Header ---------------- */}
      <header className="sticky top-0 z-30 border-b border-border/60 bg-background/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[900px] flex-wrap items-center gap-4 px-6 py-3">
          <div className="flex items-center gap-3">
            <BrandMark className="h-9 w-9" />
            <div>
              <h1 className="text-lg font-bold leading-none">
                <span className="brand-gradient-text">DecisionLedger</span>
              </h1>
              <p className="mt-0.5 text-[0.68rem] text-muted-foreground">New order request</p>
            </div>
          </div>

          <div className="ml-auto flex items-center gap-3">
            {user && (
              <span className="hidden text-sm text-muted-foreground sm:inline">
                Signed in as <span className="font-semibold text-foreground">{user.username}</span>
              </span>
            )}
            <ThemeToggle />
            <Button variant="outline" size="sm" onClick={signout}>
              Sign Out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[900px] px-6 py-6">
        <div className="mb-5">
          <h2 className="text-xl font-bold">Build a decision request</h2>
          <p className="text-sm text-muted-foreground">
            Fill in the customer, order, and context details, then submit to get an instant verdict.
          </p>
        </div>

        {result && <ResultCard result={result} />}

        {error && (
          <Alert variant="destructive" className="mb-5">
            <AlertTitle>Request failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Customer */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Customer</CardTitle>
              <CardDescription>Who is placing the order.</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Customer ID" htmlFor="customer_id">
                <Input
                  id="customer_id"
                  value={form.customer_id}
                  onChange={(e) => set("customer_id", e.target.value)}
                />
              </Field>
              <Field label="Date of birth" htmlFor="date_of_birth">
                <Input
                  id="date_of_birth"
                  type="date"
                  value={form.date_of_birth}
                  onChange={(e) => set("date_of_birth", e.target.value)}
                />
              </Field>
              <Field label="Existing customer" htmlFor="is_existing">
                <Select
                  value={form.is_existing ? "yes" : "no"}
                  onValueChange={(v) => set("is_existing", v === "yes")}
                >
                  <SelectTrigger id="is_existing" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="yes">Yes</SelectItem>
                    <SelectItem value="no">No</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              {/* <Field label="Tenure (months)" htmlFor="tenure_months">
                <Input
                  id="tenure_months"
                  type="number"
                  min={0}
                  value={form.tenure_months}
                  onChange={(e) => set("tenure_months", num(e.target.value))}
                />
              </Field> */}
              <Field label="Segment" htmlFor="segment">
                <Select value={form.segment} onValueChange={(v) => set("segment", v)}>
                  <SelectTrigger id="segment" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CONSUMER">Consumer</SelectItem>
                    <SelectItem value="BUSINESS">Business</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </CardContent>
          </Card>

          {/* Order */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Order</CardTitle>
              <CardDescription>The device and financing terms.</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Total amount (€)" htmlFor="total_amount">
                <Input
                  id="total_amount"
                  type="number"
                  min={0}
                  step="0.01"
                  value={form.total_amount}
                  onChange={(e) => set("total_amount", num(e.target.value))}
                />
              </Field>
              <Field label="Financed amount (€)" htmlFor="financed_amount">
                <Input
                  id="financed_amount"
                  type="number"
                  min={0}
                  step="0.01"
                  value={form.financed_amount}
                  onChange={(e) => set("financed_amount", num(e.target.value))}
                />
              </Field>
              <Field label="Term (months)" htmlFor="term_months">
                <Input
                  id="term_months"
                  type="number"
                  min={0}
                  value={form.term_months}
                  onChange={(e) => set("term_months", num(e.target.value))}
                />
              </Field>
              <Field label="Lines" htmlFor="line_count">
                <Input
                  id="line_count"
                  type="number"
                  min={0}
                  value={form.line_count}
                  onChange={(e) => set("line_count", num(e.target.value))}
                />
              </Field>
              <Field label="Device SKU" htmlFor="device_sku">
                <Input
                  id="device_sku"
                  value={form.device_sku}
                  onChange={(e) => set("device_sku", e.target.value)}
                />
              </Field>
              <Field label="Tariff code" htmlFor="tariff_code">
                <Input
                  id="tariff_code"
                  value={form.tariff_code}
                  onChange={(e) => set("tariff_code", e.target.value)}
                />
              </Field>
              <Field label="Payment method" htmlFor="payment_method">
                <Select
                  value={form.payment_method}
                  onValueChange={(v) => set("payment_method", v)}
                >
                  <SelectTrigger id="payment_method" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="SEPA_DD">SEPA direct debit</SelectItem>
                    <SelectItem value="CARD">Card</SelectItem>
                    <SelectItem value="INVOICE">Invoice</SelectItem>
                    <SelectItem value="PREPAY">Prepay</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </CardContent>
          </Card>

          {/* Context */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Context</CardTitle>
              <CardDescription>Where the request is coming from.</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="IP country" htmlFor="ip_country">
                <Input
                  id="ip_country"
                  value={form.ip_country}
                  onChange={(e) => set("ip_country", e.target.value.toUpperCase())}
                />
              </Field>
              <Field label="Shipping country" htmlFor="shipping_country">
                <Input
                  id="shipping_country"
                  value={form.shipping_country}
                  onChange={(e) => set("shipping_country", e.target.value.toUpperCase())}
                />
              </Field>
              <Field label="Device fingerprint" htmlFor="device_fingerprint">
                <Input
                  id="device_fingerprint"
                  value={form.device_fingerprint}
                  onChange={(e) => set("device_fingerprint", e.target.value)}
                />
              </Field>
              <Field label="Session age (seconds)" htmlFor="session_age_seconds">
                <Input
                  id="session_age_seconds"
                  type="number"
                  min={0}
                  value={form.session_age_seconds}
                  onChange={(e) => set("session_age_seconds", num(e.target.value))}
                />
              </Field>
            </CardContent>
          </Card>

          <div className="flex items-center gap-3">
            <Button type="submit" size="lg" disabled={loading}>
              {loading ? "Evaluating…" : "Submit order"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="lg"
              disabled={loading}
              onClick={() => {
                setForm(DEFAULTS);
                setResult(null);
                setError(null);
              }}
            >
              Reset
            </Button>
          </div>
        </form>
      </main>
    </div>
  );
}

function ResultCard({ result }: { result: DecisionResponse }) {
  const severity = severityOf(result.composite_verdict);
  const color = colorOf(result.composite_verdict);
  const headline = DECISION_HEADLINE[severity];

  // Collect unique, human-ish reason codes across every sub-decision.
  const reasons = Array.from(new Set(result.decisions.flatMap((d) => d.reason_codes)));

  return (
    <Card className="relative glass glow-ring animate-reveal mb-6 gap-3">
      <span className="absolute inset-x-0 top-0 h-1" style={{ background: color }} />
      <CardHeader>
        <div className="flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-muted-foreground">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
          Your verdict
        </div>
        <CardTitle className="mt-1 text-3xl font-bold tracking-tight" style={{ color }}>
          {headline}
        </CardTitle>
        <CardDescription className="text-sm">{RESULT_COPY[severity]}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {result.explanation?.text && (
          <p className="text-sm text-foreground/90">{result.explanation.text}</p>
        )}

        {result.terms && Object.keys(result.terms).length > 0 && (
          <div className="flex flex-wrap gap-2">
            {Object.entries(result.terms).map(([key, value]) => (
              <div
                key={key}
                className="rounded-lg border border-border bg-background/40 px-3 py-1.5 text-sm"
              >
                <span className="text-muted-foreground">{key.replaceAll("_", " ")}</span>{" "}
                <span className="font-mono font-semibold text-foreground">{String(value)}</span>
              </div>
            ))}
          </div>
        )}

        {reasons.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Why
            </p>
            <div className="flex flex-wrap gap-1.5">
              {reasons.map((code) => (
                <Badge key={code} variant="secondary" className="font-normal">
                  {code.replaceAll("_", " ").toLowerCase()}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
