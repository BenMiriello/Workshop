import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { getToken } from "../native/auth";
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
  // undefined = still loading the stored token; null = none. We hold the WebView
  // until this resolves so the injected fetch-patch is in place before any SPA
  // request fires.
  const [token, setTokenState] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    getToken()
      .then((t) => setTokenState(t))
      .catch(() => setTokenState(null));
  }, []);

  const injected = useMemo(() => buildInjectedBridge(TIER, token ?? null), [token]);
  const dispatcher = useMemo(() => createDispatcher(() => webRef.current), []);
  const webOrigin = useMemo(() => originOf(WEB_URL), []);

  const onMessage = useCallback(
    (e: WebViewMessageEvent) => {
      void dispatcher.onMessage(e.nativeEvent.data);
    },
    [dispatcher],
  );

  // Keep navigation inside the app on our origin; send anything else to Safari.
  const onShouldStart = useCallback(
    (req: { url: string }) => {
      if (req.url.startsWith(webOrigin) || req.url.startsWith("about:") || req.url.startsWith("data:")) {
        return true;
      }
      void Linking.openURL(req.url);
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
      {token === undefined ? (
        <View style={styles.loading}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : errored ? (
        <View style={styles.center}>
          <Text style={styles.title}>Can't reach the Workshop</Text>
          <Text style={styles.muted}>{WEB_URL}</Text>
          <Pressable style={styles.btn} onPress={reload}>
            <Text style={styles.btnText}>Retry</Text>
          </Pressable>
          <Pressable onPress={() => router.push("/debug")} hitSlop={12}>
            <Text style={[styles.muted, styles.debugLink]}>Diagnostics</Text>
          </Pressable>
        </View>
      ) : (
        <>
          <WebView
            ref={webRef}
            source={{ uri: WEB_URL }}
            originWhitelist={["*"]}
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
