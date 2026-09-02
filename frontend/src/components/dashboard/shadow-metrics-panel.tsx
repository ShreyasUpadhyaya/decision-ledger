import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getShadowMetrics, publishShadowRule } from "@/lib/api";
import { RULESET_IDS } from "@/lib/types";
import type { ShadowRuleMetric } from "@/lib/types";

function SectionIcon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

export function ShadowMetricsPanel() {
  const [rulesetId, setRulesetId] = useState<string>(RULESET_IDS[0]);
  const [rules, setRules] = useState<ShadowRuleMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [publishedIds, setPublishedIds] = useState<Set<string>>(new Set());

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await getShadowMetrics(rulesetId);
      setRules(res.rules);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load shadow metrics");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setPublishedIds(new Set());
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rulesetId]);

  async function publish(ruleId: string) {
    setBusyId(ruleId);
    setError(null);
    try {
      await publishShadowRule(rulesetId, ruleId);
      setPublishedIds((prev) => new Set(prev).add(ruleId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to publish rule");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="rounded-xl border border-border glass p-5">
      <div className="flex items-center gap-2">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary/15 text-primary">
          <SectionIcon path="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
        </span>
        <div>
          <h3 className="text-sm font-semibold leading-none">Shadow mode · dark-launched rules</h3>
          <p className="mt-1 text-[0.7rem] text-muted-foreground">
            MEDIUM-risk rules evaluated against live traffic without affecting it — Tier 2
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Ruleset
          <select
            value={rulesetId}
            onChange={(e) => setRulesetId(e.target.value)}
            className="rounded-lg border border-input bg-background/40 px-2 py-1.5 text-xs text-foreground outline-none focus:border-primary/50"
          >
            {RULESET_IDS.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
        <Button variant="outline" size="sm" className="ml-auto" onClick={() => void refresh()} disabled={loading}>
          {loading ? "…" : "Refresh"}
        </Button>
      </div>

      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

      {!loading && rules.length === 0 && !error && (
        <p className="mt-4 rounded-lg border border-dashed border-border bg-background/40 p-4 text-center text-xs text-muted-foreground">
          No shadow rules have fired yet for {rulesetId}. A MEDIUM-risk generated rule, or one added with
          is_shadow: true, will accumulate hits here without affecting real decisions.
        </p>
      )}

      <div className="stagger mt-4 space-y-3">
        {rules.map((r) => {
          const published = publishedIds.has(r.rule_id);
          return (
            <div key={r.rule_id} className="rounded-lg border border-border bg-background/40 p-3 hover-lift">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs font-semibold">{r.rule_id}</span>
                <span className="ml-auto rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[0.62rem] font-semibold text-primary">
                  {r.total_hits} hit{r.total_hits === 1 ? "" : "s"}
                </span>
              </div>

              {Object.keys(r.by_verdict).length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(r.by_verdict).map(([verdict, count]) => (
                    <span key={verdict} className="rounded bg-muted px-1.5 py-0.5 text-[0.62rem] text-muted-foreground">
                      would have yielded <span className="font-semibold text-foreground">{verdict}</span> × {count}
                    </span>
                  ))}
                </div>
              )}

              <Button
                size="sm"
                className={cn("mt-3 w-full", published && "opacity-60")}
                disabled={busyId === r.rule_id || published}
                onClick={() => publish(r.rule_id)}
              >
                {published ? "Published — now live" : busyId === r.rule_id ? "…" : "Publish (make live)"}
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
