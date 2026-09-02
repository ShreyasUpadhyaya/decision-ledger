import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/lib/auth";
import type { Role } from "@/lib/types";

export function SignUpPage() {
  const { signup } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      // On success signup() registers, auto signs-in, then navigates to the role home.
      await signup(username, password, role);
    } catch (err) {
      // Surfaces backend messages like "username already taken".
      setError(err instanceof Error ? err.message : "Could not create account.");
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
            Join <span className="brand-gradient-text">DecisionLedger</span>
          </h1>
        </div>

        <Card className="glass glow-ring">
          <CardHeader>
            <CardTitle className="text-lg">Create account</CardTitle>
            <CardDescription>Pick a role to see the matching workspace.</CardDescription>
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
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="role">Role</Label>
                <Select value={role} onValueChange={(v) => setRole(v as Role)}>
                  <SelectTrigger id="role" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">admin</SelectItem>
                    <SelectItem value="user">user</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {role === "admin"
                    ? "Admins land in the full decision cockpit."
                    : "Users get a streamlined order form."}
                </p>
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              <Button type="submit" className="w-full" disabled={loading}>
                {loading ? "Creating…" : "Create account"}
              </Button>
            </form>

            <p className="mt-4 text-center text-sm text-muted-foreground">
              Already have an account?{" "}
              <Link to="/signin" className="font-medium text-primary hover:underline">
                Sign in
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
