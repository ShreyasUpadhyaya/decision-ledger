import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { SEVERITY_BADGE, severityOf } from "@/lib/decision";
import type { Verdict } from "@/lib/types";

export function DecisionBadge({ verdict, className }: { verdict: Verdict; className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("font-semibold tracking-wide", SEVERITY_BADGE[severityOf(verdict)], className)}
    >
      {verdict.replaceAll("_", " ")}
    </Badge>
  );
}
