import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Package, Boxes, Download, RefreshCw } from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  PageHeader,
  Row,
  Stack,
  Badge,
  Tag,
  Skeleton,
  StateBlock,
} from "../ui";
import { marketplaceApi, type RegistryItem } from "../lib/marketplace";

// Marketplace: the local app + kit registry (docs/workshop-v1/03 §4). A uniform
// card grid mirroring the Apps launcher. Each card shows the item's identity
// (title, kind, version, sensitivity) and installs (forks) it into the
// workspace, landing on the new project. Publishing happens from a project's
// Deployments card (the Public row), so the empty state points there.

// More than this many items and the grid gets long; not a hard cap, just a note
// (the registry is single-operator local, so this is generous headroom).
const OVERFLOW_NOTE_AT = 60;

type ListState =
  | { kind: "loading" }
  | { kind: "loaded"; items: RegistryItem[] }
  | { kind: "error"; message: string };

// A kit is a package (no runnable surface); an app is runnable. The icon names
// the kind beside its Badge.
function kindIcon(kind: RegistryItem["kind"]) {
  return kind === "kit" ? <Boxes size={14} strokeWidth={1.8} aria-hidden /> : <Package size={14} strokeWidth={1.8} aria-hidden />;
}

function MarketplaceCard({
  item,
  busy,
  error,
  onInstall,
}: {
  item: RegistryItem;
  busy: boolean;
  error: string | null;
  onInstall: (item: RegistryItem) => void;
}) {
  return (
    <Card pad0 style={{ overflow: "hidden", height: "100%" }}>
      <div style={{ padding: "var(--space-5)", display: "flex", flexDirection: "column", gap: "var(--space-3)", height: "100%" }}>
        <Row style={{ justifyContent: "space-between", alignItems: "flex-start", gap: "var(--space-2)" }}>
          <h3 className="ws-card__title">{item.title}</h3>
          <Badge>
            <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}>
              {kindIcon(item.kind)}
              {item.kind}
            </span>
          </Badge>
        </Row>

        <Row style={{ gap: "var(--space-2)", flexWrap: "wrap" }}>
          <span className="ws-card__meta ws-mono">v{item.version}</span>
          <Tag hue="slate">{item.sensitivity}</Tag>
        </Row>

        {item.description && (
          <p className="ws-card__meta" style={{ margin: 0 }}>
            {item.description}
          </p>
        )}

        {/* Push the action to the bottom so cards align in the grid. */}
        <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          <Row style={{ justifyContent: "flex-end" }}>
            <Button variant="primary" size="sm" loading={busy} disabled={busy} onClick={() => onInstall(item)}>
              <Download aria-hidden /> Install
            </Button>
          </Row>
          {error && (
            <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0, textAlign: "right" }}>
              {error}
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}

export default function Marketplace() {
  const nav = useNavigate();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [installing, setInstalling] = useState<string | null>(null); // item id mid-install
  const [installErr, setInstallErr] = useState<Record<string, string>>({}); // per-item error

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const items = await marketplaceApi.listItems();
      setState({ kind: "loaded", items });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const install = useCallback(
    async (item: RegistryItem) => {
      setInstalling(item.id);
      setInstallErr((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      try {
        const project = await marketplaceApi.install(item.id, {});
        nav(`/projects/${project.slug}`);
      } catch (e) {
        setInstallErr((prev) => ({ ...prev, [item.id]: e instanceof Error ? e.message : String(e) }));
      } finally {
        setInstalling(null);
      }
    },
    [nav],
  );

  const count = state.kind === "loaded" ? state.items.length : null;

  return (
    <div>
      <PageHeader
        title="Marketplace"
        sub={count != null ? `${count} published item${count === 1 ? "" : "s"}` : "Local app + kit registry"}
        actions={
          <Button size="sm" variant="ghost" onClick={load} disabled={state.kind === "loading"}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        }
      />

      {state.kind === "loading" ? (
        <CardGrid>
          {[0, 1, 2].map((i) => (
            <li key={i}>
              <Card>
                <Stack gap="sm">
                  <Skeleton width="55%" />
                  <Skeleton width="30%" />
                  <Skeleton width="80%" />
                </Stack>
              </Card>
            </li>
          ))}
        </CardGrid>
      ) : state.kind === "error" ? (
        <StateBlock
          variant="error"
          title="Couldn't load the marketplace"
          hint={
            <>
              <code>GET /api/base-apps/registry</code>: {state.message}
            </>
          }
          action={
            <Button size="sm" onClick={load}>
              Retry
            </Button>
          }
        />
      ) : state.items.length === 0 ? (
        <StateBlock
          title="No published apps yet"
          hint="Publish one from a project (Deployments, Public)."
        />
      ) : (
        <>
          {state.items.length > OVERFLOW_NOTE_AT && (
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "0 0 var(--space-3)" }}>
              Showing all {state.items.length} items. The list is long; use the kind and version on each card to find what you want.
            </p>
          )}
          <CardGrid>
            {state.items.map((item) => (
              <li key={item.id} style={{ display: "flex" }}>
                <MarketplaceCard
                  item={item}
                  busy={installing === item.id}
                  error={installErr[item.id] ?? null}
                  onInstall={install}
                />
              </li>
            ))}
          </CardGrid>
        </>
      )}
    </div>
  );
}
