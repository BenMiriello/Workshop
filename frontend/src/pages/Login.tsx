// Login / create-account screen. Rendered by the App guard whenever there is no
// authenticated session (browser or mobile shell). One card, toggling between
// "Sign in" and "Create account".

import { useState, type FormEvent } from "react";
import { Hexagon } from "lucide-react";

import { Button, Card, Field, Input, PageHeader, Stack, StateBlock } from "@workshop/ui";
import { useAuth } from "../auth/AuthProvider";
import { AuthError } from "../lib/auth";

type Mode = "login" | "register";

export default function Login() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const registering = mode === "register";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      if (registering) {
        await register(email.trim(), password, name.trim() || undefined);
      } else {
        await login(email.trim(), password);
      }
      // On success the AuthProvider flips status to "authed" and the guard
      // swaps this screen for the app — nothing else to do here.
    } catch (err) {
      setError(
        err instanceof AuthError
          ? err.message
          : "Something went wrong.",
      );
      setBusy(false);
    }
  }

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
  }

  const canSubmit =
    email.trim().length > 0 && password.length >= (registering ? 8 : 1);

  return (
    <div className="ws-authscreen">
      <div className="ws-authscreen__card">
        <div className="ws-authscreen__brand">
          <Hexagon size={20} strokeWidth={1.8} aria-hidden />
          <span>Workshop</span>
        </div>
        <Card>
          <form onSubmit={submit}>
            <Stack>
              <PageHeader
                title={registering ? "Create your account" : "Sign in"}
                sub={
                  registering
                    ? "Set up access to your Workshop."
                    : "Welcome back. Sign in to continue."
                }
              />
              {registering && (
                <Field label="Name" help="Optional. How you'd like to be addressed.">
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    autoComplete="name"
                    placeholder="Ben"
                  />
                </Field>
              )}
              <Field label="Email" required>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  autoCapitalize="none"
                  autoFocus
                  placeholder="you@example.com"
                />
              </Field>
              <Field
                label="Password"
                required
                help={registering ? "At least 8 characters." : undefined}
              >
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={registering ? "new-password" : "current-password"}
                  placeholder="••••••••"
                />
              </Field>

              {error && <StateBlock variant="error" title={error} />}

              <Button
                type="submit"
                variant="primary"
                loading={busy}
                disabled={busy || !canSubmit}
              >
                {registering ? "Create account" : "Sign in"}
              </Button>

              <Button
                type="button"
                variant="ghost"
                onClick={() => switchMode(registering ? "login" : "register")}
                disabled={busy}
              >
                {registering
                  ? "Already have an account? Sign in"
                  : "Need an account? Create one"}
              </Button>
            </Stack>
          </form>
        </Card>
      </div>
    </div>
  );
}
