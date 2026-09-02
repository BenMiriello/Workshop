// This app's frontend declaration: its routes, breadcrumbs, icon and card status.
//
// Moved out of `src/apps/registry.tsx` on 2026-09-02, where a literal APPS
// array named every app, so the shell hardcoded which apps exist. The registry
// discovers these modules now and names none. See `src/apps/appModule.ts`.

import { Route } from "react-router-dom";
import { timeAgoSeconds } from "@workshop/ui";
import type { AppModule, AppStatus } from "../../../apps/appModule";
import { AppGroupNav, makeChatDock } from "../../../apps/appShell";
import { lazyPage } from "../../../lib/lazyPage";

const Finance = lazyPage(() => import("../Finance"));

// A CARD READS THIS APP'S OWN ENDPOINTS. The launcher used to switch on app ids
// and fetch each app's data itself; an app knows its own summary.
async function status(): Promise<AppStatus> {
  try {
    const [statusR, balR] = await Promise.all([
      fetch("/api/finance/cache"),
      fetch("/api/finance/balances"),
    ]);
    // The balances route carries the answer, so a failure there is a failure of
    // the card. Substituting an empty roster rendered a 502 from the bank as
    // the sentence "0 accounts", and a card must not report as fact something
    // it could not ask about. The cache route is diagnostics and supplies only
    // the "updated" half, so losing THAT drops that half and keeps the count.
    if (!balR.ok) return { text: "unavailable", tone: "err" };
    const bal = await balR.json();
    const cache = statusR.ok ? await statusR.json() : { entries: {} };
    const n = (bal.items || []).reduce(
      (s: number, it: { accounts?: unknown[] }) => s + (it.accounts?.length || 0),
      0,
    );
    const entry = cache.entries?.["balances"];
    const age = entry ? Math.max(0, entry.age_s) : null;
    return {
      text: `${n} account${n === 1 ? "" : "s"} · ${age != null ? `updated ${timeAgoSeconds(age)}` : "no data yet"}`,
      tone: "muted",
    };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

const id = "finance";
const home = `/apps/${id}`;

export const appModule: AppModule = {
  id,
  order: 20,
  icon: "banknote",
  status,
  group: "money",
  link: { label: "Finance", to: home },
  crumbs: { [home]: { label: "Finance", parent: "/apps" } },
  routes: () => (
    <Route
      key={id}
      path={home}
      element={<Finance groupNav={<AppGroupNav />} renderChatDock={makeChatDock(id)} />}
    />
  ),
};
