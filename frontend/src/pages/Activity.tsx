import { useState } from "react";
import Services from "./Services";
import Events from "./Events";
import Jobs from "./Jobs";
import { Tabs, type TabItem } from "../ui";

type Tab = "services" | "events" | "jobs";

const TABS: TabItem<Tab>[] = [
  { value: "services", label: "Services" },
  { value: "events", label: "Events" },
  { value: "jobs", label: "Jobs" },
];

export default function Activity() {
  const initial: Tab = (() => {
    const h = window.location.hash.replace("#", "");
    return TABS.find((t) => t.value === h)?.value || "services";
  })();
  const [tab, setTab] = useState<Tab>(initial);

  const select = (t: Tab) => {
    setTab(t);
    window.history.replaceState(null, "", `#${t}`);
  };

  return (
    <div>
      <Tabs items={TABS} value={tab} onChange={select} />
      <div style={{ marginTop: "var(--space-5)" }}>
        {tab === "services" && <Services />}
        {tab === "events" && <Events />}
        {tab === "jobs" && <Jobs />}
      </div>
    </div>
  );
}
