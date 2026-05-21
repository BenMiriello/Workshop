import { useEffect, useMemo, useState } from "react";
import { FlatList, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { api, colors } from "../../lib/api";

type Account = {
  id: string; name: string; type: string; subtype?: string; mask?: string;
  balance: { current: number | null; available: number | null; iso_currency_code: string | null };
};
type Item = { item_id: string; institution_name: string | null; accounts: Account[]; error?: string };
type Tx = { id: string; date: string; name: string; merchant_name?: string; amount: number; iso_currency_code?: string; category?: string; pending?: boolean };

const fmt = (n: number | null, ccy: string | null) => {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: ccy || "USD" }).format(n);
};

export default function Apps() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [txns, setTxns] = useState<Tx[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    try {
      const [b, t] = await Promise.all([
        api<{ items: Item[] }>("/finance/balances"),
        api<{ transactions: Tx[] }>("/finance/transactions?days=30"),
      ]);
      setItems(b.items || []);
      setTxns(t.transactions || []);
    } catch (e) {
      setErr(String(e));
    }
  };
  useEffect(() => { load(); }, []);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const liquid = useMemo(() => {
    if (!items) return null;
    return items.flatMap((i) => i.accounts).filter((a) => a.type === "depository").reduce((sum, a) => sum + (a.balance.current ?? 0), 0);
  }, [items]);

  if (err) return <View style={s.container}><Text style={s.err}>{err}</Text></View>;
  if (!items || !txns) return <View style={s.container}><Text style={s.muted}>Loading…</Text></View>;

  const accounts = items.flatMap((item) => item.accounts.map((a) => ({ ...a, institution: item.institution_name })));

  return (
    <ScrollView
      style={s.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />}
    >
      <Text style={s.section}>Finance</Text>
      {liquid != null && <Text style={s.muted}>Liquid total: {fmt(liquid, "USD")}</Text>}

      {accounts.map((a) => (
        <View key={a.id} style={s.card}>
          <Text style={s.name}>{a.name}</Text>
          <Text style={s.muted}>{a.institution} · {a.subtype || a.type}{a.mask ? ` · ··${a.mask}` : ""}</Text>
          <Text style={s.balance}>{fmt(a.balance.current, a.balance.iso_currency_code)}</Text>
        </View>
      ))}

      <Text style={s.section}>Recent (30d)</Text>
      <FlatList
        scrollEnabled={false}
        data={txns.slice(0, 50)}
        keyExtractor={(t) => t.id}
        renderItem={({ item }) => (
          <View style={[s.txn, item.pending && { opacity: 0.6 }]}>
            <View style={{ flex: 1 }}>
              <Text style={s.fg}>{item.merchant_name || item.name}</Text>
              <Text style={s.muted}>{item.date}  ·  {item.category || ""}</Text>
            </View>
            <Text style={s.fg}>{fmt(-item.amount, item.iso_currency_code || "USD")}</Text>
          </View>
        )}
      />

      <Text style={s.section}>Health</Text>
      <Text style={s.muted}>Open Wearables endpoint not yet wired into Workshop API.</Text>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: 16 },
  section: { color: colors.fg, fontSize: 18, fontWeight: "600", marginTop: 16, marginBottom: 8 },
  card: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 12, marginBottom: 8 },
  name: { color: colors.fg, fontSize: 15, fontWeight: "500" },
  balance: { color: colors.fg, fontSize: 20, marginTop: 4 },
  muted: { color: colors.muted, fontSize: 13 },
  fg: { color: colors.fg, fontSize: 14 },
  err: { color: colors.err },
  txn: { flexDirection: "row", paddingVertical: 8, borderBottomColor: colors.border, borderBottomWidth: 1 },
});
