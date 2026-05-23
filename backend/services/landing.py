"""Workshop launcher landing page.

Served by the backend at GET / and reached via Tailscale Serve mount
:443/ → :8000. Lists the per-port apps as equals so no single app is the
implicit root of the universe.
"""
from __future__ import annotations

LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Workshop</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
<meta name="color-scheme" content="light dark" />
<style>
  :root { color-scheme: light dark; }
  html, body { margin: 0; height: 100%; }
  body {
    font: 16px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f8fafc;
    color: #111;
    padding: env(safe-area-inset-top) 1rem env(safe-area-inset-bottom);
    -webkit-text-size-adjust: 100%;
  }
  @media (prefers-color-scheme: dark) {
    body { background: #0b0f17; color: #e7ecf3; }
  }
  main { max-width: 620px; margin: 2rem auto; }
  h1 { margin: 0 0 1.5rem; font-size: 1.4rem; font-weight: 600; }
  ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.6rem; }
  a.tile {
    display: block;
    padding: 1rem 1.1rem;
    background: #fff;
    color: inherit;
    text-decoration: none;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    transition: border-color 120ms ease;
  }
  a.tile:hover, a.tile:active { border-color: #2563eb; }
  @media (prefers-color-scheme: dark) {
    a.tile { background: #131a26; border-color: #1f2a3a; }
    a.tile:hover, a.tile:active { border-color: #60a5fa; }
  }
  a.tile strong { display: block; font-size: 1.05rem; }
  a.tile span { display: block; font-size: 0.85rem; color: #6b7280; margin-top: 0.15rem; }
  @media (prefers-color-scheme: dark) { a.tile span { color: #94a3b8; } }
  footer { margin-top: 2.5rem; font-size: 0.78rem; color: #6b7280; }
  @media (prefers-color-scheme: dark) { footer { color: #94a3b8; } }
</style>
</head>
<body>
<main>
<h1>Workshop launcher</h1>
<ul>
  <li><a class="tile" href="https://debian.tail38e62e.ts.net:8443/">
    <strong>Workshop</strong><span>Agents, projects, apps</span>
  </a></li>
  <li><a class="tile" href="https://debian.tail38e62e.ts.net:8444/">
    <strong>Commute</strong><span>Day-of brief, alerts, schedule</span>
  </a></li>
  <li><a class="tile" href="https://debian.tail38e62e.ts.net:8445/">
    <strong>Open Wearables</strong><span>Health + activity data</span>
  </a></li>
</ul>
<footer>Tailnet only. Bookmark this page on your phone home screen.</footer>
</main>
</body>
</html>
"""
