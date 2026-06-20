// Builds the JavaScript injected into the WebView BEFORE the Workshop SPA loads
// (react-native-webview `injectedJavaScriptBeforeContentLoaded`). It installs
// `window.WorkshopNative` — a generic promise-based RPC client whose namespaces
// are generated from the shared CAP_METHODS manifest, so the web app and the
// native dispatcher can never drift on method names.
//
// This file runs in the RN context (it can import); its output is a plain string
// that runs in the web context (it cannot import — everything is interpolated).

import { BRIDGE_VERSION, CAP_METHODS, NATIVE_GLOBAL, RECV_FN } from "@shared/native-bridge";

export function buildInjectedBridge(tier: "free" | "paid", token?: string | null): string {
  const manifest = JSON.stringify(CAP_METHODS);
  const caps = JSON.stringify(Object.keys(CAP_METHODS));
  const global = JSON.stringify(NATIVE_GLOBAL);
  const recv = JSON.stringify(RECV_FN);
  const tok = JSON.stringify(token ?? "");

  // NOTE: kept ES5-ish on purpose — runs in whatever the WebView's JS engine is.
  return `(function(){
    var GLOBAL=${global};
    if (window[GLOBAL]) return;
    var RECV=${recv};
    var VERSION=${BRIDGE_VERSION};
    var TIER=${JSON.stringify(tier)};
    var TOKEN=${tok};
    var CAPS=${caps};
    var MANIFEST=${manifest};
    var pending={}, seq=1, subs={};
    function post(msg){
      if (window.ReactNativeWebView && window.ReactNativeWebView.postMessage) {
        window.ReactNativeWebView.postMessage(JSON.stringify(msg));
      }
    }
    function call(cap, method, args){
      return new Promise(function(resolve, reject){
        var id=seq++;
        pending[id]={resolve:resolve, reject:reject};
        post({t:"req", id:id, cap:cap, method:method, args:args||[], v:VERSION});
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
    var api={
      bridgeVersion:VERSION,
      platform:"ios",
      tier:TIER,
      has:function(cap){ return CAPS.indexOf(cap) !== -1; },
      on:function(topic, cb){
        (subs[topic]=subs[topic]||[]).push(cb);
        return function(){ subs[topic]=(subs[topic]||[]).filter(function(f){return f!==cb;}); };
      }
    };
    Object.keys(MANIFEST).forEach(function(cap){
      api[cap]={};
      MANIFEST[cap].forEach(function(m){
        api[cap][m]=function(){ return call(cap, m, Array.prototype.slice.call(arguments)); };
      });
    });
    window[GLOBAL]=api;
    // Off-tailnet fallback: attach the stored Bearer token to same-origin /api
    // calls. On the tailnet the backend trusts the peer and ignores this.
    if (TOKEN) {
      var _fetch=window.fetch;
      window.fetch=function(input, init){
        init=init||{};
        var url=(typeof input==="string")?input:((input&&input.url)||"");
        if (url.charAt(0)==="/" || url.indexOf("/api")!==-1) {
          init.headers=Object.assign({}, init.headers||{}, {Authorization:"Bearer "+TOKEN});
        }
        return _fetch.call(this, input, init);
      };
    }
    window.dispatchEvent(new Event("workshopnativeready"));
  })();
  true;`;
}
