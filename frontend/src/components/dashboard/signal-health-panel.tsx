import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import type { SignalStatus } from "@/lib/types";

const STATUS_STYLES: Record<SignalStatus, string> = {
  OK: "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30",
  DEGRADED: "bg-red-100 text-red-800 border-red-300 dark:bg-red-500/15 dark:text-red-300 dark:border-red-500/30",
  STALE: "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/30",
};

export function SignalHealthPanel({
  confidence,
  health,
}: {
  confidence: number;
  health: Record<string, SignalStatus>;
}) {
  const confidencePct = Math.round(confidence * 100);
  // Full class names as static literals (not interpolated) so Tailwind's scanner picks them up.
  const barColorClass =
    confidence >= 0.6
      ? "[&>div]:bg-emerald-500"
      : confidence >= 0.4
        ? "[&>div]:bg-amber-500"
        : "[&>div]:bg-red-500";

  return (
    <div className="space-y-3">
      <div>
        <div className="flex items-center justify-between text-sm mb-1">
          <span className="text-muted-foreground">Signal confidence</span>
          <span className="font-mono font-medium">{confidence.toFixed(2)}</span>
        </div>
        <Progress value={confidencePct} className={cn("h-2", barColorClass)} />
        <p className="text-xs text-muted-foreground mt-1">
          Below 0.6, OV-401 tightens device-financing verdicts toward REFER (spec §8.3).
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(health).map(([provider, status]) => (
          <Badge key={provider} variant="outline" className={cn("text-xs", STATUS_STYLES[status])}>
            {provider.replaceAll("_", " ")}: {status}
          </Badge>
        ))}
      </div>
    </div>
  );
}
