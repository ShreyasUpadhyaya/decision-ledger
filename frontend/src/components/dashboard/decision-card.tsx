import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DecisionBadge } from "./decision-badge";
import { colorOf, DECISION_LABELS } from "@/lib/decision";
import type { SubDecision } from "@/lib/types";

export function DecisionCard({ decision }: { decision: SubDecision }) {
  const terms = {
    ...(decision.terms ?? {}),
    ...(decision.review_task ? { sla_hours: decision.review_task.sla_hours } : {}),
  };
  const termEntries = Object.entries(terms);
  const accent = colorOf(decision.verdict);

  return (
    <Card className="relative gap-3 overflow-hidden hover-lift">
      {/* severity accent bar */}
      <span className="absolute inset-y-0 left-0 w-1" style={{ background: accent }} />
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-sm font-semibold">{DECISION_LABELS[decision.type] ?? decision.type}</CardTitle>
        <DecisionBadge verdict={decision.verdict} />
      </CardHeader>
      <CardContent className="space-y-2.5">
        {decision.pre_overlay_verdict && (
          <p className="text-xs text-muted-foreground">
            Tightened by an overlay from <span className="font-medium text-foreground/80">{decision.pre_overlay_verdict}</span>
          </p>
        )}
        {decision.score !== null && (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Score</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{ width: `${Math.max(0, Math.min(100, decision.score))}%`, background: accent }}
              />
            </div>
            <span className="font-mono font-semibold text-foreground">{decision.score}</span>
          </div>
        )}
        {termEntries.length > 0 && (
          <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-xs">
            {termEntries.map(([key, value]) => (
              <div key={key} className="contents">
                <span className="text-muted-foreground">{key.replaceAll("_", " ")}</span>
                <span className="text-right font-mono">{String(value)}</span>
              </div>
            ))}
          </div>
        )}
        {decision.reason_codes.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1">
            {decision.reason_codes.map((code) => (
              <Badge key={code} variant="secondary" className="text-[0.65rem] font-normal">
                {code}
              </Badge>
            ))}
          </div>
        )}
        {decision.matched_rules.length > 0 && (
          <p className="truncate font-mono text-[0.7rem] text-muted-foreground">{decision.matched_rules.join(" → ")}</p>
        )}
      </CardContent>
    </Card>
  );
}
