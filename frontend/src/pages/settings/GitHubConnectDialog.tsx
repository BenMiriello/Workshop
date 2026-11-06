import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import { Button, Modal, Notice, Stack, StatusDot, copyText, type StatusKind } from "@workshop/ui";
import type { components } from "../../api/schema.gen";

// Connecting GitHub, as two steps in one dialog: sign in with a device code,
// then install the product's GitHub App on the account. A GitHub App's sign-in
// reaches only where the app is installed, so both steps are the connection.
//
// The single "Install & Authorize" page hosted products show needs a server
// holding the app's secret at a fixed address, which a self-hosted Workshop
// does not have; this is the device-flow version of the same two steps.
// Each step checks itself off from GitHub's answer, so the dialog carries no
// waiting text: the live dot is the waiting.

type Status = components["schemas"]["StatusOut"];
type Device = components["schemas"]["DeviceStartOut"];

const ENDPOINT = "/api/github";
const INSTALL_POLL_MS = 3000;

function detailOf(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  return typeof d === "string" ? d : fallback;
}

async function call<T>(path: string, method: "GET" | "POST", body?: unknown): Promise<T> {
  const r = await fetch(`${ENDPOINT}${path}`, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const parsed = await r.json().catch(() => null);
  if (!r.ok) throw new Error(detailOf(parsed, `GitHub sign-in failed (${r.status}).`));
  return parsed as T;
}

function Step({ status, title, children }: { status: StatusKind; title: ReactNode; children?: ReactNode }) {
  return (
    <Stack gap="sm">
      <StatusDot status={status}><strong>{title}</strong></StatusDot>
      {children && <div style={{ paddingLeft: "var(--space-5)" }}><Stack gap="sm">{children}</Stack></div>}
    </Stack>
  );
}

export function GitHubConnectDialog({ open, onClose, onStatus }: {
  open: boolean;
  onClose: () => void;
  onStatus: (s: Status) => void;
}) {
  const [device, setDevice] = useState<Device | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const report = useRef(onStatus);
  report.current = onStatus;

  const clearTimer = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };

  const take = useCallback((s: Status) => { setStatus(s); report.current(s); }, []);

  const pollDevice = useCallback((d: Device, ms: number) => {
    timer.current = window.setTimeout(async () => {
      try {
        const res = await call<{ status: string } & Status>("/device/poll", "POST", { device_code: d.device_code });
        if (res.status === "connected") { setDevice(null); take(res); return; }
        pollDevice(d, ms);
      } catch (e) {
        setDevice(null);
        setError(e instanceof Error ? e.message : String(e));
      }
    }, ms);
  }, [take]);

  const start = useCallback(async () => {
    clearTimer();
    setError(null);
    setStatus(null);
    try {
      const d = await call<Device>("/device", "POST");
      setDevice(d);
      // Answering faster than GitHub's interval earns a slow_down.
      pollDevice(d, Math.max(d.interval, 5) * 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [pollDevice]);

  useEffect(() => {
    if (!open) return;
    void start();
    return () => { clearTimer(); setDevice(null); setStatus(null); setError(null); };
  }, [open, start]);

  const signedIn = !!status?.connected;
  const installed = status?.installation?.state === "installed";

  // Step two: watch for the install while the dialog is open, and at once when
  // the operator comes back from github.com.
  useEffect(() => {
    if (!open || !signedIn || installed) return;
    const check = async () => {
      try { take(await call<Status>("", "GET")); } catch { /* the next tick asks again */ }
    };
    const id = window.setInterval(() => void check(), INSTALL_POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") void check(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", onVisible); };
  }, [open, signedIn, installed, take]);

  const openGitHub = (d: Device) => {
    void copyText(d.user_code);
    window.open(d.verification_uri, "_blank", "noopener");
  };

  const signInStatus: StatusKind = signedIn ? "ok" : error ? "stopped" : "running";
  const installStatus: StatusKind = installed ? "ok" : signedIn ? "running" : "stopped";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Connect GitHub"
      actions={installed
        ? <Button variant="primary" onClick={onClose}>Done</Button>
        : <Button variant="ghost" onClick={onClose}>Cancel</Button>}
    >
      <Stack gap="lg">
        <Step status={signInStatus}
              title={signedIn ? `Signed in as ${status?.login}` : "Sign in to GitHub"}>
          {!signedIn && device && <>
            <span className="ws-mono" style={{ fontSize: "var(--fs-xl)", letterSpacing: "0.08em" }}>
              {device.user_code}
            </span>
            <div>
              <Button variant="primary" onClick={() => openGitHub(device)}>
                <ExternalLink aria-hidden /> Copy code and open GitHub
              </Button>
            </div>
          </>}
          {!signedIn && error && (
            <Notice tone="error" action={<Button size="sm" onClick={() => void start()}>Get a new code</Button>}>
              {error}
            </Notice>
          )}
        </Step>

        <Step status={installStatus}
              title={installed ? "Installed on your account" : "Install on your account"}>
          {signedIn && !installed && status && <>
            <span className="ws-muted">Choose All repositories so Workshop can create new ones.</span>
            <div>
              <Button variant="primary" onClick={() => window.open(status.install_url, "_blank", "noopener")}>
                <ExternalLink aria-hidden /> Install on GitHub
              </Button>
            </div>
          </>}
        </Step>
      </Stack>
    </Modal>
  );
}
