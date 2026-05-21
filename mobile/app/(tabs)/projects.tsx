import { useEffect, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { api, colors } from "../../lib/api";
import type { Project } from "@shared/types";

export default function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = () => api<Project[]>("/projects/").then(setProjects).catch((e) => setErr(String(e)));
  useEffect(() => { load(); }, []);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  if (err) return <View style={s.container}><Text style={s.err}>{err}</Text></View>;
  if (!projects) return <View style={s.container}><Text style={s.muted}>Loading…</Text></View>;

  return (
    <FlatList
      style={s.container}
      data={projects}
      keyExtractor={(p) => p.path}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />}
      renderItem={({ item }) => (
        <View style={s.card}>
          <Text style={s.name}>{item.name}</Text>
          {item.branch && <Text style={s.muted}>{item.branch} · {item.commit?.slice(0, 8)}</Text>}
          {item.subject && <Text style={s.subject}>{item.subject}</Text>}
          {item.ts && <Text style={s.muted}>{item.ts}</Text>}
        </View>
      )}
      ListEmptyComponent={<Text style={s.muted}>No git repos under projects root.</Text>}
    />
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: 16 },
  card: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 12, marginBottom: 12 },
  name: { color: colors.fg, fontSize: 16, fontWeight: "600", marginBottom: 4 },
  subject: { color: colors.fg, fontSize: 14, marginTop: 4 },
  muted: { color: colors.muted, fontSize: 13 },
  err: { color: colors.err },
});
