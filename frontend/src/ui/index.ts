// Workshop Design System — React primitives.
// Single import surface: `import { Button, Card, StatusPill, ... } from "../ui"`.
// Every primitive is a thin className wrapper over components.css — no inline
// styles, no CSS-in-JS. See frontend/CLAUDE.md for the rules that govern these.
export { cx } from "./cx";
export { useTheme, applyTheme, getStoredTheme, type ThemeMode } from "./theme";
export { statusClass, catClass, catFor, type StatusKind, type CatHue } from "./status";

export { Stack, Row, Spacer, SectionLabel, PageHeader, Toolbar, Card, CardGrid, Stat, Divider } from "./layout";
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
export { Segmented, type SegmentedOption } from "./Segmented";
export { Stepper } from "./Stepper";
export { Modal, ConfirmDialog } from "./Modal";
export { StateBlock, Skeleton, ActionBar, CopyButton, RevealField } from "./feedback";
export { TableScroll, Table, NumCell, TimeCell } from "./Table";
export { ChartFrame, ChartTooltip, CHART_SERIES, CHART_TARGET } from "./chart";
export { useEditMode, type EditMode } from "./useEditMode";
export { Markdown } from "./Markdown";
