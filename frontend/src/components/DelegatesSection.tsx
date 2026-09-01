import { useEffect, useState } from "react";
import { Workflow } from "lucide-react";
import { Badge, Card, CardGrid, Row, SectionLabel, Skeleton, Stack, StateBlock, Tag, catFor, shortHttpError } from "@workshop/ui";
import type { components } from "../api/schema.gen";

// Who can be CALLED, as opposed to who runs.
//
// The roster above answers "which agents does this workshop dispatch". It
// cannot answer "who can call whom", because the agents that only ever run
// because something else invoked them are not in it — Claude Code's native
// subagents, which live in ~/.claude/agents and are reached through delegation.
//
// They are not a second format: same markdown, same YAML frontmatter, same
// parser as agents/<slug>.md. The difference is LIFECYCLE. A roster agent is
// dispatched — it has triggers, it can run on a schedule, it is a chat scope. A
// delegate has no trigger to enable and no autonomy of its own to grant, so
// merging the two lists would put rows in the roster with every control
// permanently inert. Hence its own section, stating what it is.

// The delegate row, named from the generated client (routes/agents.NativeSubagent).
//
// The hand-written copy declared `description`, `purpose`, `scope`, `tools`,
// `unrestricted_tools` and `definition_path` as REQUIRED. The route serves this
// with `response_model_exclude_unset=True`, so a field the service never set is
// dropped from the wire entirely -- only `slug`, `name`, `dispatch` and `source`
// are promised. Everything else is optional here now because it is optional
// there, and `definition_path` can also be null.
export type Delegate = components["schemas"]["NativeSubagent"];

export default function DelegatesSection() {
  const [items, setItems] = useState<Delegate[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch("/api/agents/delegates")
      .then(async (r) => {
        if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
        return r.json();
      })
      .then((d: components["schemas"]["DelegatesOut"]) => { if (live) setItems(d.delegates ?? []); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, []);

  return (
    <section style={{ marginTop: "var(--space-6)" }}>
      <SectionLabel>
        <Workflow size={14} strokeWidth={1.8} aria-hidden
                  style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
        {/* Counted here, because the page header counts only what it
            dispatches and these are loaded separately. Between the two every
            card on the page is accounted for. */}
        Delegates{items ? ` · ${items.length}` : ""}
      </SectionLabel>
      <p className="ws-muted" style={{ marginTop: "var(--space-1)", marginBottom: "var(--space-3)" }}>
        Invoked by another agent, never on a schedule, so they have no triggers
        of their own.
      </p>

      {err ? (
        <StateBlock variant="error" title="Couldn't load delegates"
                    hint={<code>/api/agents/delegates</code>} />
      ) : !items ? (
        <Skeleton />
      ) : items.length === 0 ? (
        <StateBlock
          title="No delegates"
          hint="Nothing here yet. A delegate becomes callable once one is defined."
        />
      ) : (
        <CardGrid>
          {items.map((d) => (
            <li key={d.slug}>
              <Card>
                <Row between>
                  <h2 className="ws-card__title">{d.name}</h2>
                  {/* An absence renders as nothing. A "no role" label is a
                      mark for a thing that is not there, and beside real role
                      tags it reads as a role called "no role". */}
                  {d.role && <Tag hue={catFor(`role:${d.role}`)}>{d.role}</Tag>}
                </Row>
                <p className="ws-card__meta">{d.purpose || d.description}</p>
                <Stack gap="sm" style={{ marginTop: "var(--space-2)" }}>
                  <Row wrap style={{ gap: "var(--space-1)" }}>
                    {/* What it may reach for. `['*']` means every tool, which
                        is worth saying in words rather than rendering as a
                        one-item list containing an asterisk. */}
                    {d.unrestricted_tools
                      ? <Badge>every tool</Badge>
                      : d.tools.length === 0
                        ? <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>no tools declared</span>
                        : d.tools.map((t) => <Badge key={t}>{t}</Badge>)}
                  </Row>
                  {/* An agent that exists and is not ours is worth seeing
                      exactly once, named. Filtering it silently would make it
                      invisible; this says whose it is. */}
                  {d.foreign && (
                    <p className="ws-card__meta ws-muted">belongs to the {d.foreign}</p>
                  )}
                </Stack>
              </Card>
            </li>
          ))}
        </CardGrid>
      )}
    </section>
  );
}
