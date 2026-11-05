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
import { Stack, router, useLocalSearchParams } from "expo-router";
import { WebView, type WebViewMessageEvent } from "react-native-webview";

import { APP_ID } from "@shared/native-bridge";

import { TIER, WEB_URL, nativeEngine } from "../native/config";
import { buildInjectedBridge } from "../native/inject";
import { createDispatcher } from "../native/bridge";
import * as clipboard from "../native/clipboard";
import * as pages from "../native/pages";
import { READ_SELECTION_CALL, buildReadSelection } from "../native/readSelection";
import { buildAppearanceWatch, parseAppearance, type PageAppearance } from "../native/appearance";
import SelectionMenu from "../modules/workshop-selection-menu";
import { colors } from "../lib/api";

// The whole product surface is the Workshop web app, hosted in this WebView.
// Native capabilities reach the page through window.WorkshopNative (injected
// before content loads); see native/inject.ts + native/bridge.ts.

// The shell's own screens (loading, error) are dark, the Workshop default.
const SHELL_LOOK: PageAppearance = { bg: colors.bg, dark: true };

function originOf(url: string): string {
  const m = /^[a-z]+:\/\/[^/]+/i.exec(url);
  return m ? m[0] : url;
}

/** Workshop's page for app `slug`, the target of `<scheme>://?open=<slug>`:
 *  the link a Live Activity opens. */
function appPage(slug: string | undefined): string | undefined {
  return slug && APP_ID.test(slug) ? `${originOf(WEB_URL)}/apps/${slug}` : undefined;
}

export default function Shell() {
  const webRef = useRef<WebView>(null);
  const { open } = useLocalSearchParams<{ open?: string }>();
  const [startUrl] = useState(() => appPage(open) ?? WEB_URL);
  const opened = useRef(open);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);
  const [pageLook, setPageLook] = useState(SHELL_LOOK);
  const look = errored ? SHELL_LOOK : pageLook;

  // The bridge stub is installed before content loads; the web app's own auth
  // layer (lib/auth.ts) handles sign-in and attaches the Bearer token, reading/
  // writing the session through this bridge's secureStore. No token to preload.
  const injected = useMemo(
    () => buildInjectedBridge(TIER, "ios", undefined, nativeEngine()) +
      buildReadSelection() +
      buildAppearanceWatch(),
    [],
  );
  // Page views belong to the document that opened them: a new top document
  // (reload, full navigation) closes every app's.
  const dispatcher = useMemo(
    () => createDispatcher(() => webRef.current, () => void pages.closeEverything()),
    [],
  );
  useEffect(() => pages.subscribe(dispatcher.emit), [dispatcher]);
  useEffect(() => clipboard.startWatch(), []);
  useEffect(() => {
    if (errored) void pages.closeEverything();
  }, [errored]);
  const webOrigin = useMemo(() => originOf(WEB_URL), []);

  // A link arriving while the shell runs; one at launch is `startUrl`. The
  // param is cleared so the same link works again.
  useEffect(() => {
    const page = appPage(open);
    if (page && opened.current !== open) {
      webRef.current?.injectJavaScript(`window.location.assign(${JSON.stringify(page)}); true;`);
    }
    opened.current = open;
    if (open) router.setParams({ open: "" });
  }, [open]);

  const onMessage = useCallback(
    (e: WebViewMessageEvent) => {
      const appearance = parseAppearance(e.nativeEvent.data);
      if (appearance) setPageLook(appearance);
      else void dispatcher.onMessage(e.nativeEvent.data);
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

  // "Read aloud" in the system's text-selection menu. It starts from a native
  // callback, not a page gesture; the reader is still heard because this
  // WebView plays audio without one (mediaPlaybackRequiresUserAction).
  const readSelection = useCallback(() => {
    webRef.current?.injectJavaScript(READ_SELECTION_CALL);
  }, []);

  const reload = useCallback(() => {
    setErrored(false);
    setLoading(true);
    webRef.current?.reload();
  }, []);

  return (
    <SafeAreaView style={[styles.safe, { backgroundColor: look.bg }]} edges={["top", "left", "right"]}>
      <Stack.Screen options={{ statusBarStyle: look.dark ? "light" : "dark" }} />
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
          <SelectionMenu style={styles.web} title="Read aloud" onSelect={readSelection}>
            <WebView
              ref={webRef}
              source={{ uri: startUrl }}
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
              mediaCapturePermissionGrantType="grantIfSameHostElsePrompt"
              mediaPlaybackRequiresUserAction={false}
              startInLoadingState={false}
              style={styles.web}
            />
          </SelectionMenu>
          {loading && (
            <View style={[styles.loading, { backgroundColor: look.bg }]} pointerEvents="none">
              <ActivityIndicator color={colors.accent} size="large" />
            </View>
          )}
        </>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1 },
  // See-through: page views sit under it, over the SafeAreaView's color.
  web: { flex: 1, backgroundColor: "transparent" },
  loading: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
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
