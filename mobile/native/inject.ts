// Builds the JavaScript injected into the WebView BEFORE the Workshop SPA loads
// (react-native-webview `injectedJavaScriptBeforeContentLoaded`). It installs
// `window.WorkshopNative` — a generic promise-based RPC client whose namespaces
// are generated from the shared CAP_METHODS manifest, so the web app and the
// native dispatcher can never drift on method names.
//
// This file runs in the RN context (it can import); its output is a plain string
// that runs in the web context (it cannot import — everything is interpolated).
//
// It installs on the top document only. A same-origin app frame calls it as
// `window.parent.WorkshopNative`: arguments, the returned promises and `on()`
// callbacks cross realms safely, and a callback whose frame has gone away is
// caught like any other throwing listener. The contract is
// shared/native-bridge.ts.
//
// `forApp(app)` gives the same client acting for one app: its requests carry
// `app`, and its `on` passes only that app's "pages:*" events. The shell keeps
// each app's page views and their data, and its Live Activities, apart by that
// id (shared/native-pages.ts, shared/native-live-activity.ts).

import {
  APP_ID,
  BRIDGE_VERSION,
  CAP_METHODS,
  ENGINE_GLOBAL,
  NATIVE_GLOBAL,
  RECV_FN,
  type Capability,
  type NativeEngine,
  type NativePlatform,
} from "@shared/native-bridge";

/** The stub for a shell offering `caps` (every capability by default) on
 *  `platform`. The macOS shell (macos/) builds its stub here too, so both
 *  shells install the same client. `engine` is given when known now; without
 *  it the stub takes the one the shell put in `ENGINE_GLOBAL`, if any. */
export function buildInjectedBridge(
  tier: "free" | "paid",
  platform: NativePlatform = "ios",
  caps: readonly Capability[] = Object.keys(CAP_METHODS) as Capability[],
  engine?: NativeEngine,
): string {
  const offered = Object.fromEntries(caps.map((cap) => [cap, CAP_METHODS[cap]]));
  const manifest = JSON.stringify(offered);
  const capList = JSON.stringify(caps);
  const global = JSON.stringify(NATIVE_GLOBAL);
  const recv = JSON.stringify(RECV_FN);
  const engineKey = JSON.stringify(ENGINE_GLOBAL);
  const engineSource = engine ? JSON.stringify(engine) : `window[${engineKey}]`;

  // NOTE: kept ES5-ish on purpose — runs in whatever the WebView's JS engine is.
  return `(function(){
    var GLOBAL=${global};
    if (window[GLOBAL]) return;
    var RECV=${recv};
    var VERSION=${BRIDGE_VERSION};
    var TIER=${JSON.stringify(tier)};
    var CAPS=${capList};
    var MANIFEST=${manifest};
    var ENGINE=${engineSource};
    var APP_ID=${APP_ID};
    try { delete window[${engineKey}]; } catch(e){}
    var pending={}, seq=1, subs={};
    function post(msg){
      if (window.ReactNativeWebView && window.ReactNativeWebView.postMessage) {
        window.ReactNativeWebView.postMessage(JSON.stringify(msg));
      }
    }
    function call(cap, method, args, app){
      return new Promise(function(resolve, reject){
        var id=seq++;
        pending[id]={resolve:resolve, reject:reject};
        var msg={t:"req", id:id, cap:cap, method:method, args:args||[], v:VERSION};
        if (app) msg.app=app;
        post(msg);
      });
    }
    window[RECV]=function(msg){
      try { if (typeof msg === "string") msg = JSON.parse(msg); } catch(e){ return; }
      if (!msg || !msg.t) return;
      if (msg.t === "res"){
        var p=pending[msg.id]; if(!p) return; delete pending[msg.id];
        if (msg.ok) p.resolve(msg.result); else p.reject(new Error(msg.error || "native error"));
      } else if (msg.t === "evt"){
        var list=subs[msg.topic]||[];
        for (var i=0;i<list.length;i++){ try{ list[i](msg.payload); }catch(e){} }
      }
    };
    function subscribe(topic, cb){
      (subs[topic]=subs[topic]||[]).push(cb);
      return function(){ subs[topic]=(subs[topic]||[]).filter(function(f){return f!==cb;}); };
    }
    function build(app){
      var api={
        bridgeVersion:VERSION,
        platform:${JSON.stringify(platform)},
        tier:TIER,
        engine:ENGINE && typeof ENGINE === "object" ? ENGINE : undefined,
        has:function(cap){ return CAPS.indexOf(cap) !== -1; },
        on:function(topic, cb){
          if (!app || String(topic).indexOf("pages:") !== 0) return subscribe(topic, cb);
          return subscribe(topic, function(p){ if (p && p.app === app) cb(p); });
        },
        forApp:forApp
      };
      Object.keys(MANIFEST).forEach(function(cap){
        api[cap]={};
        MANIFEST[cap].forEach(function(m){
          var parts=m.split("."), ns=api[cap];
          for (var i=0;i<parts.length-1;i++){ ns=ns[parts[i]]=ns[parts[i]]||{}; }
          ns[parts[parts.length-1]]=function(){ return call(cap, m, Array.prototype.slice.call(arguments), app); };
        });
      });
      return api;
    }
    var scopes=Object.create(null);
    function forApp(app){
      if (typeof app !== "string" || !APP_ID.test(app)) throw new Error("Not an app id: " + app);
      return scopes[app] || (scopes[app]=build(app));
    }
    var api=build(null);
    window[GLOBAL]=api;
    post({t:"hello", v:VERSION});
    // Auth is owned by the web app's auth layer (frontend/src/lib/auth.ts): it
    // patches fetch to attach the product Bearer token and persists the session
    // through this bridge's secureStore. The shell deliberately does NOT bake a
    // token here — doing so would clobber that header.
    window.dispatchEvent(new Event("workshopnativeready"));
  })();
  true;`;
}
