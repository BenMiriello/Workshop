// The one fetch helper Memory's tree and documents share. Every call site reads
// the same way and every failure carries the same short, human message
// (`shortHttpError`) rather than a raw status and a body.
import { shortHttpError } from "@workshop/ui";

export async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return (await r.json()) as T;
}

export async function sendJson<T>(url: string, method: "PUT" | "POST" | "DELETE", body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    ...(body === undefined
      ? {}
      : { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!r.ok) {
    throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return (await r.json()) as T;
}
