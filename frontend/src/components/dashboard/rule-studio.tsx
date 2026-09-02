import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { generateRule, searchRules } from "@/lib/api";
import { RULESET_IDS } from "@/lib/types";
import type { RuleGenerateResponse, RuleSearchResponse } from "@/lib/types";

const PHASE_TINT: Record<string, string> = {
  GATE: "border-[var(--block)]/40 bg-[var(--block)]/10 text-[var(--block)]",
  SCORING: "border-primary/40 bg-primary/10 text-primary",
  TERMS: "border-[var(--clear)]/40 bg-[var(--clear)]/10 text-[var(--clear)]",
  OVERLAY: "border-[var(--caution)]/40 bg-[var(--caution)]/10 text-[var(--caution)]",
};

const EXAMPLE_PROMPTS = [
  "Require a 50% deposit if the financed amount exceeds 2000 for new customers",
  "Decline the order if days past due is greater than 60",
  "Add 15 points to the score when tenure is at least 24 months",
  "Refer to manual review when velocity exceeds 3",
];

const EXAMPLE_QUERIES = ["cap financing for young customers", "fraud velocity blocklist", "deposit for high-value orders", "identity step up"];

type Seed = { q: string; n: number };

function SectionIcon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

function Meter({ value }: { value: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div className="h-full rounded-full bg-primary transition-all duration-700" style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }} />
    </div>
  );
}

function Generator({ onStored }: { onStored: (query: string) => void }) {
  const [text, setText] = useState(EXAMPLE_PROMPTS[0]);
  const [rulesetId, setRulesetId] = useState<string>(RULESET_IDS[0]);
  const [persist, setPersist] = useState(true);
  const [result, setResult] = useState<RuleGenerateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const res = await generateRule(text, { ruleset_id: rulesetId, persist });
      setResult(res);
      if (res.persisted) {
        // Surface the freshly-stored rule in the search panel so it's visibly in the vector DB.
        const codes = (res.rule.then?.reason_codes as string[] | undefined) ?? [];
        onStored((codes[0] ?? res.source_text).replaceAll("_", " ").toLowerCase());
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-xl border border-border glass p-5">
      <div className="flex items-center gap-2">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary/15 text-primary">
          <SectionIcon path="M12 3v18M3 12h18M7.5 7.5l9 9M16.5 7.5l-9 9" />
        </span>
        <div>
          <h3 className="text-sm font-semibold leading-none">Natural language → rule</h3>
          <p className="mt-1 text-[0.7rem] text-muted-foreground">Type a policy; it's validated, stored in the vector DB, and live at once</p>
        </div>
      </div>

      <Textarea value={text} onChange={(e) => setText(e.target.value)} className="mt-4 h-20 resize-none text-sm" placeholder="e.g. Require a 30% deposit if financed amount exceeds 1500" />

      <div className="mt-2 flex flex-wrap gap-1.5">
        {EXAMPLE_PROMPTS.map((p) => (
          <button key={p} onClick={() => setText(p)} className="rounded-md border border-border bg-background/40 px-2 py-1 text-[0.68rem] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground">
            {p.length > 40 ? p.slice(0, 40) + "…" : p}
          </button>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Target ruleset
          <select
            value={rulesetId}
            onChange={(e) => setRulesetId(e.target.value)}
            className="rounded-lg border border-input bg-background/40 px-2 py-1.5 text-xs text-foreground outline-none focus:border-primary/50"
          >
            {RULESET_IDS.map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </label>
        <button
          onClick={() => setPersist((p) => !p)}
          className={cn(
            "flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors",
            persist ? "border-primary/50 bg-primary/10 text-primary" : "border-border text-muted-foreground",
          )}
          title="When on, the rule is written to the vector DB and used in the next decision"
        >
          <span className={cn("h-3.5 w-6 rounded-full p-0.5 transition-colors", persist ? "bg-primary" : "bg-muted")}>
            <span className={cn("block h-2.5 w-2.5 rounded-full bg-white transition-transform", persist && "translate-x-2.5")} />
          </span>
          Store &amp; go live
        </button>
      </div>

      <Button onClick={run} disabled={loading || text.trim().length < 3} className="mt-3 w-full">
        {loading ? "Generating…" : persist ? "Generate & store rule" : "Preview rule"}
      </Button>

      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

      {result && (
        <div className="mt-4 animate-fade-up space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("rounded-full border px-2.5 py-1 text-[0.62rem] font-semibold uppercase tracking-wide", result.mode === "llm" ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-muted text-muted-foreground")}>
              {result.mode === "llm" ? "LLM" : "Heuristic"}
            </span>
            <span className={cn("rounded-full border px-2.5 py-1 text-[0.62rem] font-semibold uppercase tracking-wide", PHASE_TINT[result.rule.phase])}>{result.rule.phase}</span>
            <span className={cn("rounded-full border px-2.5 py-1 text-[0.62rem] font-semibold uppercase tracking-wide", result.valid ? "border-[var(--clear)]/40 bg-[var(--clear)]/10 text-[var(--clear)]" : "border-[var(--block)]/40 bg-[var(--block)]/10 text-[var(--block)]")}>
              {result.valid ? "✓ compiles" : "✗ invalid"}
            </span>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-[0.65rem] text-muted-foreground">confidence</span>
              <div className="w-16"><Meter value={result.confidence} /></div>
            </div>
          </div>

          {/* persistence banner — the key story: it's in the vector DB and live */}
          <div
            className={cn(
              "flex items-center gap-2 rounded-lg border px-3 py-2 text-xs",
              result.persisted
                ? "border-[var(--clear)]/40 bg-[var(--clear)]/10 text-foreground"
                : "border-border bg-muted/40 text-muted-foreground",
            )}
          >
            {result.persisted ? (
              <>
                <span className="text-[var(--clear)]">●</span>
                Stored in <span className="font-mono">{result.ruleset_id}</span> → live in search &amp; the next decision
              </>
            ) : (
              <>
                <span>○</span> Preview only — not stored
              </>
            )}
          </div>

          {result.warnings.length > 0 && (
            <ul className="space-y-1 rounded-lg border border-[var(--caution)]/30 bg-[var(--caution)]/10 p-2.5 text-[0.7rem] text-foreground/80">
              {result.warnings.map((w, i) => (
                <li key={i}>⚠ {w}</li>
              ))}
            </ul>
          )}

          <pre className="thin-scroll max-h-64 overflow-auto rounded-lg border border-border bg-background/60 p-3 text-[0.72rem] leading-relaxed">
            <code className="font-mono text-foreground/90">{JSON.stringify(result.rule, null, 2)}</code>
          </pre>
        </div>
      )}
    </div>
  );
}

function Search({ seed }: { seed: Seed }) {
  const [q, setQ] = useState(EXAMPLE_QUERIES[0]);
  const [result, setResult] = useState<RuleSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);

  async function run(query = q) {
    setLoading(true);
    setError(null);
    try {
      setResult(await searchRules(query));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  // When the generator stores a rule, auto-run a search for it and flash the panel.
  useEffect(() => {
    if (seed.n === 0) return;
    setQ(seed.q);
    run(seed.q);
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 1200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed.n]);

  return (
    <div className={cn("rounded-xl border glass p-5 transition-colors", flash ? "border-[var(--clear)]/60" : "border-border")}>
      <div className="flex items-center gap-2">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary/15 text-primary">
          <SectionIcon path="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3" />
        </span>
        <div>
          <h3 className="text-sm font-semibold leading-none">Semantic rule search</h3>
          <p className="mt-1 text-[0.7rem] text-muted-foreground">Vector search over every rule stored in the DB</p>
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          className="flex-1 rounded-lg border border-input bg-background/40 px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/20"
          placeholder="Search rules…"
        />
        <Button onClick={() => run()} disabled={loading || !q.trim()} variant="secondary">
          {loading ? "…" : "Search"}
        </Button>
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {EXAMPLE_QUERIES.map((query) => (
          <button key={query} onClick={() => { setQ(query); run(query); }} className="rounded-md border border-border bg-background/40 px-2 py-1 text-[0.68rem] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground">
            {query}
          </button>
        ))}
      </div>

      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

      {result && (
        <div className="mt-4 space-y-2">
          <p className="text-[0.68rem] text-muted-foreground">
            {result.count} matches · backend <span className="font-mono text-foreground/80">{result.backend}</span>
          </p>
          <div className="stagger space-y-2">
            {result.results.map((r) => (
              <div key={`${r.ruleset_id}-${r.rule_id}`} className="rounded-lg border border-border bg-background/40 p-3 hover-lift">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-semibold">{r.rule_id}</span>
                  <span className="text-[0.65rem] text-muted-foreground">{r.ruleset_id}</span>
                  {r.phase && <span className={cn("rounded border px-1.5 py-0.5 text-[0.58rem] font-semibold uppercase", PHASE_TINT[r.phase] ?? "border-border text-muted-foreground")}>{r.phase}</span>}
                  {r.score != null && <span className="ml-auto font-mono text-[0.65rem] text-primary">{r.score.toFixed(2)}</span>}
                </div>
                {r.score != null && <div className="mt-2"><Meter value={r.score} /></div>}
                {r.reason_codes.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {r.reason_codes.map((c) => (
                      <span key={c} className="rounded bg-muted px-1.5 py-0.5 text-[0.6rem] text-muted-foreground">{c}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function RuleStudio() {
  const [seed, setSeed] = useState<Seed>({ q: "", n: 0 });
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <Generator onStored={(q) => setSeed((s) => ({ q, n: s.n + 1 }))} />
      <Search seed={seed} />
    </div>
  );
}
