// The fetch helpers Memory's tree and documents share (`getJson` is the app's
// own, from lib/http). Every call site reads the same way and every failure
// carries the same short, human message (`httpFail`) rather than a raw status
// and a body.
import { httpFail } from "../../lib/http";

export { getJson } from "../../lib/http";

export async function sendJson<T>(url: string, method: "PUT" | "POST" | "DELETE", body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    ...(body === undefined
      ? {}
      : { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!r.ok) {
    throw await httpFail(r);
  }
  return (await r.json()) as T;
}
