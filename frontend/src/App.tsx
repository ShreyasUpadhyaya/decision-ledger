import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import { ScenarioPicker } from "@/components/dashboard/scenario-picker";
import { FailureInjectionPanel } from "@/components/dashboard/failure-injection-panel";
import { RecentDecisions } from "@/components/dashboard/recent-decisions";
import { DecisionHero } from "@/components/dashboard/decision-hero";
import { AiExplanationPanel } from "@/components/dashboard/ai-explanation-panel";
import { DecisionCard } from "@/components/dashboard/decision-card";
import { TracePipeline } from "@/components/dashboard/trace-pipeline";
import { SignalHealthPanel } from "@/components/dashboard/signal-health-panel";
import { RuleStudio } from "@/components/dashboard/rule-studio";
import { CandidateRulesPanel } from "@/components/dashboard/candidate-rules-panel";
import { ShadowMetricsPanel } from "@/components/dashboard/shadow-metrics-panel";
import { ThemeToggle } from "@/components/dashboard/theme-toggle";
import { Button } from "@/components/ui/button";
import { checkReady, listDecisions, postDecision } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DecisionRequest, DecisionResponse, ReadyResponse } from "@/lib/types";

type View = "cockpit" | "studio" | "governance";

// True once every decision type the request touched fell through to its ruleset's
// default_outcome — the JSON rule engine matched nothing anywhere. Mirrors
// app/decision_service.py's _all_unmatched(). When this is true and ai_assisted is
// false, the composite verdict is the configured safe default, not an ordinary rule
// match — the AI explanation panel needs to say so plainly instead of narrating a
// generic (and potentially misleading) rationale for a decision no rule actually made.
function noRuleMatchedAnywhere(decision: DecisionResponse): boolean {
  if (decision.decisions.length === 0) return true;
  return decision.decisions.every((d) => d.matched_rules.length === 0);
}

function CapabilityPill({ tone, label, value }: { tone: "on" | "off" | "info"; label: string; value: string }) {
  const dot = tone === "on" ? "bg-emerald-500" : tone === "off" ? "bg-red-500" : "bg-primary";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-card/50 px-3 py-1.5">
      <span className={cn("size-2 rounded-full", dot, tone !== "off" && "animate-pulse")} />
      <span className="text-[0.68rem] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-xs font-semibold">{value}</span>
    </div>
  );
}

function App() {
  const { user, signout } = useAuth();
  const [view, setView] = useState<View>("cockpit");
  const [ready, setReady] = useState<ReadyResponse | null>(null);
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  const [history, setHistory] = useState<DecisionResponse[]>([]);
  const [current, setCurrent] = useState<DecisionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshHistory = useCallback(() => {
    listDecisions()
      .then((res) => setHistory(res.decisions))
      .catch(() => void 0);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    // Poll until the API answers, then stop. This keeps the "Can't reach the API" banner
    // from sticking while the backend is still booting (ChromaDB seed can take ~10s).
    const probe = () => {
      checkReady()
        .then((r) => {
          if (cancelled) return;
          setReady(r);
          setBackendUp(true);
          refreshHistory();
        })
        .catch(() => {
          if (cancelled) return;
          setBackendUp(false);
          timer = setTimeout(probe, 2500);
        });
    };
    probe();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [refreshHistory]);

  async function handleSubmit(request: DecisionRequest) {
    setLoading(true);
    setError(null);
    try {
      const result = await postDecision(request);
      setCurrent(result);
      setView("cockpit");
      refreshHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-svh cockpit-bg">
      {/* ---------------- Header ---------------- */}
      <header className="sticky top-0 z-30 border-b border-border/60 bg-background/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-4 px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-primary-foreground shadow-[0_0_20px_-4px_var(--brand-glow)]">
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 12l5 5L20 6" />
              </svg>
            </div>
            <div>
              <h1 className="text-lg font-bold leading-none">
                <span className="brand-gradient-text">DecisionLedger</span>
              </h1>
              <p className="mt-0.5 text-[0.68rem] text-muted-foreground">Decision Automation Cockpit · DTDL</p>
            </div>
          </div>

          {/* view switch */}
          <nav className="ml-2 flex items-center gap-1 rounded-xl border border-border bg-card/50 p-1">
            {(["cockpit", "studio", "governance"] as View[]).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs font-semibold transition-all",
                  view === v ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {v === "cockpit" ? "Decision Cockpit" : v === "studio" ? "Rule Studio" : "Governance"}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <div className="hidden items-center gap-2 md:flex">
              <CapabilityPill
                tone={backendUp ? "on" : backendUp === false ? "off" : "info"}
                label="API"
                value={backendUp === null ? "…" : backendUp ? "online" : "offline"}
              />
              {ready && (
                <>
                  <CapabilityPill tone="info" label="AI" value={ready.llm_enabled ? "LLM live" : "Deterministic"} />
                  <CapabilityPill tone="info" label="Vector DB" value={`${ready.rag_backend} · ${ready.rules_loaded}`} />
                </>
              )}
            </div>
            <ThemeToggle />
            {user && (
              <span className="hidden text-sm text-muted-foreground lg:inline">
                <span className="font-semibold text-foreground">{user.username}</span>
              </span>
            )}
            <Button variant="outline" size="sm" onClick={signout}>
              Sign Out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-6 py-6">
        {backendUp === false && (
          <Alert variant="destructive" className="mb-6">
            <AlertTitle>Can't reach the API</AlertTitle>
            <AlertDescription>
              Start it with <code className="font-mono">uvicorn app.main:app --reload</code> from the repo root, then reload this page.
            </AlertDescription>
          </Alert>
        )}

        {view === "studio" ? (
          <div className="animate-fade-up space-y-4">
            <div>
              <h2 className="text-xl font-bold">Rule Studio</h2>
              <p className="text-sm text-muted-foreground">
                Author rules from plain English and search the active policy semantically — powered by the platform's LLM &amp; RAG layer,
                with deterministic fallbacks so it works with no API key.
              </p>
            </div>
            <RuleStudio />
          </div>
        ) : view === "governance" ? (
          <div className="animate-fade-up space-y-4">
            <div>
              <h2 className="text-xl font-bold">Governance</h2>
              <p className="text-sm text-muted-foreground">
                Autonomous rule generation, risk-tiered (risk_tiered_architecture.md): LOW risk auto-enforces
                instantly, MEDIUM risk dark-launches in shadow mode until proven safe, HIGH risk waits here for a
                human.
              </p>
            </div>
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <CandidateRulesPanel />
              <ShadowMetricsPanel />
            </div>
          </div>
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[340px_1fr]">
              {/* ---------------- Left rail ---------------- */}
              <div className="space-y-4">
                <ScenarioPicker onSubmit={handleSubmit} loading={loading} />
                <FailureInjectionPanel onChanged={() => void 0} />
                <RecentDecisions decisions={history} selectedId={current?.decision_id} onSelect={setCurrent} onRefresh={refreshHistory} />
              </div>

              {/* ---------------- Main ---------------- */}
              <div className="space-y-5">
                {error && (
                  <Alert variant="destructive">
                    <AlertTitle>Request failed</AlertTitle>
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                )}

                {!current && !error && (
                  <div className="grid place-items-center rounded-2xl border border-dashed border-border glass py-20 text-center">
                    <div className="max-w-sm px-6">
                      <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-primary/15 text-primary">
                        <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M4 12l5 5L20 6" />
                        </svg>
                      </div>
                      <h3 className="mt-4 text-lg font-semibold">Run a decision to light up the cockpit</h3>
                      <p className="mt-1 text-sm text-muted-foreground">
                        Pick a scenario on the left — you'll see the composite verdict, an AI explanation, the per-domain breakdown, and the full
                        4-phase rule trace.
                      </p>
                    </div>
                  </div>
                )}

                {current && (
                  <>
                    <DecisionHero response={current} />
                    <AiExplanationPanel
                      explanation={current.explanation}
                      aiAssisted={current.ai_assisted}
                      confidence={current.confidence}
                      similarRules={current.similar_rules}
                      noRuleMatched={noRuleMatchedAnywhere(current)}
                    />
                    {current.decisions.length > 0 && (
                      <section>
                        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Decision set · 5 domains</h2>
                        <div className="stagger grid grid-cols-1 items-start gap-3 sm:grid-cols-2">
                          {current.decisions.map((d) => (
                            <DecisionCard key={d.type} decision={d} />
                          ))}
                        </div>
                      </section>
                    )}
                  </>
                )}
              </div>
            </div>

            {/* ---------------- Full-width analysis strip ---------------- */}
            {current && Object.keys(current.traces).length > 0 && (
              <>
                <section className="rounded-xl border border-border glass p-5">
                  <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Rule trace · every rule considered</h2>
                  <p className="mb-4 text-xs text-muted-foreground">Deterministic 4-phase pipeline · pick a domain to inspect</p>
                  <TracePipeline traces={current.traces} />
                </section>

                <section className="rounded-xl border border-border glass p-5">
                  <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Signal health</h2>
                  <SignalHealthPanel confidence={current.signal_confidence} health={current.signal_health} />
                </section>
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
