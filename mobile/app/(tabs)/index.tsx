import { useEffect, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { api, colors } from "../../lib/api";
import type { Agent } from "@shared/types";

export default function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = () => api<Agent[]>("/agents/").then(setAgents).catch((e) => setErr(String(e)));
  useEffect(() => { load(); }, []);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  if (err) return <View style={s.container}><Text style={s.err}>{err}</Text></View>;
  if (!agents) return <View style={s.container}><Text style={s.muted}>Loading…</Text></View>;

  return (
    <FlatList
      style={s.container}
      data={agents}
      keyExtractor={(a) => a.id}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />}
      renderItem={({ item }) => (
        <View style={s.card}>
          <Text style={s.name}>{item.name}</Text>
          <Text style={s.muted}>{item.id}</Text>
          <Text style={s.muted}>memory: {item.memory_blocks.join(", ") || "—"}</Text>
        </View>
      )}
      ListEmptyComponent={<Text style={s.muted}>No agents yet.</Text>}
    />
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: 16 },
  card: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 12, marginBottom: 12 },
  name: { color: colors.fg, fontSize: 16, fontWeight: "600", marginBottom: 4 },
  muted: { color: colors.muted, fontSize: 13 },
  err: { color: colors.err },
});
