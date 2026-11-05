// Local Expo module entry. The native side registers the view
// "WorkshopSelectionMenu": it holds the shell's WebView and adds one item,
// `title`, to the system menu for a text selection in it, keeping the system
// items; choosing it fires `onSelect`.
import { requireNativeViewManager } from "expo-modules-core";
import type { ComponentType } from "react";
import type { ViewProps } from "react-native";

export type SelectionMenuProps = ViewProps & {
  title: string;
  onSelect: () => void;
};

const SelectionMenu: ComponentType<SelectionMenuProps> =
  requireNativeViewManager<SelectionMenuProps>("WorkshopSelectionMenu");

export default SelectionMenu;
