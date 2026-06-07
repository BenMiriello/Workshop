import Constants from "expo-constants";

const API_BASE = (Constants.expoConfig?.extra as any)?.apiBase ?? "http://127.0.0.1:8000";

export async function api<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

export const colors = {
  bg: "#0b0f17",
  card: "#131a26",
  border: "#1f2a3a",
  fg: "#e7ecf3",
  muted: "#94a3b8",
  accent: "#60a5fa",
  err: "#fca5a5",
  good: "#86efac",
};
