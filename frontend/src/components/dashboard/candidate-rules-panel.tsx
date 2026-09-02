import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { approveCandidateRule, discardCandidateRule, listCandidateRules } from "@/lib/api";
import type { CandidateRule } from "@/lib/types";

// Mirrors SEVERITY_BADGE's bucket coloring (lib/decision.ts) so a risk tier reads with
// the same visual language as a verdict severity elsewhere in the app.
const RISK_TINT: Record<string, string> = {
  LOW: "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30",
  MEDIUM: "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/30",
  HIGH: "bg-red-100 text-red-800 border-red-300 dark:bg-red-500/15 dark:text-red-300 dark:border-red-500/30",
};

function SectionIcon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

export function CandidateRulesPanel() {
  const [candidates, setCandidates] = useState<CandidateRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await listCandidateRules("PENDING_REVIEW");
      setCandidates(res.candidates);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load candidate rules");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function act(candidateId: string, action: "approve" | "discard") {
    setBusyId(candidateId);
    setError(null);
    try {
      if (action === "approve") await approveCandidateRule(candidateId);
      else await discardCandidateRule(candidateId);
      setCandidates((prev) => prev.filter((c) => c.candidate_id !== candidateId));
    } catch (e) {
      setError(e instanceof Error ? e.message : `Failed to ${action} rule`);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="rounded-xl border border-border glass p-5">
      <div className="flex items-center gap-2">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary/15 text-primary">
          <SectionIcon path="M9 12l2 2 4-4m6 2a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" />
        </span>
        <div>
          <h3 className="text-sm font-semibold leading-none">Candidate rules · pending review</h3>
          <p className="mt-1 text-[0.7rem] text-muted-foreground">
            HIGH-risk rules the anomaly trigger generated but never touched the live engine — Tier 3
          </p>
        </div>
        <Button variant="outline" size="sm" className="ml-auto" onClick={() => void refresh()} disabled={loading}>
          {loading ? "…" : "Refresh"}
        </Button>
      </div>

      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

      {!loading && candidates.length === 0 && !error && (
        <p className="mt-4 rounded-lg border border-dashed border-border bg-background/40 p-4 text-center text-xs text-muted-foreground">
          Nothing waiting on review. A HIGH-risk rule from a detected anomaly will show up here.
        </p>
      )}

      <div className="stagger mt-4 space-y-3">
        {candidates.map((c) => (
          <div key={c.candidate_id} className="rounded-lg border border-border bg-background/40 p-3 hover-lift">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs font-semibold">{c.rule.id}</span>
              <span className="text-[0.65rem] text-muted-foreground">{c.ruleset_id}</span>
              <span className={cn("rounded-full border px-2 py-0.5 text-[0.62rem] font-semibold uppercase tracking-wide", RISK_TINT[c.risk_level])}>
                {c.risk_level} risk
              </span>
              <span className="ml-auto text-[0.62rem] text-muted-foreground">{new Date(c.created_at).toLocaleString()}</span>
            </div>

            {c.source_text && <p className="mt-2 text-xs italic text-muted-foreground">&ldquo;{c.source_text}&rdquo;</p>}
            <p className="mt-1 text-xs text-foreground/80">{c.reasoning}</p>

            <pre className="thin-scroll mt-2 max-h-32 overflow-auto rounded-lg border border-border bg-background/60 p-2 text-[0.68rem] leading-relaxed">
              <code className="font-mono text-foreground/90">{JSON.stringify(c.rule, null, 2)}</code>
            </pre>

            <div className="mt-3 flex gap-2">
              <Button size="sm" className="flex-1" disabled={busyId === c.candidate_id} onClick={() => act(c.candidate_id, "approve")}>
                {busyId === c.candidate_id ? "…" : "Approve"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="flex-1"
                disabled={busyId === c.candidate_id}
                onClick={() => act(c.candidate_id, "discard")}
              >
                Discard
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
