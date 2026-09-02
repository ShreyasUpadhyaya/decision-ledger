import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/lib/auth";

export function SignInPage() {
  const { signin } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      // On success signin() navigates to the user's role home.
      await signin(username, password);
    } catch (err) {
      // Surfaces backend messages like "invalid credentials".
      setError(err instanceof Error ? err.message : "Sign in failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="grid min-h-svh place-items-center cockpit-bg px-6">
      <div className="animate-fade-up relative z-10 w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <Link to="/" aria-label="DecisionLedger home">
            <BrandMark className="h-12 w-12" />
          </Link>
          <h1 className="mt-4 text-2xl font-bold tracking-tight">
            Welcome back to <span className="brand-gradient-text">DecisionLedger</span>
          </h1>
        </div>

        <Card className="glass glow-ring">
          <CardHeader>
            <CardTitle className="text-lg">Sign in</CardTitle>
            <CardDescription>Enter your credentials to reach your cockpit.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="e.g. ada"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                />
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              <Button type="submit" className="w-full" disabled={loading}>
                {loading ? "Signing in…" : "Sign In"}
              </Button>
            </form>

            <p className="mt-4 text-center text-sm text-muted-foreground">
              No account yet?{" "}
              <Link to="/signup" className="font-medium text-primary hover:underline">
                Create one
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
