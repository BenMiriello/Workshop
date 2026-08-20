// An app's icon is DATA the app declares, never an import the platform makes.
//
// Operator, 2026-08-20: "The icon for an app is not always going to be a Lucide
// SVG. It can be the first letter or first two letters of an app's name as a
// fallback. Or it can be something else. It can be a custom icon. All of these
// things should be options. But in no way is the specific app icon choice
// something that should be built in."
//
// It was built in: `frontend/src/apps/registry.tsx` did
// `import { Bird } from "lucide-react"` and hung the component off the app's
// entry, so the platform bundle knew what a bird looks like and knew that one
// particular app wanted one. The backend was already doing this correctly -- an
// AppSpec card declares `"icon": "Bird"`, a string -- and the frontend override
// took precedence over it.
//
// THE TOKEN TABLE BELOW IS NOT AN APP LIST. It is a design vocabulary, the same
// kind of closed set as the five status kinds: names an app may ask for, none
// of which mentions an app. Adding an app never edits it; asking for a token it
// does not have degrades to the monogram, which is a real answer rather than a
// broken image.
//
// See docs/platform-redesign/app-separation-and-github-2026-08-20.md stage 2.

import {
  Banknote,
  Bird,
  Boxes,
  Calendar,
  Camera,
  ChartLine,
  Cloud,
  Compass,
  Cpu,
  Database,
  Dumbbell,
  Film,
  Gamepad2,
  Globe,
  Heart,
  Home,
  Image,
  LayoutGrid,
  Leaf,
  Mail,
  Map,
  MapPin,
  Music,
  NotebookPen,
  PiggyBank,
  Plane,
  Radio,
  ShoppingCart,
  Sparkles,
  Terminal,
  TrainFront,
  Utensils,
  Watch,
  Wrench,
} from "lucide-react";
import type { ComponentType } from "react";
import type { LucideProps } from "lucide-react";

/** How an app's icon is declared. Absent is legal and means "use my name". */
export type AppIcon =
  | { kind: "token"; name: string }
  | { kind: "image"; src: string }
  | { kind: "monogram"; text: string };

/** The icon names an app may ask for.
 *
 *  ONE RULE: a token is the kebab-case of its lucide export name, so
 *  `TrainFront` -> `train-front` and an app may spell it either way. Inventing
 *  friendlier short names (`train`) instead was the first version of this and
 *  it broke immediately: the backend cards already say `TrainFront`, so a
 *  hand-picked alias table means every new icon is a chance to guess wrong. */
const TOKENS: Record<string, ComponentType<LucideProps>> = {
  banknote: Banknote,
  bird: Bird,
  boxes: Boxes,
  calendar: Calendar,
  camera: Camera,
  "chart-line": ChartLine,
  cloud: Cloud,
  compass: Compass,
  cpu: Cpu,
  database: Database,
  dumbbell: Dumbbell,
  film: Film,
  gamepad2: Gamepad2,
  globe: Globe,
  heart: Heart,
  home: Home,
  image: Image,
  "layout-grid": LayoutGrid,
  leaf: Leaf,
  mail: Mail,
  map: Map,
  "map-pin": MapPin,
  music: Music,
  "notebook-pen": NotebookPen,
  "piggy-bank": PiggyBank,
  plane: Plane,
  radio: Radio,
  "shopping-cart": ShoppingCart,
  sparkles: Sparkles,
  terminal: Terminal,
  "train-front": TrainFront,
  utensils: Utensils,
  watch: Watch,
  wrench: Wrench,
};

/** Every token name, for a picker. Sorted so the UI order is not insertion order. */
export const ICON_TOKENS: string[] = Object.keys(TOKENS).sort();

export function iconComponent(name: string): ComponentType<LucideProps> | null {
  return TOKENS[name.trim().toLowerCase()] ?? null;
}

/**
 * One or two letters standing in for an app with no icon.
 *
 * TWO letters by default, not one. The operator asked for "the first letter or
 * first two letters", and one letter is genuinely ambiguous on this very
 * instance: Budget and Birds both render "B". Multi-word names take initials
 * ("Yard Birds" -> "YB") because that is what a person would write.
 */
export function monogram(name: string): string {
  const clean = (name ?? "").trim();
  if (!clean) return "?";
  const words = clean.split(/[\s._/-]+/).filter(Boolean);
  if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase();
  // Array.from, not slice: an emoji or any astral-plane first character is one
  // grapheme and two UTF-16 code units, and slice(0, 2) would cut it in half.
  return Array.from(clean).slice(0, 2).join("").toUpperCase();
}

/**
 * Normalise whatever an app declared into something renderable.
 *
 * Accepts the loose shapes that already exist in the tree: a bare string is a
 * token name (`"Bird"` from a backend card, case-insensitively), and a string
 * that looks like a path or URL is an image. Anything unrecognised falls
 * through to the monogram rather than to a broken glyph.
 */
export function resolveAppIcon(declared: AppIcon | string | null | undefined, name: string): AppIcon {
  if (declared && typeof declared === "object") {
    if (declared.kind === "token" && iconComponent(declared.name)) return declared;
    if (declared.kind === "image" && declared.src) return declared;
    if (declared.kind === "monogram" && declared.text.trim()) return declared;
    return { kind: "monogram", text: monogram(name) };
  }
  const raw = (declared ?? "").trim();
  if (!raw) return { kind: "monogram", text: monogram(name) };
  if (/^(https?:|data:|\/)/.test(raw) || /\.(png|jpe?g|svg|webp|gif)$/i.test(raw)) {
    return { kind: "image", src: raw };
  }
  // "PiggyBank" and "piggy-bank" are the same request. The backend cards spell
  // it the first way because they were written against the lucide export name.
  const token = raw.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
  if (iconComponent(token)) return { kind: "token", name: token };
  return { kind: "monogram", text: monogram(name) };
}

/** Render an app's icon at a given size. Never returns null: an app always has a glyph. */
export function AppGlyph({
  icon,
  name,
  size = 18,
  className,
}: {
  icon: AppIcon | string | null | undefined;
  name: string;
  size?: number;
  className?: string;
}) {
  const resolved = resolveAppIcon(icon, name);
  if (resolved.kind === "token") {
    const Icon = iconComponent(resolved.name)!;
    return <Icon size={size} aria-hidden className={className} />;
  }
  if (resolved.kind === "image") {
    return (
      <img
        src={resolved.src}
        alt=""
        aria-hidden
        width={size}
        height={size}
        className={className}
        style={{ borderRadius: "var(--radius-sm)", objectFit: "cover" }}
      />
    );
  }
  return (
    <span aria-hidden className={className} style={{ fontSize: Math.round(size * 0.62) }}>
      {resolved.text}
    </span>
  );
}
