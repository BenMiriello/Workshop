#!/usr/bin/env node
// Print JSON: { items: [{ item_id, institution_name, accounts: [{ id, name, type, subtype, balance: { current, available, iso_currency_code } }] }] }
// Reused by workshop-backend via subprocess. Self-contained, no MCP framing.
//
// Default uses Plaid's `accountsGet` (the balances Plaid already holds — sub-second,
// refreshed by Plaid on access/webhook). Pass `--realtime` to force `accountsBalanceGet`,
// which opens a live connection to each bank (accurate to the second but ~3-4s PER item).
// Page loads should use the default; an explicit "Refresh" uses --realtime.
//
// Either way all items are fetched in PARALLEL (Promise.all) instead of serially,
// so total latency is one bank's roundtrip, not the sum across banks.

import { Configuration, PlaidApi, PlaidEnvironments } from "plaid";
import { config as dotenvConfig } from "dotenv";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import os from "node:os";

const CONFIG_DIR = process.env.WORKSHOP_CONFIG_DIR
  || path.join(os.homedir(), ".config", "workshop");


dotenvConfig({ path: path.join(CONFIG_DIR, "plaid-auth.env") });

const TOKENS_PATH = path.join(CONFIG_DIR, "plaid-tokens.json");

const REALTIME = process.argv.includes("--realtime");

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

async function fetchItem(client, item) {
  try {
    const r = await (REALTIME
      ? client.accountsBalanceGet({ access_token: item.access_token })
      : client.accountsGet({ access_token: item.access_token }));
    return {
      item_id: item.item_id,
      institution_name: item.institution_name || null,
      accounts: r.data.accounts.map((a) => ({
        id: a.account_id,
        name: a.name,
        official_name: a.official_name,
        type: a.type,
        subtype: a.subtype,
        mask: a.mask,
        balance: {
          current: a.balances.current,
          available: a.balances.available,
          iso_currency_code: a.balances.iso_currency_code,
          limit: a.balances.limit,
        },
      })),
    };
  } catch (e) {
    return {
      item_id: item.item_id,
      institution_name: item.institution_name || null,
      error: e?.response?.data?.error_message || String(e?.message || e),
      accounts: [],
    };
  }
}

async function main() {
  if (!existsSync(TOKENS_PATH)) {
    console.log(JSON.stringify({ items: [] }));
    return;
  }
  const tokens = JSON.parse(readFileSync(TOKENS_PATH, "utf8"));
  const items = tokens.items || [];
  const client = plaid();
  const out = await Promise.all(items.map((item) => fetchItem(client, item)));
  console.log(JSON.stringify({ items: out }));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
