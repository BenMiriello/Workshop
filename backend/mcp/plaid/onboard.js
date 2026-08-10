#!/usr/bin/env node
// Plaid Hosted Link onboarding.
//
// Usage:
//   node onboard.js create               -> creates link_token, prints hosted URL + link_token
//   node onboard.js complete <link_token> [label]   -> polls until session finishes, exchanges public_token, saves
//   node onboard.js list                 -> show connected institutions
//   node onboard.js remove <item_id>     -> remove a connected institution
//   node onboard.js rotate <item_id>     -> invalidate + replace the access_token (incident response)
//
// Reads Plaid credentials from $WORKSHOP_CONFIG_DIR/plaid-auth.env
// Persists access_tokens to $WORKSHOP_CONFIG_DIR/plaid-tokens.json (mode 600).

import { Configuration, PlaidApi, PlaidEnvironments, Products, CountryCode } from "plaid";
import { config as dotenvConfig } from "dotenv";
import { readFileSync, writeFileSync, existsSync, chmodSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import path from "node:path";
import os from "node:os";

const CONFIG_DIR = process.env.WORKSHOP_CONFIG_DIR
  || path.join(os.homedir(), ".config", "workshop");


dotenvConfig({ path: path.join(CONFIG_DIR, "plaid-auth.env") });

const TOKENS_PATH = path.join(CONFIG_DIR, "plaid-tokens.json");

function plaidClient() {
  const env = process.env.PLAID_ENV || "production";
  return new PlaidApi(new Configuration({
    basePath: PlaidEnvironments[env],
    baseOptions: {
      headers: {
        "PLAID-CLIENT-ID": process.env.PLAID_CLIENT_ID,
        "PLAID-SECRET": process.env.PLAID_SECRET,
      },
    },
  }));
}

function loadTokens() {
  if (!existsSync(TOKENS_PATH)) return { version: 1, items: [] };
  return JSON.parse(readFileSync(TOKENS_PATH, "utf-8"));
}

function saveTokens(tokens) {
  mkdirSync(dirname(TOKENS_PATH), { recursive: true });
  writeFileSync(TOKENS_PATH, JSON.stringify(tokens, null, 2) + "\n", { mode: 0o600 });
  chmodSync(TOKENS_PATH, 0o600);
}

async function cmdCreate() {
  const client = plaidClient();
  const resp = await client.linkTokenCreate({
    user: { client_user_id: process.env.WORKSHOP_PLAID_USER_ID || "workshop" },
    client_name: process.env.WORKSHOP_PLAID_CLIENT_NAME || "Workshop",
    products: [Products.Transactions, Products.Investments],
    country_codes: [CountryCode.Us],
    language: "en",
    hosted_link: { url_lifetime_seconds: 86400 },
  });
  console.log(JSON.stringify({
    hosted_link_url: resp.data.hosted_link_url,
    link_token: resp.data.link_token,
    expiration: resp.data.expiration,
    next: "Open hosted_link_url in browser, log into bank, then run: node onboard.js complete <link_token> [label]",
  }, null, 2));
}

async function cmdComplete(linkToken, label) {
  if (!linkToken) {
    console.error("Usage: node onboard.js complete <link_token> [label]");
    process.exit(2);
  }
  const client = plaidClient();
  const maxPolls = 180;
  const intervalMs = 5000;
  let session = null;
  for (let i = 0; i < maxPolls; i++) {
    const info = await client.linkTokenGet({ link_token: linkToken });
    const sessions = info.data.link_sessions || [];
    const finished = sessions.find(s => s.results?.item_add_results?.length);
    if (finished) {
      session = finished;
      break;
    }
    if (i === 0) console.error(`Waiting for bank login (poll up to ${(maxPolls * intervalMs) / 60000} min)...`);
    await new Promise(r => setTimeout(r, intervalMs));
  }
  if (!session) {
    console.error("Timed out waiting for Hosted Link session to complete.");
    process.exit(3);
  }
  const itemAdd = session.results.item_add_results[0];
  const publicToken = itemAdd.public_token;
  const institutionId = itemAdd.institution_id || null;
  const exchange = await client.itemPublicTokenExchange({ public_token: publicToken });
  const accessToken = exchange.data.access_token;
  const itemId = exchange.data.item_id;

  let institutionName = label || null;
  if (institutionId && !institutionName) {
    try {
      const inst = await client.institutionsGetById({
        institution_id: institutionId,
        country_codes: [CountryCode.Us],
      });
      institutionName = inst.data.institution.name;
    } catch (_) {
      institutionName = institutionId;
    }
  }

  const tokens = loadTokens();
  tokens.items = tokens.items.filter(it => it.item_id !== itemId);
  tokens.items.push({
    item_id: itemId,
    access_token: accessToken,
    institution_id: institutionId,
    institution_name: institutionName || "Unknown",
    added_at: new Date().toISOString(),
  });
  saveTokens(tokens);
  console.log(JSON.stringify({
    ok: true,
    item_id: itemId,
    institution: institutionName,
    tokens_file: TOKENS_PATH,
    next: "Restart the backend to load the new token: systemctl --user restart workshop-backend.service",
  }, null, 2));
}

async function cmdList() {
  const tokens = loadTokens();
  console.log(JSON.stringify({
    count: tokens.items.length,
    items: tokens.items.map(it => ({
      item_id: it.item_id,
      institution: it.institution_name,
      added_at: it.added_at,
    })),
  }, null, 2));
}

async function cmdRemove(itemId) {
  if (!itemId) {
    console.error("Usage: node onboard.js remove <item_id>");
    process.exit(2);
  }
  const tokens = loadTokens();
  const it = tokens.items.find(x => x.item_id === itemId);
  if (!it) {
    console.error(`No item with id ${itemId}`);
    process.exit(4);
  }
  const client = plaidClient();
  try {
    await client.itemRemove({ access_token: it.access_token });
  } catch (e) {
    console.error("Plaid /item/remove failed (continuing local removal):", e.response?.data || e.message);
  }
  tokens.items = tokens.items.filter(x => x.item_id !== itemId);
  saveTokens(tokens);
  console.log(JSON.stringify({ ok: true, removed: itemId }, null, 2));
}

// Incident response: invalidate a possibly-exposed access_token and swap in the
// fresh one Plaid returns. The old token stops working immediately, so a leaked
// copy (backup, log, disk image) is rendered useless. Item stays linked — no
// re-auth at the bank needed. Run this if plaid-tokens.json may have leaked.
async function cmdRotate(itemId) {
  if (!itemId) {
    console.error("Usage: node onboard.js rotate <item_id>   (use `list` to see ids; omit id to rotate ALL)");
  }
  const tokens = loadTokens();
  const targets = itemId ? tokens.items.filter(x => x.item_id === itemId) : tokens.items;
  if (!targets.length) {
    console.error(itemId ? `No item with id ${itemId}` : "No linked items to rotate.");
    process.exit(4);
  }
  const client = plaidClient();
  const rotated = [];
  for (const it of targets) {
    const resp = await client.itemAccessTokenInvalidate({ access_token: it.access_token });
    it.access_token = resp.data.new_access_token;
    it.rotated_at = new Date().toISOString();
    rotated.push({ item_id: it.item_id, institution: it.institution_name });
  }
  saveTokens(tokens);
  console.log(JSON.stringify({
    ok: true,
    rotated,
    note: "Old access_token(s) invalidated; new ones saved (mode 0600). Restart the backend to reload: systemctl --user restart workshop-backend.service",
  }, null, 2));
}

const [, , cmd, ...args] = process.argv;
const handlers = {
  create: () => cmdCreate(),
  complete: () => cmdComplete(args[0], args[1]),
  list: () => cmdList(),
  remove: () => cmdRemove(args[0]),
  rotate: () => cmdRotate(args[0]),
};
const handler = handlers[cmd];
if (!handler) {
  console.error("Usage: node onboard.js {create|complete <link_token> [label]|list|remove <item_id>|rotate <item_id>}");
  process.exit(1);
}
handler().catch(e => {
  console.error("Error:", e.response?.data || e.message || e);
  process.exit(1);
});
