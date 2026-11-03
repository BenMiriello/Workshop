import { useCallback, useEffect, useRef, useState } from "react";
import { shortHttpError } from "@workshop/ui";
import type { components } from "../../api/schema.gen";

type AppStatus = components["schemas"]["AppStatus"];
type ServiceStatus = components["schemas"]["ServiceStatus"];

export type AppRun = {
  /** The screen's service, once the runner has answered. */
  service: ServiceStatus | null;
  phase: string | null;
  error: string | null;
  /** This page asked the runner to start the app. */
  started: boolean;
  reload: () => void;
};

const POLL_MS = 2000;

/**
 * The run state of one app, for the screen that frames one of its services.
 *
 * RUNNING IS THE NORMAL STATE (app-screens-2026-09-23.md): opening a screen of
 * a stopped app starts it, once, and this polls until the service answers. A
 * FAILED app is not restarted from here: that would loop on a crash, and the
 * reason is on the app's Overview, which the page links to.
 */
export function useAppRun(slug: string, serviceName: string): AppRun {
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const asked = useRef(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/base-apps/run/${encodeURIComponent(slug)}/status`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const s = (await r.json()) as AppStatus;
      setStatus(s);
      setError(null);
      if (s.phase === "stopped" && !asked.current) {
        asked.current = true;
        setStarted(true);
        const st = await fetch(`/api/base-apps/run/${encodeURIComponent(slug)}/start`, { method: "POST" });
        if (!st.ok) throw new Error(shortHttpError(st.status, st.statusText, await st.text().catch(() => "")));
        setStatus((await st.json()) as AppStatus);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [slug]);

  const service = status?.services.find((s) => s.name === serviceName) ?? null;
  const settled = service?.status === "running" || status?.phase === "failed" || !!error;

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (settled) return;
    const t = window.setInterval(() => { void load(); }, POLL_MS);
    return () => window.clearInterval(t);
  }, [settled, load]);

  return { service, phase: status?.phase ?? null, error, started, reload: () => { void load(); } };
}
