// The avatar, as a menu: the settings pages, a divider, then the actions that
// belong to nowhere in particular (find in page, and each running app that
// opens over any page).
//
// Tapping the avatar while the menu is open goes to Settings, so the old
// one-tap route there survives as a second tap. It reads the trigger's own
// aria-expanded, which the kit Menu keeps current, so the kit needs no change.

import { useNavigate } from "react-router-dom";
import { AppWindow, Search } from "lucide-react";
import { Menu, MenuItem, MenuSeparator } from "@workshop/ui";
import { useAuth } from "../../auth/AuthProvider";
import { monogram } from "../../lib/appIcon";
import { overlayApps, useOverlayApps } from "../../lib/overlayApps/store";
import { useFind } from "./find/FindProvider";
import type { Shell } from "./useShell";

export function AccountMenu({ shell, variant }: { shell: Shell; variant: "rail" | "bar" }) {
  const { user } = useAuth();
  const nav = useNavigate();
  const { openFind } = useFind();
  const { apps: overlays } = useOverlayApps();
  const settings = shell.places.find((p) => p.kind === "settings");
  const inSettings = shell.place.kind === "settings";
  const label = monogram(user?.name || user?.email || "");

  return (
    <Menu
      label="Account and settings"
      side={variant === "rail" ? "top" : "bottom"}
      className={"ws-accountmenu ws-accountmenu--" + variant}
      trigger={
        <button
          type="button"
          className={
            (variant === "rail" ? "ws-rail__avatar" : "ws-bar1__avatar") +
            (variant === "rail" && inSettings ? " is-active" : "")
          }
          aria-label="Account and settings"
          title="Account and settings"
          onClick={(e) => {
            if (e.currentTarget.getAttribute("aria-expanded") === "true") nav("/settings");
          }}
        >
          {label}
        </button>
      }
    >
      {settings?.pages.map((p) => {
        const Icon = p.Icon;
        return (
          <MenuItem
            key={p.id}
            icon={Icon ? <Icon size={14} /> : undefined}
            active={shell.page?.id === p.id && inSettings}
            onSelect={() => nav(p.to)}
          >
            {p.label}
          </MenuItem>
        );
      })}
      <MenuSeparator />
      <MenuItem icon={<Search size={14} />} onSelect={openFind}>
        Find in page
      </MenuItem>
      {overlays.map((a) => (
        <MenuItem key={a.slug} icon={<AppWindow size={14} />} onSelect={() => overlayApps.open(a.slug)}>
          {a.title}
        </MenuItem>
      ))}
    </Menu>
  );
}
