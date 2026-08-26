import { AppsCard } from "../components/dashboard/AppsCard";
import { ContinueCard } from "../components/dashboard/ContinueCard";
import { NeedsYouCard } from "../components/dashboard/NeedsYouCard";
import { RunningCard } from "../components/dashboard/RunningCard";
import { useThreads } from "../components/dashboard/useThreads";

// THE DASHBOARD. Home's first page and what the root opens to.
//
// Four fixed-height cards in one grid: the apps, what needs you, what you were
// doing, and what is running. Design record:
// docs/platform-redesign/dashboard-2026-08-25.md.
//
// NO PAGE HEADER, on purpose and at both widths. The shell already says where
// you are -- the page column carries the "Dashboard" row on a desktop and bar
// one carries "Workshop" on a phone -- so an <h1> here is the second telling.
// That duplication is what a screenshot pass found stacked ~290px of chrome
// above the first row on Files and on Memory (2026-08-23); it is not repeated.
//
// The composer the mockup draws under this grid is NOT built. It is the input
// system and it is its own stage; the page ends at the bottom of the grid.
//
// Each card owns its own read and its own poll, so a failed endpoint degrades
// one card into its error state rather than blanking the page. See
// lib/dashboard for the reads and every derivation behind them.

export default function Home() {
  // ONE threads read, two consumers: the chats card lists them and the running
  // card wants the ones with a turn in flight. See useThreads -- everything
  // else stays per-card.
  const threads = useThreads();
  return (
    <div className="ws-dashboard">
      <div className="ws-dashboard__grid">
        <AppsCard />
        <NeedsYouCard />
        <ContinueCard read={threads} />
        <RunningCard read={threads} />
      </div>
    </div>
  );
}
