import { useCallback, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { router } from "expo-router";
import { WebView, type WebViewMessageEvent } from "react-native-webview";

import { TIER, WEB_URL } from "../native/config";
import { buildInjectedBridge } from "../native/inject";
import { createDispatcher } from "../native/bridge";
import { colors } from "../lib/api";

// The whole product surface is the Workshop web app, hosted in this WebView.
// Native capabilities reach the page through window.WorkshopNative (injected
// before content loads); see native/inject.ts + native/bridge.ts.

function originOf(url: string): string {
  const m = /^[a-z]+:\/\/[^/]+/i.exec(url);
  return m ? m[0] : url;
}

export default function Shell() {
  const webRef = useRef<WebView>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);

  // The bridge stub is installed before content loads; the web app's own auth
  // layer (lib/auth.ts) handles sign-in and attaches the Bearer token, reading/
  // writing the session through this bridge's secureStore. No token to preload.
  const injected = useMemo(() => buildInjectedBridge(TIER), []);
  const dispatcher = useMemo(() => createDispatcher(() => webRef.current), []);
  const webOrigin = useMemo(() => originOf(WEB_URL), []);

  const onMessage = useCallback(
    (e: WebViewMessageEvent) => {
      void dispatcher.onMessage(e.nativeEvent.data);
    },
    [dispatcher],
  );

  // Only our own origin renders in the WebView — it is the sole origin the native
  // bridge (window.WorkshopNative) trusts. about:blank is the harmless internal
  // empty document. Everything else — data:/about: variants, any off-origin page —
  // must NOT load here, where it would inherit the bridge and could reach the
  // Keychain device token; off-origin http(s) opens in Safari instead.
  // (security-audit-2026-07-18 H6)
  const onShouldStart = useCallback(
    (req: { url: string }) => {
      // Exact origin or a path UNDER it — with the "/" boundary so a look-alike host
      // like `your-host.example.com.evil.com` can't satisfy a bare startsWith and
      // inherit the bridge (security-audit-2026-07-18 re-audit finding 9).
      if (req.url === webOrigin || req.url.startsWith(webOrigin + "/") || req.url === "about:blank") {
        return true;
      }
      if (/^https?:\/\//i.test(req.url)) void Linking.openURL(req.url);
      return false;
    },
    [webOrigin],
  );

  const reload = useCallback(() => {
    setErrored(false);
    setLoading(true);
    webRef.current?.reload();
  }, []);

  return (
    <SafeAreaView style={styles.safe} edges={["top", "left", "right"]}>
      <StatusBar style="light" />
      {errored ? (
        <View style={styles.center}>
          <Text style={styles.title}>Can't reach the Workshop</Text>
          <Text style={styles.muted}>{WEB_URL}</Text>
          <Pressable style={styles.btn} onPress={reload}>
            <Text style={styles.btnText}>Retry</Text>
          </Pressable>
          {__DEV__ && (
            <Pressable onPress={() => router.push("/debug")} hitSlop={12}>
              <Text style={[styles.muted, styles.debugLink]}>Diagnostics</Text>
            </Pressable>
          )}
        </View>
      ) : (
        <>
          <WebView
            ref={webRef}
            source={{ uri: WEB_URL }}
            originWhitelist={[webOrigin, webOrigin + "/*"]}
            injectedJavaScriptBeforeContentLoaded={injected}
            onMessage={onMessage}
            onShouldStartLoadWithRequest={onShouldStart}
            onLoadEnd={() => setLoading(false)}
            onError={() => {
              setLoading(false);
              setErrored(true);
            }}
            onHttpError={() => setErrored(true)}
            pullToRefreshEnabled
            allowsBackForwardNavigationGestures
            allowsInlineMediaPlayback
            mediaPlaybackRequiresUserAction={false}
            startInLoadingState={false}
            style={styles.web}
          />
          {loading && (
            <View style={styles.loading} pointerEvents="none">
              <ActivityIndicator color={colors.accent} size="large" />
            </View>
          )}
        </>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  web: { flex: 1, backgroundColor: colors.bg },
  loading: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 12 },
  title: { color: colors.fg, fontSize: 18, fontWeight: "600" },
  muted: { color: colors.muted, fontSize: 13 },
  btn: {
    marginTop: 8,
    backgroundColor: colors.accent,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
  },
  btnText: { color: colors.bg, fontWeight: "600" },
  debugLink: { marginTop: 20, textDecorationLine: "underline" },
});
