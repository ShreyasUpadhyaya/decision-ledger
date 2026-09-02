import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DecisionBadge } from "./decision-badge";
import type { DecisionResponse } from "@/lib/types";

export function RecentDecisions({
  decisions,
  selectedId,
  onSelect,
  onRefresh,
}: {
  decisions: DecisionResponse[];
  selectedId?: string;
  onSelect: (decision: DecisionResponse) => void;
  onRefresh: () => void;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">Recent decisions</CardTitle>
        <Button variant="ghost" size="sm" onClick={onRefresh}>
          Refresh
        </Button>
      </CardHeader>
      <CardContent>
        {decisions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No decisions yet — send a request to get started.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Decision</TableHead>
                <TableHead>Verdict</TableHead>
                <TableHead className="text-right">Confidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {decisions.map((d) => (
                <TableRow
                  key={d.decision_id}
                  data-state={d.decision_id === selectedId ? "selected" : undefined}
                  className="cursor-pointer"
                  onClick={() => onSelect(d)}
                >
                  <TableCell className="font-mono text-xs">
                    {d.decision_id}
                    {d.replayed && <span className="text-muted-foreground"> (replayed)</span>}
                  </TableCell>
                  <TableCell>
                    <DecisionBadge verdict={d.composite_verdict} />
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">{d.signal_confidence.toFixed(2)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
