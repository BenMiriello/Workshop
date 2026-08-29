import { shortHttpError } from "@workshop/ui";

/** Turn a failed Response into an Error, reading the body for the message. */
export async function httpFail(r: Response): Promise<Error> {
  return new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}
