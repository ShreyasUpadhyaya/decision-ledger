import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { setProviderMode, resetProviders } from "@/lib/api";
import type { ProviderMode, ProviderName } from "@/lib/types";

const PROVIDERS: { name: ProviderName; label: string }[] = [
  { name: "bureau", label: "Bureau" },
  { name: "account_history", label: "Account history" },
  { name: "fraud", label: "Fraud" },
  { name: "identity", label: "Identity" },
  { name: "device_catalog", label: "Device catalog" },
];

const MODES: ProviderMode[] = ["OK", "TIMEOUT", "ERROR", "SLOW", "STALE"];

export function FailureInjectionPanel({ onChanged }: { onChanged: () => void }) {
  const [modes, setModes] = useState<Record<ProviderName, ProviderMode>>({
    bureau: "OK",
    account_history: "OK",
    fraud: "OK",
    identity: "OK",
    device_catalog: "OK",
  });
  const [busy, setBusy] = useState(false);

  async function apply(provider: ProviderName, mode: ProviderMode) {
    setModes((prev) => ({ ...prev, [provider]: mode }));
    setBusy(true);
    try {
      await setProviderMode(provider, mode);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    try {
      await resetProviders();
      setModes({ bureau: "OK", account_history: "OK", fraud: "OK", identity: "OK", device_catalog: "OK" });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Failure injection</CardTitle>
        <CardDescription>
          Flip a provider live, then re-send a request — spec §8.4. The most credible detail in the build: the
          system fails closed, never open, on credit.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {PROVIDERS.map(({ name, label }) => (
          <div key={name} className="flex items-center justify-between gap-2">
            <span className="text-sm">{label}</span>
            <Select value={modes[name]} disabled={busy} onValueChange={(mode) => apply(name, mode as ProviderMode)}>
              <SelectTrigger size="sm" className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODES.map((mode) => (
                  <SelectItem key={mode} value={mode}>
                    {mode}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
        <Button variant="outline" size="sm" className="w-full mt-2" onClick={reset} disabled={busy}>
          Reset all to OK
        </Button>
      </CardContent>
    </Card>
  );
}
