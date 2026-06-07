import { Link } from "react-router-dom";
import type { ReactNode } from "react";

// Generic breadcrumb trail primitive. Render a path of crumbs so any ancestor is
// a single click away. The last crumb (or any crumb without `to`) is the current
// page and is not a link. Each crumb may carry a leading icon.
//
// Usage:
//   <Breadcrumbs items={[
//     { label: "Yard Birds", to: "/apps/birds", icon: <Bird size={14} /> },
//     { label: "Locations", to: "/apps/birds/locations" },
//     { label: place.name },
//   ]} />
//
// Styling lives in styles.css under `.crumbs`.

export type Crumb = { label: string; to?: string; icon?: ReactNode };

export default function Breadcrumbs({ items, className }: { items: Crumb[]; className?: string }) {
  return (
    <nav className={`crumbs${className ? ` ${className}` : ""}`} aria-label="Breadcrumb">
      {items.map((c, i) => {
        const last = i === items.length - 1;
        const body = (
          <>{c.icon}{c.icon ? " " : ""}{c.label}</>
        );
        return (
          <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
            {last || !c.to
              ? <span className="cur" style={{ display: "inline-flex", alignItems: "center", gap: ".2rem" }}>{body}</span>
              : <Link to={c.to} style={{ display: "inline-flex", alignItems: "center", gap: ".2rem" }}>{body}</Link>}
            {!last && <span className="sep">›</span>}
          </span>
        );
      })}
    </nav>
  );
}
