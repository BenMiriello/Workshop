// The shell's live state: which place, which page, is the column open, and
// may this transition animate.
//
// Everything here that could be a rule instead of a hook already IS one, in
// `places.ts` and `shellState.ts`. What is left is the part that genuinely
// needs React: reading the router, persisting to storage, and the one piece of
// state that is about MOTION rather than about navigation.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Archive,
  Blocks,
  Bot,
  Brain,
  CircleUser,
  Cpu,
  FileText,
  FolderGit2,
  FolderTree,
  Paperclip,
  GitPullRequestArrow,
  Gauge,
  House,
  KeyRound,
  LayoutDashboard,
  LayoutGrid,
  ListChecks,
  MessagesSquare,
  Palette,
  PlayCircle,
  Rocket,
  Settings as SettingsIcon,
  Tags,
  Wrench,
} from "lucide-react";
import { appPlaces } from "../../apps/registry";
import { userStorage } from "../../lib/storage";
import { buildPlaces, pageFor, placeFor, type ShellPage, type ShellPlace } from "./places";
import { useTakeover } from "./takeover";
import {
  EMPTY_MEMORY,
  STORAGE_KEY,
  columnOpen as readColumnOpen,
  grantMotion,
  openPath,
  parseMemory,
  rememberPath,
  setColumnOpen,
  settleMotion,
  tabsKey,
  tabsOpen as readTabsOpen,
  toggleColumn,
  toggleTabs,
  type Motion,
  type ShellMemory,
} from "./shellState";

const ICONS = {
  home: House,
  dashboard: LayoutDashboard,
  chats: MessagesSquare,
  archived: Archive,
  projects: FolderGit2,
  apps: LayoutGrid,
  agents: Bot,
  tickets: ListChecks,
  changes: GitPullRequestArrow,
  runs: PlayCircle,
  deploys: Rocket,
  memory: Brain,
  files: FolderTree,
  // Attached to a chat, which is a paperclip and not a directory.
  attachments: Paperclip,
  usage: Gauge,
  settings: SettingsIcon,
  // Settings' own page rows.
  account: CircleUser,
  appearance: Palette,
  instructions: FileText,
  trackers: Tags,
  models: Cpu,
  mcp: Blocks,
  access: KeyRound,
  tools: Wrench,
};

export type Shell = {
  places: ShellPlace[];
  place: ShellPlace;
  page: ShellPage | null;
  columnOpen: boolean;
  /** True only for a transition that should animate. See `animate` below. */
  animating: boolean;
  /** A route that owns the whole screen: no page column, no mobile tab bar. */
  fullScreen: boolean;
  /** Whether this place has pages to switch BETWEEN. A place with one page has
   *  no page list, so the phone draws no tab bar and no caret for it: a bar
   *  holding a single always-active tab is not a switcher, it is a second copy
   *  of the title directly under the title, on the smallest screen there is.
   *  (The desktop column still opens on that place, because it is opt-in and
   *  it is the mechanism that will hold more rows later.) */
  hasPageList: boolean;
  tabsOpen: boolean;
  /** A document is open and owns the content area (mockup spec section 6). The
   *  page toolbar is force-collapsed while it is, and the caret that toggles
   *  the toolbar is not drawn: a control that cannot move is worse than none. */
  documentOpen: boolean;
  /** Tapping a dock tile or a menu row. Switches place, or toggles the column
   *  when it is the place you are already in. */
  selectPlace: (place: ShellPlace) => void;
  /** Where a tile POINTS, so it can be a real link (middle-click, copy link,
   *  hover preview) even though clicking it while you are there toggles the
   *  column instead of navigating. */
  hrefFor: (place: ShellPlace) => string;
  /** Tapping a page row. */
  selectPage: (page: ShellPage) => void;
  /** Make sure the page column is open, without flipping it shut for someone
   *  whose column already was. The directory takeover calls this: its tree is
   *  drawn in the column, and the control that asks for it can be in the
   *  content. */
  openColumn: () => void;
  toggleTabs: () => void;
};

export function useShell(): Shell {
  const { pathname } = useLocation();
  const navigate = useNavigate();

  // The app catalog cannot change without a reload, so this is built once.
  const places = useMemo(() => buildPlaces(appPlaces(), ICONS), []);

  const [memory, setMemory] = useState<ShellMemory>(() =>
    parseMemory(userStorage.local.getItem(STORAGE_KEY)),
  );
  useEffect(() => {
    if (memory === EMPTY_MEMORY) return; // nothing written yet, nothing to store
    userStorage.local.setItem(STORAGE_KEY, JSON.stringify(memory));
  }, [memory]);

  const place = useMemo(() => placeFor(places, pathname), [places, pathname]);
  const page = useMemo(() => pageFor(place, pathname), [place, pathname]);

  // ONE OPEN CONVERSATION OWNS THE SCREEN. It is not a page of All chats, it is
  // a record below it, and it has its own toolbar. The dock stays (it is the
  // way out); the column and the mobile tab bar go.
  const fullScreen = pathname.startsWith("/chat/");

  // MOTION IS NOT NAVIGATION STATE, which is why it is separate.
  //
  // "Toggling the same app animates. Switching apps HARD CUTS. This is
  // deliberate: motion means 'the same thing changed', not 'you moved'."
  // (Operator, sessions 2 and 4, raised repeatedly: the column may slide when
  // you open or close it WHERE YOU ARE, and never when a dock tile moves you
  // somewhere else.)
  //
  // THE CLEAR HAS TO HAPPEN DURING RENDER, NOT IN AN EFFECT. This was a
  // `useState` + `useEffect(..., [place.id])` pair, and an effect runs AFTER
  // the browser has already painted the frame it was meant to suppress. So
  // navigating from a place whose column is open (Home) to one whose column is
  // closed (any app) rendered once with `is-animating` still set and the width
  // going 240px -> 0, which starts the CSS transition; the effect then cleared
  // a flag the transition no longer needed. That is the slide-on-switch that
  // kept getting reported. Comparing against the path the toggle happened at
  // makes the answer available in the same render as the navigation, so there
  // is no frame in which the class is wrong.
  //
  // A toggle does not navigate, which is what makes the path a valid key for
  // it: `animAt === pathname` is true for exactly the renders between tapping
  // your own tile and going anywhere at all.
  const [motion, setMotion] = useState<Motion>(null);
  const settled = settleMotion(motion, pathname);
  if (settled !== motion) {
    // Adjusting state during render, the documented React pattern for "derive
    // from a change you just saw". React discards this render and re-runs
    // before touching the DOM, so the class is never applied to a navigation.
    setMotion(settled);
  }
  const animating = settled !== null;

  // Where you were, per place. Records the real path, so a record page below a
  // row is restored too, not just the row.
  useEffect(() => {
    setMemory((m) => rememberPath(m, place, pathname));
  }, [place, pathname]);

  // A DOCUMENT OWNS THE PHONE'S SCREEN while it is open (mockup spec section 6:
  // "the page toolbar collapses to 0"). The page declares it (takeover.tsx);
  // this only reads it, and ANDs it in rather than writing to `memory`, so
  // closing the document restores whatever the toolbar was before it opened.
  const documentOpen = useTakeover();

  const columnOpen = readColumnOpen(memory, place) && !fullScreen;
  const hasPageList = place.pages.length > 1;
  const key = tabsKey(place.id, page?.id ?? null);
  const tabsOpen = readTabsOpen(memory, key) && !fullScreen && hasPageList && !documentOpen;

  const selectPlace = useCallback(
    (next: ShellPlace) => {
      if (next.id === place.id && !fullScreen) {
        // Rule 2: the place you are already in. Toggle, and animate it.
        setMotion(grantMotion(pathname));
        setMemory((m) => toggleColumn(m, next));
        return;
      }
      // Rule 1: a different place never toggles the column, and hard cuts. The
      // navigation below is what clears the flag; nothing has to clear it here.
      navigate(openPath(memory, next));
    },
    [place.id, fullScreen, memory, navigate, pathname],
  );

  const selectPage = useCallback(
    (next: ShellPage) => {
      // A page row is a navigation, so it hard cuts like any other. The path
      // change clears the flag on its own.
      navigate(next.to);
    },
    [navigate],
  );

  const openColumn = useCallback(() => {
    setMotion(grantMotion(pathname));
    setMemory((m) => setColumnOpen(m, place, true));
  }, [place, pathname]);

  const onToggleTabs = useCallback(() => {
    setMemory((m) => toggleTabs(m, key));
  }, [key]);

  const hrefFor = useCallback((next: ShellPlace) => openPath(memory, next), [memory]);

  return {
    places,
    place,
    page,
    columnOpen,
    animating,
    fullScreen,
    hasPageList,
    tabsOpen,
    documentOpen,
    selectPlace,
    hrefFor,
    selectPage,
    openColumn,
    toggleTabs: onToggleTabs,
  };
}
