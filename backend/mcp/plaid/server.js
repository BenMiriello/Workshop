#!/usr/bin/env node
// Plaid MCP server. Single-operator, single-file.
//
// Reads credentials from $WORKSHOP_CONFIG_DIR/plaid-auth.env
// Reads connected-bank access_tokens from $WORKSHOP_CONFIG_DIR/plaid-tokens.json
//
// Tools:
//   list_connections                       — show linked banks
//   get_balances [item_id]                 — balances across accounts
//   list_transactions [days] [item_id]     — recent transactions
//   search_transactions {query, days, item_id}  — filter by text/category
//   get_holdings [item_id]                 — investment positions
//   list_investment_transactions [days]    — buy/sell/dividends
//   refresh_transactions [item_id]         — kick Plaid to fetch latest

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { Configuration, PlaidApi, PlaidEnvironments, CountryCode } from "plaid";
import { config as dotenvConfig } from "dotenv";
import { readFileSync, existsSync } from "node:fs";
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

function resolveItems(itemId) {
  const tokens = loadTokens();
  if (!tokens.items.length) {
    throw new Error("No banks linked yet. Run: cd ~/clawdbot/plaid-mcp && node onboard.js create");
  }
  if (itemId) {
    const found = tokens.items.find(it => it.item_id === itemId);
    if (!found) throw new Error(`No linked bank with item_id=${itemId}. Use list_connections.`);
    return [found];
  }
  return tokens.items;
}

function isoDaysAgo(n) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

const client = plaidClient();

// ─── Tool implementations ────────────────────────────────────────────────────

async function toolListConnections() {
  const tokens = loadTokens();
  return {
    count: tokens.items.length,
    items: tokens.items.map(it => ({
      item_id: it.item_id,
      institution: it.institution_name,
      added_at: it.added_at,
    })),
    note: tokens.items.length === 0
      ? "No banks linked. Run `cd ~/clawdbot/plaid-mcp && node onboard.js create` to start."
      : undefined,
  };
}

async function toolGetBalances({ item_id } = {}) {
  const items = resolveItems(item_id);
  const out = [];
  for (const it of items) {
    const resp = await client.accountsBalanceGet({ access_token: it.access_token });
    out.push({
      institution: it.institution_name,
      item_id: it.item_id,
      accounts: resp.data.accounts.map(a => ({
        account_id: a.account_id,
        name: a.name,
        official_name: a.official_name,
        type: a.type,
        subtype: a.subtype,
        mask: a.mask,
        balance: {
          available: a.balances.available,
          current: a.balances.current,
          limit: a.balances.limit,
          iso_currency_code: a.balances.iso_currency_code,
        },
      })),
    });
  }
  return out;
}

async function toolListTransactions({ days = 30, item_id, count = 25, offset = 0, detail = false } = {}) {
  const items = resolveItems(item_id);
  const capped = Math.min(count, 100);
  const start = isoDaysAgo(days);
  const end = today();
  const out = [];
  for (const it of items) {
    const resp = await client.transactionsGet({
      access_token: it.access_token,
      start_date: start,
      end_date: end,
      options: { count: capped, offset },
    });
    const txns = resp.data.transactions.map(t => detail ? t : {
      date: t.date,
      name: t.name,
      merchant: t.merchant_name,
      amount: t.amount,
      currency: t.iso_currency_code,
      category: t.personal_finance_category?.detailed || t.category?.[0],
      pending: t.pending || undefined,
      id: t.transaction_id,
    });
    const has_more = offset + txns.length < resp.data.total_transactions;
    out.push({
      institution: it.institution_name,
      item_id: it.item_id,
      range: { start, end },
      total: resp.data.total_transactions,
      returned: txns.length,
      next_offset: has_more ? offset + txns.length : null,
      transactions: txns,
    });
  }
  return out;
}

async function toolSearchTransactions({ query, days = 90, item_id, limit = 50 }) {
  if (!query || typeof query !== "string") {
    throw new Error("`query` (string) is required");
  }
  const items = resolveItems(item_id);
  const cappedLimit = Math.min(limit, 200);
  const start = isoDaysAgo(days);
  const end = today();
  const q = query.toLowerCase();
  const matches = [];
  let truncated = false;
  for (const it of items) {
    let offset = 0;
    const pageSize = 500;
    while (matches.length < cappedLimit) {
      const resp = await client.transactionsGet({
        access_token: it.access_token,
        start_date: start,
        end_date: end,
        options: { count: pageSize, offset },
      });
      for (const t of resp.data.transactions) {
        if (matches.length >= cappedLimit) { truncated = true; break; }
        const haystack = [
          t.name,
          t.merchant_name,
          t.personal_finance_category?.primary,
          t.personal_finance_category?.detailed,
          ...(t.category || []),
        ].filter(Boolean).join(" ").toLowerCase();
        if (haystack.includes(q)) {
          matches.push({
            date: t.date,
            name: t.name,
            merchant: t.merchant_name,
            amount: t.amount,
            currency: t.iso_currency_code,
            category: t.personal_finance_category?.detailed,
            institution: it.institution_name,
            id: t.transaction_id,
          });
        }
      }
      offset += resp.data.transactions.length;
      if (offset >= resp.data.total_transactions || resp.data.transactions.length === 0) break;
    }
  }
  matches.sort((a, b) => b.date.localeCompare(a.date));
  return {
    query, days,
    count: matches.length,
    truncated_at_limit: truncated || undefined,
    matches,
  };
}

async function toolGetHoldings({ item_id } = {}) {
  const items = resolveItems(item_id);
  const out = [];
  for (const it of items) {
    let resp;
    try {
      resp = await client.investmentsHoldingsGet({ access_token: it.access_token });
    } catch (e) {
      const code = e.response?.data?.error_code;
      if (code === "PRODUCTS_NOT_SUPPORTED" || code === "NO_INVESTMENT_ACCOUNTS") {
        out.push({ institution: it.institution_name, item_id: it.item_id, error: code, message: "No investment accounts on this item." });
        continue;
      }
      throw e;
    }
    const securityById = Object.fromEntries(resp.data.securities.map(s => [s.security_id, s]));
    const accountById = Object.fromEntries(resp.data.accounts.map(a => [a.account_id, a]));
    out.push({
      institution: it.institution_name,
      item_id: it.item_id,
      holdings: resp.data.holdings.map(h => {
        const sec = securityById[h.security_id] || {};
        const acct = accountById[h.account_id] || {};
        return {
          account: acct.name,
          account_mask: acct.mask,
          ticker: sec.ticker_symbol,
          name: sec.name,
          type: sec.type,
          quantity: h.quantity,
          institution_price: h.institution_price,
          institution_value: h.institution_value,
          cost_basis: h.cost_basis,
          iso_currency_code: h.iso_currency_code,
        };
      }),
    });
  }
  return out;
}

async function toolListInvestmentTransactions({ days = 90, item_id, count = 25 } = {}) {
  count = Math.min(count, 100);
  const items = resolveItems(item_id);
  const start = isoDaysAgo(days);
  const end = today();
  const out = [];
  for (const it of items) {
    let resp;
    try {
      resp = await client.investmentsTransactionsGet({
        access_token: it.access_token,
        start_date: start,
        end_date: end,
        options: { count, offset: 0 },
      });
    } catch (e) {
      const code = e.response?.data?.error_code;
      if (code === "PRODUCTS_NOT_SUPPORTED" || code === "NO_INVESTMENT_ACCOUNTS") {
        out.push({ institution: it.institution_name, item_id: it.item_id, error: code });
        continue;
      }
      throw e;
    }
    const secById = Object.fromEntries(resp.data.securities.map(s => [s.security_id, s]));
    out.push({
      institution: it.institution_name,
      item_id: it.item_id,
      range: { start, end },
      total: resp.data.total_investment_transactions,
      transactions: resp.data.investment_transactions.map(t => ({
        date: t.date,
        type: t.type,
        subtype: t.subtype,
        amount: t.amount,
        quantity: t.quantity,
        price: t.price,
        fees: t.fees,
        ticker: secById[t.security_id]?.ticker_symbol,
        security_name: secById[t.security_id]?.name,
        iso_currency_code: t.iso_currency_code,
      })),
    });
  }
  return out;
}

async function toolRefreshTransactions({ item_id } = {}) {
  const items = resolveItems(item_id);
  const results = [];
  for (const it of items) {
    try {
      await client.transactionsRefresh({ access_token: it.access_token });
      results.push({ institution: it.institution_name, item_id: it.item_id, ok: true });
    } catch (e) {
      results.push({ institution: it.institution_name, item_id: it.item_id, ok: false, error: e.response?.data?.error_code || e.message });
    }
  }
  return { note: "Plaid refresh requested. New transactions typically arrive within minutes.", results };
}

// ─── MCP server wiring ───────────────────────────────────────────────────────

// `_meta["anthropic/maxResultSizeChars"]` is Claude Code v2.1.91+: when a tool
// result exceeds this size, Claude Code auto-persists the full result to disk
// and replaces it in-context with a file reference. Keeps long-running sessions
// from being polluted by oversized financial dumps.
const TOOLS = [
  {
    name: "list_connections",
    description: "Linked banks (item_id, institution, added_at). Cheap; call first if unsure what's connected.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    _meta: { "anthropic/maxResultSizeChars": 4000 },
  },
  {
    name: "get_balances",
    description: "Current balances per account at linked banks. Optional item_id scopes to one bank.",
    inputSchema: {
      type: "object",
      properties: { item_id: { type: "string", description: "Plaid item_id." } },
      additionalProperties: false,
    },
    _meta: { "anthropic/maxResultSizeChars": 8000 },
  },
  {
    name: "list_transactions",
    description: "Recent transactions. Defaults: 30 days, 25 most-recent. Use cursor for paging.",
    inputSchema: {
      type: "object",
      properties: {
        days: { type: "number", description: "Days back (default 30)." },
        count: { type: "number", description: "Max per bank (default 25, hard cap 100)." },
        offset: { type: "number", description: "Pagination offset (default 0)." },
        item_id: { type: "string", description: "Plaid item_id." },
        detail: { type: "boolean", description: "Include full transaction objects (default false; lean fields only)." },
      },
      additionalProperties: false,
    },
    _meta: { "anthropic/maxResultSizeChars": 25000 },
  },
  {
    name: "search_transactions",
    description: "Case-insensitive substring search across transaction name/merchant/category. Returns lean summaries.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Text to search." },
        days: { type: "number", description: "Days back (default 90)." },
        item_id: { type: "string", description: "Plaid item_id." },
        limit: { type: "number", description: "Max matches returned (default 50)." },
      },
      required: ["query"],
      additionalProperties: false,
    },
    _meta: { "anthropic/maxResultSizeChars": 15000 },
  },
  {
    name: "get_holdings",
    description: "Investment positions at linked brokerages.",
    inputSchema: {
      type: "object",
      properties: { item_id: { type: "string", description: "Plaid item_id." } },
      additionalProperties: false,
    },
    _meta: { "anthropic/maxResultSizeChars": 15000 },
  },
  {
    name: "list_investment_transactions",
    description: "Buy/sell/dividend/fee transactions. Defaults: 90 days, 25 most-recent.",
    inputSchema: {
      type: "object",
      properties: {
        days: { type: "number", description: "Days back (default 90)." },
        count: { type: "number", description: "Max per bank (default 25, hard cap 100)." },
        item_id: { type: "string", description: "Plaid item_id." },
      },
      additionalProperties: false,
    },
    _meta: { "anthropic/maxResultSizeChars": 15000 },
  },
  {
    name: "refresh_transactions",
    description: "Ask Plaid to refetch transactions from the bank now (vs. daily auto). Call only if operator says recent transactions are missing.",
    inputSchema: {
      type: "object",
      properties: { item_id: { type: "string", description: "Plaid item_id." } },
      additionalProperties: false,
    },
    _meta: { "anthropic/maxResultSizeChars": 2000 },
  },
];

const HANDLERS = {
  list_connections: toolListConnections,
  get_balances: toolGetBalances,
  list_transactions: toolListTransactions,
  search_transactions: toolSearchTransactions,
  get_holdings: toolGetHoldings,
  list_investment_transactions: toolListInvestmentTransactions,
  refresh_transactions: toolRefreshTransactions,
};

const server = new Server(
  { name: "plaid", version: "0.1.0" },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const handler = HANDLERS[req.params.name];
  if (!handler) {
    return {
      content: [{ type: "text", text: `Unknown tool: ${req.params.name}` }],
      isError: true,
    };
  }
  try {
    const result = await handler(req.params.arguments || {});
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  } catch (e) {
    const detail = e.response?.data || { message: e.message };
    return {
      content: [{ type: "text", text: `Error in ${req.params.name}: ${JSON.stringify(detail, null, 2)}` }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
