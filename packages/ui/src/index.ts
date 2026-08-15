// Workshop Design System — React primitives.
// Single import surface: `import { Button, Card, StatusPill, ... } from "@workshop/ui"`.
// Every primitive is a thin className wrapper over components.css — no inline
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
export { StateBlock, Notice, Skeleton, ActionBar, CopyButton, RevealField } from "./feedback";
export { TableScroll, Table, NumCell, TimeCell } from "./Table";
export { ChartFrame, ChartTooltip, CHART_SERIES, CHART_TARGET } from "./chart";
export { useEditMode, type EditMode } from "./useEditMode";
