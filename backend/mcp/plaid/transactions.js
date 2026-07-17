#!/usr/bin/env node
// Print JSON: { transactions: [...] }
// Args: --days <N> (default 30)
// Reused by workshop-backend via subprocess.

import { Configuration, PlaidApi, PlaidEnvironments } from "plaid";
import { config as dotenvConfig } from "dotenv";
import { readFileSync, existsSync } from "node:fs";

dotenvConfig({ path: "/home/clawd-a/.config/clawdbot/plaid-auth.env" });

const TOKENS_PATH = "/home/clawd-a/.config/clawdbot/plaid-tokens.json";

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  if (i === -1) return fallback;
  return process.argv[i + 1];
}

function plaid() {
  const env = process.env.PLAID_ENV || "production";
  return new PlaidApi(new Configuration({
    basePath: PlaidEnvironments[env],
    baseOptions: { headers: {
      "PLAID-CLIENT-ID": process.env.PLAID_CLIENT_ID,
      "PLAID-SECRET": process.env.PLAID_SECRET,
    } },
  }));
}

async function main() {
  const days = parseInt(arg("--days", "30"), 10);
  if (!existsSync(TOKENS_PATH)) {
    console.log(JSON.stringify({ transactions: [] }));
    return;
  }
  const tokens = JSON.parse(readFileSync(TOKENS_PATH, "utf8"));
  const items = tokens.items || [];
  const now = new Date();
  const start = new Date(now.getTime() - days * 86400000);
  const fmt = (d) => d.toISOString().slice(0, 10);
  const client = plaid();
  // Fetch all items in PARALLEL — total latency is one bank's roundtrip, not the
  // sum across banks. Each item's transactions are flattened into `all` after.
  const perItem = await Promise.all(items.map(async (item) => {
    try {
      const r = await client.transactionsGet({
        access_token: item.access_token,
        start_date: fmt(start),
        end_date: fmt(now),
        options: { count: 500 },
      });
      return r.data.transactions.map((t) => ({
        id: t.transaction_id,
        item_id: item.item_id,
        institution_name: item.institution_name || null,
        account_id: t.account_id,
        date: t.date,
        name: t.name,
        merchant_name: t.merchant_name,
        amount: t.amount,
        iso_currency_code: t.iso_currency_code,
        category: t.personal_finance_category?.primary || (t.category || []).join(" / "),
        category_detailed: t.personal_finance_category?.detailed || null,
        pending: t.pending,
      }));
    } catch (e) {
      // Skip items with errors but keep going.
      process.stderr.write(`item ${item.item_id}: ${e?.response?.data?.error_message || e?.message || e}\n`);
      return [];
    }
  }));
  const all = perItem.flat();
  all.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  console.log(JSON.stringify({ transactions: all }));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
