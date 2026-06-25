import { useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { router } from "expo-router";

import { API_BASE, TIER, WEB_URL } from "../native/config";
import { colors } from "../lib/api";
import * as location from "../native/location";
import * as notify from "../native/notify";
import * as liveActivity from "../native/liveActivity";
import { clearToken, setDeviceToken } from "../native/auth";

// On-device verification of each capability without the web app. Reach it from
// the WebView error screen ("Diagnostics"), or route to /debug directly.

export default function Debug() {
  const [log, setLog] = useState<string[]>([]);
  const [tokenInput, setTokenInput] = useState("");
  const append = (s: string) => setLog((l) => [`${new Date().toLocaleTimeString()}  ${s}`, ...l].slice(0, 50));

  const run = (label: string, fn: () => Promise<unknown>) => async () => {
    append(`${label}…`);
    try {
      const r = await fn();
      append(`${label}  OK ${r === undefined ? "" : JSON.stringify(r)}`.trim());
    } catch (e) {
      append(`${label}  ERR ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const actions: Array<[string, () => Promise<void>]> = [
    ["location.getCurrent", run("getCurrent", location.getCurrent)],
    ["location.start (active)", run("startTracking", () => location.startBackgroundTracking({ mode: "active" }))],
    ["location.status", run("status", location.trackingStatus)],
    ["location.stop", run("stopTracking", location.stopBackgroundTracking)],
    ["notify in 10s", run("schedule", () =>
      notify.schedule({
        id: "debug-test",
        title: "Workshop",
        body: "Local notification test",
        fireAt: new Date(Date.now() + 10_000).toISOString(),
      }),
    )],
    ["liveActivity.isSupported", run("liveActivity", liveActivity.isSupported)],
  ];

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Text style={styles.link}>Back</Text>
        </Pressable>
        <Text style={styles.title}>Diagnostics</Text>
      </View>

      <View style={styles.kv}>
        <Text style={styles.muted}>tier</Text>
        <Text style={styles.mono}>{TIER}</Text>
        <Text style={styles.muted}>webUrl</Text>
        <Text style={styles.mono}>{WEB_URL}</Text>
        <Text style={styles.muted}>apiBase</Text>
        <Text style={styles.mono}>{API_BASE}</Text>
      </View>

      <View style={styles.actions}>
        {actions.map(([label, fn]) => (
          <Pressable key={label} style={styles.btn} onPress={fn}>
            <Text style={styles.btnText}>{label}</Text>
          </Pressable>
        ))}
      </View>

      <View style={styles.tokenRow}>
        <TextInput
          style={styles.input}
          placeholder="API token (off-tailnet only)"
          placeholderTextColor={colors.muted}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          value={tokenInput}
          onChangeText={setTokenInput}
        />
        <Pressable
          style={styles.btn}
          onPress={() => {
            void setDeviceToken(tokenInput.trim()).then(() => append("device token saved (reload app)"));
          }}
        >
          <Text style={styles.btnText}>Save</Text>
        </Pressable>
        <Pressable
          style={styles.btn}
          onPress={() => {
            setTokenInput("");
            void clearToken().then(() => append("token cleared (reload app)"));
          }}
        >
          <Text style={styles.btnText}>Clear</Text>
        </Pressable>
      </View>

      <ScrollView style={styles.logBox}>
        {log.map((line, i) => (
          <Text key={i} style={styles.logLine}>{line}</Text>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg, padding: 16 },
  header: { flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 12 },
  link: { color: colors.accent, fontSize: 16 },
  title: { color: colors.fg, fontSize: 18, fontWeight: "600" },
  kv: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginBottom: 12 },
  muted: { color: colors.muted, fontSize: 12, width: 60 },
  mono: { color: colors.fg, fontSize: 12, fontFamily: "Menlo", flexShrink: 1 },
  actions: { gap: 8, marginBottom: 12 },
  tokenRow: { flexDirection: "row", gap: 8, marginBottom: 12, alignItems: "center" },
  input: {
    flex: 1,
    color: colors.fg,
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 10,
    fontSize: 13,
  },
  btn: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 12 },
  btnText: { color: colors.fg, fontSize: 14 },
  logBox: { flex: 1, backgroundColor: colors.card, borderRadius: 8, padding: 10 },
  logLine: { color: colors.muted, fontSize: 12, fontFamily: "Menlo", marginBottom: 3 },
});
