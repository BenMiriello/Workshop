// The Overview tab: what this app IS, and the four controls that had no other
// home once /projects went.
//
// threads-branches-and-pages-2026-08-29.md section 6 lists the per-app pages as
// "Overview, Work, Chats, Memory, Files, Settings", and section 7 step 4 is
// "`ProjectDetail` and `/projects` are deleted; app Overview absorbs what they
// had." This is the absorbing half, and it is a prerequisite for the deleting
// half rather than a nicety: ProjectDetail was 1801 lines and four of the
// things it mounted (autonomy, services, deploys, environment) existed nowhere
// else, so deleting it first would have removed working controls and put
// nothing in their place.
//
// WHY THIS IS NOT THE FIRST TAB, even though section 6 lists it first. That
// document's own addendum supersedes its ordering: "Work (tickets) is the main
// list... it is not where you start your day" is said of BRANCHES, and the same
// reasoning puts a settings-shaped page after the work. An app opens on its
// first tab (backend/services/app_surface.py), and the operator's report on
// 2026-08-29, 08-31 and twice on 09-01 was that apps opened somewhere he did
// not want. Work stays first; Overview sits beside it.
//
// THE PANELS ARE MOUNTED, NOT REDRAWN. Each is the same component ProjectDetail
// mounted, taking the same `slug`. A second drawing of a deploy list is exactly
// the duplication this whole phase is removing.
import { useParams } from "react-router-dom";
import { Boxes } from "lucide-react";
import { Card, Prop, Props, SectionLabel, StateBlock, Tag, catFor } from "@workshop/ui";

import AutonomyPanel from "../../../components/AutonomyPanel";
import AppServicesPanel from "../../../components/AppServicesPanel";
import DeploysPanel from "../../../components/DeploysPanel";
import EnvironmentPanel from "../../../components/EnvironmentPanel";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage } from "./AppTabShell";

function OverviewBody({ app }: { app: AppRecord }) {
  const slug = app.project_slug;

  // AN APP WITH NO WORKING TREE HAS NONE OF THIS. Every one of the four panels
  // reads /api/projects/<slug>/..., so without a linked tree each would render
  // its own error and the page would be four error regions stacked.
  if (!slug) {
    return (
      <StateBlock
        title="Nothing to show yet"
        // Its own sentence rather than `unlinkedReason("overview", ...)`. That
        // helper names the COLLECTION a tab is short of ("no branches yet",
        // "no tickets yet"), and this tab is not a collection: it is four
        // controls over a working tree. "This app has no settings yet" would be
        // a true-shaped sentence about the wrong thing.
        hint="This app has no linked working tree, so it has nothing to run, deploy or configure."
      />
    );
  }

  return (
    <>
      <section>
        <SectionLabel icon={<Boxes aria-hidden />}>This app</SectionLabel>
        <Card>
          <Props>
            <Prop label="Name">{app.name}</Prop>
            <Prop label="Working tree">
              <Tag hue={catFor(slug)} variant="neutral">{slug}</Tag>
            </Prop>
          </Props>
        </Card>
      </section>

      {/* The autonomy brake. It used to sit in two places (here and the top of
          the Deploys page); the Deploys page is going, so this is the one. */}
      <AutonomyPanel slug={slug} />
      <AppServicesPanel slug={slug} />
      <DeploysPanel slug={slug} />
      <EnvironmentPanel slug={slug} />
    </>
  );
}

export default function AppOverviewTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage
      slug={appSlug}
      title="Overview"
      sub="What this app is, and how it runs."
    >
      {(app) => <OverviewBody app={app} />}
    </AppTabPage>
  );
}
