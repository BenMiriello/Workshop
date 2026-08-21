import type { ReactNode } from "react";
import { AppSettings, PageHeader, type AppChatDockRenderer } from "@workshop/ui";
import CommutePages from "./commute/CommutePages";
import CommuteSettings from "./commute/CommuteSettings";
import { useCommuteNative } from "./commute/useCommuteNative";

// Commute is a first-class Workshop app (like Finance / Budget / Location /
// Birds) — its source lives here under pages/apps/commute. The page header +
// linked-app switcher match the other apps; CommutePages owns the tabbed body
// (Status / Schedule / Sources / Alerts).
//
// `groupNav` and `renderChatDock` are supplied by the shell
// (`apps/registry.tsx`, the one file allowed to know this app's id and its
// switcher group): both need platform knowledge this page must not import
// directly. See docs/platform-redesign/HANDOFF-app-platform-and-github-2026-08-20.md
// "Stage 4b".
export default function Commute({
  groupNav,
  renderChatDock,
}: {
  groupNav?: ReactNode;
  renderChatDock?: AppChatDockRenderer;
}) {
  // In the mobile shell: schedule local "leave now" notifications + drive the
  // lock-screen Live Activity countdown from the commute brief. No-op in a browser.
  useCommuteNative();

  return (
    <div>
      {groupNav}
      <PageHeader title="Commute" />
      <CommutePages />
      <AppSettings>
        <CommuteSettings />
      </AppSettings>
      {renderChatDock?.({
        title: "Commute assistant",
        placeholder: "Ask about departures or transit status…",
      })}
    </div>
  );
}
