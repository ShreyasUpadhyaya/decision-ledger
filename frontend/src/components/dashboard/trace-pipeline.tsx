import { useState } from "react";
import { cn } from "@/lib/utils";
import { DECISION_LABELS } from "@/lib/decision";
import type { TraceEntry, TraceOutcome } from "@/lib/types";

const PHASES = ["GATE", "SCORING", "TERMS", "OVERLAY"] as const;
type Phase = (typeof PHASES)[number];

const PHASE_BLURB: Record<Phase, string> = {
  GATE: "Hard stops · first match wins",
  SCORING: "Accumulate risk deltas",
  TERMS: "Set commercial terms",
  OVERLAY: "Tighten-only caps",
};

const OUTCOME_DOT: Record<TraceOutcome, string> = {
  TRUE: "bg-[var(--clear)] shadow-[0_0_8px_var(--clear)]",
  INDETERMINATE: "bg-[var(--caution)] shadow-[0_0_8px_var(--caution)]",
  FALSE: "bg-muted-foreground/30",
  SKIPPED: "bg-muted-foreground/20",
  NOT_EVALUATED: "bg-muted-foreground/20",
};

const OUTCOME_CHIP: Record<TraceOutcome, string> = {
  TRUE: "border-[var(--clear)]/40 bg-[var(--clear)]/10 text-foreground",
  INDETERMINATE: "border-[var(--caution)]/40 bg-[var(--caution)]/10 text-foreground",
  FALSE: "border-border bg-muted/40 text-muted-foreground",
  SKIPPED: "border-dashed border-border bg-transparent text-muted-foreground/70",
  NOT_EVALUATED: "border-dashed border-border bg-transparent text-muted-foreground/70",
};

function PhaseColumn({ phase, entries }: { phase: Phase; entries: TraceEntry[] }) {
  const matched = entries.filter((e) => e.outcome === "TRUE").length;
  const active = matched > 0;

  return (
    <div className="relative flex-1 min-w-[150px]">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "grid h-6 w-6 place-items-center rounded-full border text-[0.6rem] font-bold",
            active ? "border-primary/50 bg-primary/15 text-primary" : "border-border bg-muted text-muted-foreground",
          )}
        >
          {matched}
        </span>
        <div>
          <div className="text-xs font-semibold tracking-wide">{phase}</div>
          <div className="text-[0.62rem] text-muted-foreground">{PHASE_BLURB[phase]}</div>
        </div>
      </div>

      <div className="mt-3 space-y-1.5 thin-scroll max-h-56 overflow-y-auto pr-1">
        {entries.length === 0 && <p className="text-[0.7rem] text-muted-foreground/60 italic">no rules</p>}
        {entries.map((e, i) => (
          <div
            key={`${e.rule_id}-${i}`}
            className={cn(
              "flex items-center gap-2 rounded-md border px-2 py-1 text-[0.7rem] transition-colors",
              OUTCOME_CHIP[e.outcome],
            )}
            title={e.reason ?? e.skipped_by ?? e.outcome}
          >
            <span className={cn("h-2 w-2 shrink-0 rounded-full", OUTCOME_DOT[e.outcome])} />
            <span className="font-mono">{e.rule_id}</span>
            {e.score_delta != null && (
              <span className={cn("ml-auto font-mono font-semibold", e.score_delta > 0 ? "text-[var(--clear)]" : "text-[var(--block)]")}>
                {e.score_delta > 0 ? "+" : ""}
                {e.score_delta}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function TracePipeline({ traces }: { traces: Record<string, TraceEntry[]> }) {
  const domains = Object.keys(traces);
  const [active, setActive] = useState(domains[0]);
  const current = active && traces[active] ? active : domains[0];
  const entries = traces[current] ?? [];

  const hasPhases = entries.some((e) => e.phase);
  const byPhase = (phase: Phase) => entries.filter((e) => e.phase === phase);

  return (
    <div>
      {/* domain tabs */}
      <div className="mb-4 flex flex-wrap gap-1.5">
        {domains.map((d) => {
          const matched = (traces[d] ?? []).filter((e) => e.outcome === "TRUE").length;
          const isActive = d === current;
          return (
            <button
              key={d}
              onClick={() => setActive(d)}
              className={cn(
                "rounded-lg border px-3 py-1.5 text-xs font-medium transition-all",
                isActive
                  ? "border-primary/50 bg-primary/10 text-primary"
                  : "border-border bg-transparent text-muted-foreground hover:border-primary/30 hover:text-foreground",
              )}
            >
              {DECISION_LABELS[d] ?? d}
              <span className="ml-1.5 opacity-60">{matched}</span>
            </button>
          );
        })}
      </div>

      {hasPhases ? (
        <div className="relative flex flex-col gap-4 md:flex-row md:gap-2">
          {/* connecting rail behind the columns on desktop */}
          <div className="pointer-events-none absolute left-0 right-0 top-3 hidden h-px bg-gradient-to-r from-transparent via-border to-transparent md:block" />
          {PHASES.map((phase) => (
            <PhaseColumn key={phase} phase={phase} entries={byPhase(phase)} />
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {entries.map((e, i) => (
            <p key={i}>{(e as unknown as { note?: string }).note ?? JSON.stringify(e)}</p>
          ))}
        </div>
      )}
    </div>
  );
}
