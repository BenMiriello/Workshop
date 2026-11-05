import { HttpError, shortHttpError } from "@workshop/ui";

/** Turn a failed Response into an error, reading the body for the message.
 *  Typed with its status, so a 502 in a restart window is retried as the
 *  transient failure it is rather than shown as a refusal. */
export async function httpFail(r: Response): Promise<HttpError> {
  return new HttpError(r.status, shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}

/** An OK response's body as JSON; a failed one throws its HttpError. */
export async function jsonOrFail<T>(r: Response): Promise<T> {
  if (!r.ok) throw await httpFail(r);
  return r.json() as Promise<T>;
}

/** GET a JSON resource; a failed response throws its HttpError. */
export async function getJson<T>(url: string): Promise<T> {
  return jsonOrFail<T>(await fetch(url));
}
