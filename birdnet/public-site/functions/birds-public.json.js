// Cloudflare Pages Function: serves the sanitized dataset from R2 at the
// same-origin path /birds-public.json so index.html can fetch it without CORS
// or a public bucket URL. Bind the R2 bucket as BIRDS_BUCKET in the Pages
// project settings (Settings -> Functions -> R2 bucket bindings).
//
// This is the only server-side code in the public deployment, and all it can
// reach is the sanitized object. No private DB, no clips, no location.
export async function onRequestGet({ env }) {
  const obj = await env.BIRDS_BUCKET.get("birds-public.json");
  if (!obj) {
    return new Response(JSON.stringify({ available: false }), {
      headers: { "content-type": "application/json" },
      status: 200,
    });
  }
  return new Response(obj.body, {
    headers: {
      "content-type": "application/json",
      // Short edge cache; the publish job updates hourly.
      "cache-control": "public, max-age=300",
    },
  });
}
