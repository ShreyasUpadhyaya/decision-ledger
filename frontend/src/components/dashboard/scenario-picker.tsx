import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { SCENARIOS } from "@/lib/scenarios";
import type { DecisionRequest } from "@/lib/types";

export function ScenarioPicker({
  onSubmit,
  loading,
}: {
  onSubmit: (request: DecisionRequest) => void;
  loading: boolean;
}) {
  const [scenarioId, setScenarioId] = useState(SCENARIOS[0].id);
  const [customJson, setCustomJson] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);

  const scenario = SCENARIOS.find((s) => s.id === scenarioId)!;

  function runScenario() {
    onSubmit({ ...scenario.request, test_signals: scenario.testSignals });
  }

  function runCustom() {
    try {
      const parsed = JSON.parse(customJson) as DecisionRequest;
      setCustomError(null);
      onSubmit(parsed);
    } catch {
      setCustomError("That isn't valid JSON.");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Send a decision request</CardTitle>
        <CardDescription>Pick a worked example from the scenarios doc, or paste a custom request.</CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="scenario">
          <TabsList className="w-full">
            <TabsTrigger value="scenario" className="flex-1">
              Scenario
            </TabsTrigger>
            <TabsTrigger value="custom" className="flex-1">
              Custom JSON
            </TabsTrigger>
          </TabsList>
          <TabsContent value="scenario" className="space-y-3 pt-1">
            <Select value={scenarioId} onValueChange={setScenarioId}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SCENARIOS.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground min-h-8">{scenario.description}</p>
            <Button onClick={runScenario} disabled={loading} className="w-full">
              {loading ? "Evaluating…" : "Send request"}
            </Button>
          </TabsContent>
          <TabsContent value="custom" className="space-y-3 pt-1">
            <Textarea
              value={customJson}
              onChange={(e) => setCustomJson(e.target.value)}
              placeholder='{ "request_id": "req_1", "market": "DE", ... }'
              className="font-mono text-xs h-40"
            />
            {customError && <p className="text-xs text-destructive">{customError}</p>}
            <Button onClick={runCustom} disabled={loading || !customJson} className="w-full">
              {loading ? "Evaluating…" : "Send request"}
            </Button>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
