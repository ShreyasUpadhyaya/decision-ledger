import { RadialGauge } from "./risk-gauge";
import { colorOf, severityOf, DECISION_HEADLINE } from "@/lib/decision";
import type { DecisionResponse } from "@/lib/types";

export function DecisionHero({ response }: { response: DecisionResponse }) {
  const severity = severityOf(response.composite_verdict);
  const color = colorOf(response.composite_verdict);
  const headline = DECISION_HEADLINE[severity];

  const financing = response.decisions.find((d) => d.type === "DEVICE_FINANCING");
  const score = financing?.score ?? null;
  const confidence = response.signal_confidence;

  const terms = response.terms ?? {};
  const termEntries = Object.entries(terms);

  return (
    <div
      key={response.decision_id}
      className="relative overflow-hidden rounded-2xl border border-border glass glow-ring animate-reveal"
    >
      {/* accent glow keyed to the verdict severity */}
      <div
        className="pointer-events-none absolute -top-24 -left-16 h-64 w-64 rounded-full blur-3xl opacity-30"
        style={{ background: color }}
      />
      <div className="relative flex flex-col gap-6 p-6 md:flex-row md:items-center md:justify-between md:p-8">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-muted-foreground">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60" style={{ background: color }} />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full" style={{ background: color }} />
            </span>
            Composite verdict
          </div>

          <h2 className="mt-3 text-4xl font-bold leading-none tracking-tight md:text-5xl" style={{ color }}>
            {headline}
          </h2>
          <div className="mt-2 font-mono text-2xl font-semibold tracking-tight text-foreground/90">
            {response.composite_verdict.replaceAll("_", " ")}
          </div>

          <p className="mt-3 font-mono text-xs text-muted-foreground">
            {response.decision_id} · {response.request_id}
            {response.replayed && <span className="ml-1 text-primary">· replayed (idempotent)</span>}
          </p>

          {termEntries.length > 0 && (
            <div className="mt-5 flex flex-wrap gap-2">
              {termEntries.map(([key, value]) => (
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
        </div>

        <div className="flex shrink-0 items-center justify-center gap-6 md:gap-8">
          {score !== null && (
            <RadialGauge
              value={Math.max(0, Math.min(1, score / 100))}
              display={String(score)}
              sublabel="Risk score"
              color={color}
            />
          )}
          <RadialGauge
            value={confidence}
            display={confidence.toFixed(2)}
            sublabel="Signal conf."
            color={confidence >= 0.6 ? "var(--clear)" : confidence >= 0.4 ? "var(--caution)" : "var(--block)"}
            size={score !== null ? 132 : 168}
            stroke={score !== null ? 10 : 12}
          />
        </div>
      </div>
    </div>
  );
}
