"""In-app nav overlay injected into proxied app HTML.

A running app service binds 127.0.0.1:<port> and is viewed through the reverse
proxy (routes/base_apps.py). On its own the app fills the page and strands you
with no way back to Workshop. This injects a small floating widget — a circle
pinned bottom-left that expands to Back to project / Reload / Open builder chat
— so you can always navigate out, whether the app is framed inside the Workshop
preview page OR opened full-screen at the bare proxy URL.

Design follows the researched pattern (Vercel toolbar / Vite overlay):
- one inline <script> appended before </body> (text/html only),
- the widget lives in an OPEN Shadow DOM custom element so the host app's CSS
  can't break it and `all: initial` stops inherited styles leaking in,
- host is pointer-events:none (overlay never eats the app's clicks); only the
  widget itself is clickable,
- behaviour branches on framed vs standalone (`window.self !== window.top`):
  framed -> namespaced, origin-checked postMessage to the Workshop shell, which
  owns the navigation; standalone -> drive window.top.location directly.

Injected inline (not an external src) so there is no cross-origin/base-href
fetch to get wrong and no extra round-trip; the proxy strips CSP/X-Frame-Options
on proxied responses (dev preview) so the inline script runs and framing works.
"""
from __future__ import annotations

import json

# Lucide icon paths (stroke icons), drawn inline so the widget needs no asset
# fetch. Kept as path-data only; the script wraps them in a styled <svg>.
_ICONS = {
    "tools": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "back": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "reload": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/>',
    "chat": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>',
    "close": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
}


def fab_script(slug: str, service: str) -> str:
    """Return the inline <script> tag that mounts the nav overlay for one app."""
    cfg = json.dumps({"slug": slug, "service": service, "icons": _ICONS})
    # NB: this string is injected into ARBITRARY user apps, not the Workshop
    # frontend, so it cannot use design-system tokens; inline styles are required
    # and the no-inline-hex rule (which scopes to frontend/*.tsx) does not apply.
    return (
        "<script>(function(){\n"
        "var CFG=" + cfg + ";\n"
        "if(window.__wsFab)return; window.__wsFab=1;\n"
        "var framed=false; try{framed=window.self!==window.top;}catch(e){framed=true;}\n"
        "function shellOrigin(){var l=window.location;var p=l.port==='8446'?'8443':l.port==='8000'?'5173':l.port;return p?(l.protocol+'//'+l.hostname+':'+p):(l.protocol+'//'+l.hostname);}\n"
        "function post(type,extra){var msg=Object.assign({type:type,v:1,slug:CFG.slug},extra||{});try{window.parent.postMessage(msg,shellOrigin());}catch(e){}}\n"
        "function go(type,path){if(framed){post(type);}else{try{window.top.location.assign(shellOrigin()+path);}catch(e){window.location.assign(shellOrigin()+path);}}}\n"
        "function svg(d){return '<svg viewBox=\"0 0 24 24\" width=\"18\" height=\"18\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\">'+d+'</svg>';}\n"
        "var host=document.createElement('div'); host.id='workshop-fab-host';\n"
        "var root=host.attachShadow({mode:'open'});\n"
        "var style=document.createElement('style');\n"
        "style.textContent=':host{all:initial;position:fixed;left:16px;bottom:16px;z-index:2147483647;pointer-events:none;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;}'+\n"
        "'.wrap{display:flex;flex-direction:column;align-items:flex-start;gap:8px;}'+\n"
        "'.menu{display:none;flex-direction:column;gap:6px;pointer-events:auto;}'+\n"
        "'.wrap.open .menu{display:flex;}'+\n"
        "'.item{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:rgba(20,24,33,.92);color:#e8eaed;font-size:13px;font-weight:500;cursor:pointer;white-space:nowrap;box-shadow:0 4px 16px rgba(0,0,0,.35);backdrop-filter:blur(6px);line-height:1;}'+\n"
        "'.item:hover{background:rgba(38,44,56,.95);border-color:rgba(255,255,255,.28);}'+\n"
        "'.item svg{flex:0 0 auto;opacity:.9;}'+\n"
        "'.toggle{pointer-events:auto;width:44px;height:44px;border-radius:50%;border:1px solid rgba(255,255,255,.16);background:rgba(20,24,33,.92);color:#e8eaed;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(0,0,0,.4);backdrop-filter:blur(6px);transition:transform .12s ease;}'+\n"
        "'.toggle:hover{background:rgba(38,44,56,.95);transform:scale(1.04);}'+\n"
        "'.tag{position:absolute;left:54px;bottom:13px;pointer-events:none;font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:rgba(232,234,237,.55);background:rgba(20,24,33,.7);padding:3px 6px;border-radius:6px;opacity:0;transition:opacity .12s;}'+\n"
        "'.wrap:not(.open):hover .tag{opacity:1;}';\n"
        "root.appendChild(style);\n"
        "var wrap=document.createElement('div'); wrap.className='wrap';\n"
        "var menu=document.createElement('div'); menu.className='menu';\n"
        "function item(icon,label,fn){var b=document.createElement('button');b.className='item';b.type='button';b.innerHTML=svg(CFG.icons[icon])+'<span>'+label+'</span>';b.addEventListener('click',function(ev){ev.stopPropagation();fn();});return b;}\n"
        "menu.appendChild(item('back','Back to project',function(){go('workshop/v1/nav','/projects/'+CFG.slug);}));\n"
        "menu.appendChild(item('chat','Open builder chat',function(){go('workshop/v1/openChat','/projects/'+CFG.slug);}));\n"
        "menu.appendChild(item('reload','Reload app',function(){window.location.reload();}));\n"
        "var toggle=document.createElement('button'); toggle.className='toggle'; toggle.type='button'; toggle.title='Workshop'; toggle.setAttribute('aria-label','Workshop controls'); toggle.innerHTML=svg(CFG.icons.tools);\n"
        "var tag=document.createElement('div'); tag.className='tag'; tag.textContent='Workshop';\n"
        "function setOpen(o){if(o){wrap.classList.add('open');toggle.innerHTML=svg(CFG.icons.close);}else{wrap.classList.remove('open');toggle.innerHTML=svg(CFG.icons.tools);}}\n"
        "toggle.addEventListener('click',function(ev){ev.stopPropagation();setOpen(!wrap.classList.contains('open'));});\n"
        "document.addEventListener('click',function(){setOpen(false);});\n"
        "wrap.appendChild(menu); wrap.appendChild(toggle); wrap.appendChild(tag);\n"
        "root.appendChild(wrap);\n"
        "function mount(){document.body.appendChild(host);}\n"
        "if(document.body){mount();}else{document.addEventListener('DOMContentLoaded',mount);}\n"
        "})();</script>"
    )


def inject(html: str, slug: str, service: str) -> str:
    """Append the overlay script before </body> (else at end of document)."""
    snippet = fab_script(slug, service)
    idx = html.rfind("</body>")
    if idx == -1:
        return html + snippet
    return html[:idx] + snippet + html[idx:]
