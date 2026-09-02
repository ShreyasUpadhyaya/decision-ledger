import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { TraceEntry, TraceOutcome } from "@/lib/types";

const OUTCOME_STYLES: Record<TraceOutcome, string> = {
  TRUE: "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30",
  FALSE: "bg-muted text-muted-foreground border-border",
  INDETERMINATE:
    "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/30",
  SKIPPED: "bg-muted text-muted-foreground border-border italic",
  NOT_EVALUATED: "bg-muted text-muted-foreground border-border italic",
};

const LABELS: Record<string, string> = {
  DEVICE_FINANCING: "Device financing",
  FRAUD: "Fraud",
  IDENTITY: "Identity",
  TARIFF_ELIGIBILITY: "Tariff eligibility",
  PAYMENT_POLICY: "Payment policy",
};

function ruleCount(entries: TraceEntry[]) {
  const matched = entries.filter((e) => e.outcome === "TRUE").length;
  return `${matched} matched / ${entries.length} rules`;
}

export function TraceViewer({ traces }: { traces: Record<string, TraceEntry[]> }) {
  return (
    <Accordion type="multiple" className="w-full">
      {Object.entries(traces).map(([rulesetType, entries]) => (
        <AccordionItem key={rulesetType} value={rulesetType}>
          <AccordionTrigger className="text-sm hover:no-underline">
            <span className="flex items-center gap-2">
              <span className="font-medium">{LABELS[rulesetType] ?? rulesetType}</span>
              <span className="text-xs text-muted-foreground font-normal">{ruleCount(entries)}</span>
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Rule</TableHead>
                  <TableHead>Phase</TableHead>
                  <TableHead>Outcome</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.map((entry, i) => (
                  <TableRow key={`${entry.rule_id}-${i}`}>
                    <TableCell className="font-mono text-xs">{entry.rule_id}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{entry.phase}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={cn("text-[0.65rem]", OUTCOME_STYLES[entry.outcome])}>
                        {entry.outcome}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {entry.score_delta != null && (
                        <span className="font-mono">
                          {entry.score_delta > 0 ? "+" : ""}
                          {entry.score_delta}
                        </span>
                      )}
                      {entry.skipped_by && <span>skipped by {entry.skipped_by}</span>}
                      {entry.reason && <span>{entry.reason}</span>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </AccordionContent>
        </AccordionItem>
      ))}
    </Accordion>
  );
}
