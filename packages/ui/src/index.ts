// Workshop Design System, React primitives.
// Single import surface: `import { Button, Card, StatusPill, ... } from "@workshop/ui"`.
// Every primitive is a thin className wrapper over components.css: no inline
// styles, no CSS-in-JS. See frontend/CLAUDE.md for the rules that govern these.
//
// This is the CORE entry: its only runtime dependencies are react, react-dom
// and lucide-react. The two primitives that need heavy third-party libraries
// are deliberately NOT re-exported here, so importing a Button never pulls a
// map engine or a markdown parser into the bundle:
//   @workshop/ui/map        Map        (maplibre-gl, pmtiles, protomaps)
//   @workshop/ui/markdown   Markdown   (react-markdown, remark-gfm)
// Both are optional peer dependencies. See CHANGELOG 2.0.0.
export { cx } from "./cx";
export {
  useTheme, applyTheme, getStoredTheme,
  usePalette, applyPalette, getStoredPalette, useResolvedMode,
  type ThemeMode, type PaletteId,
} from "./theme";
export { PalettePicker } from "./PalettePicker";
export {
  PALETTE_META, DEFAULT_PALETTE, LEGACY_PALETTES, resolvePaletteId,
  type PaletteMeta, type PaletteGroup,
} from "./styles/palettes.gen";
export { statusClass, catClass, catFor, type StatusKind, type CatHue } from "./status";
export {
  AgencyChip, AgencyDot, agencyFor, agencyProps, AGENCY_LABEL, type AgencyTier,
} from "./agency";

export {
  Stack, Row, Spacer, SectionLabel, PageHeader, DetailLayout, Props, Prop, Clamp,
  Toolbar, Card, CardGrid, Stat, Divider, ProgressBar, StackedBar,
} from "./layout";
export {
  Button,
  Field,
  Input,
  Select,
  Textarea,
  ReadOnly,
  Toggle,
  Checkbox,
  Badge,
  StatusPill,
  StatusDot,
  Tag,
  Tabs,
  type TabItem,
} from "./controls";
export { FilterBar, FilterChip } from "./filters";
export { Board, BoardColumn, BoardCard } from "./Board";
export { Segmented, type SegmentedOption } from "./Segmented";
export { Stepper } from "./Stepper";
export { Modal, ConfirmDialog } from "./Modal";
export { OverlayHost, useOverlayHost } from "./OverlayHost";
export { PageActionBar } from "./PageActionBar";
export { Menu, MenuItem, MenuCheckItem, MenuSeparator, MenuLabel } from "./Menu";
export { Popover } from "./Popover";
export { StateBlock, Notice, Skeleton, ActionBar, CopyButton, RevealField } from "./feedback";
export { TableScroll, Table, NumCell, TimeCell } from "./Table";
export { FileTree, type FileTreeProps } from "./FileTree";
// The same tree on a phone: one level at a time, Up walking back out. Same
// adapter as `FileTree`, so a surface branches on width and says nothing twice.
export { FileDrill, type FileDrillProps } from "./FileDrill";
// The tree's data seam and its pure row model. A consumer supplies an adapter
// (a root plus a function returning one node's children); `flattenTree` and
// `treeKeyIntent` are exported because a caller that draws its own rows still
// wants this tree's row model and keymap rather than a second one.
export {
  flattenTree, treeKeyIntent, toggleBranch, nodesById, FILE_TREE_MAX_ROWS_PER_LEVEL,
  drillTrail, drillCursor, drillParent, drillInto, drillUp, drillUpIntent, drillTrailText,
  type FileDrillTrail, type FileDrillUpIntent,
  type FileTreeAdapter, type FileTreeNode, type FileTreeNodeKind, type FileTreeIconHint,
  type FileTreeLevel, type FileTreeLevels, type FileTreeLevelState, type FileTreeRow,
  type FileTreeIntent,
} from "./fileTree";
// The chrome around an open file: the header the Files tab and Memory both
// draw, plus the pure rule for what it shows. The BODY is @workshop/ui/editor,
// which is a subpath because it carries CodeMirror; this carries nothing, so it
// lives here and a surface that renders markdown rather than an editor can
// still use it.
export { DocumentBar, type DocumentBarProps } from "./DocumentBar";
export {
  documentBar, type DocumentBarInput, type DocumentBarModel, type DocumentForm,
} from "./documentBar";
export { ChartFrame, ChartTooltip, CHART_SERIES, CHART_TARGET } from "./chart";
export { useEditMode, type EditMode } from "./useEditMode";

// App-facing capabilities, promoted from the frontend's platform-internal
// modules 2026-08-20 (Stage 4b), so a page under `frontend/src/pages/apps/**`
// can import everything it legitimately needs from this one package. See
// docs/platform-redesign/HANDOFF-app-platform-and-github-2026-08-20.md.
export { usePoll, pollShouldRun, type WhenHidden } from "./usePoll";
export { timeAgo, timeAbsolute, timeAbsoluteShort, formatDuration, timeAgoSeconds } from "./time";
export { formatBytes, formatVramBytes, formatMiB, VRAM_UNKNOWN } from "./bytes";
export {
  HttpError, ERROR_BODY_CAP, isTransientNetworkError, withTransientRetry, shortHttpError,
  TRANSIENT_BACKOFF_MS,
} from "./httpError";
export {
  userStorage, setCurrentUser, registerDeviceScoped, isDeviceScoped, namespacedKey,
  isNamespacedKey, planMigration, _resetCurrentUserForTest, _resetDeviceScopedForTest,
  type UserId, type StorageExemption,
} from "./storage";
export {
  BreadcrumbProvider, useCrumbLabel, useCrumbTrail, useBreadcrumbOverride, type Crumb,
} from "./breadcrumbs";
export { AppSettings, SettingsSection, SettingsRow } from "./AppSettings";
export {
  native, isNativeShell, hasCapability, isPaidTier, onNativeEvent,
  type Capability, type WorkshopNative, type BridgeEventTopic,
} from "./native/bridge";
export {
  getCurrentFix, startDeviceTracking, stopDeviceTracking, backgroundTrackingSupported,
} from "./native/location";
export { useNative, useNativeEvent, useTrackingStatus, type NativeInfo } from "./native/useNative";
export { type AppChatDockRenderer, type AppChatDockOptions } from "./appChatDock";
