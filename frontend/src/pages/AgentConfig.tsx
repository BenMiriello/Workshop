import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Trash2 } from "lucide-react";
import {
  ActionBar,
  Button,
  Checkbox,
  ConfirmDialog,
  Field,
  Input,
  PageHeader,
  Row,
  SectionLabel,
  Segmented,
  StateBlock,
  Tag,
  Textarea,
  type SegmentedOption,
} from "../ui";
import { useCrumbLabel } from "../components/appNav";

type MemoryBlock = { label: string; value: string };
type AgentMetadata = {
  tools?: string[];
  autonomy_mode?: string;
  cron_expression?: string;
  event_sources?: string[];
  persona_path?: string;
  effective_blocks?: string[];
  effective_namespaces?: string[];
};

type AgentDetail = {
  id: string;
  name: string;
  memory: { blocks: MemoryBlock[] };
  // Letta's GET returns the field as `metadata` (no trailing underscore) while
  // some older code paths used `metadata_`. Read either; prefer underscore form
  // if both are present.
  metadata_?: AgentMetadata;
  metadata?: AgentMetadata;
};

type AutonomyMode = "off" | "scheduled" | "event-driven" | "continuous";

const ALL_TOOLS = ["filesystem", "plaid", "google-docs", "playwright", "ollama", "web"];
const ALL_SOURCES = ["plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles", "custom"];
const AUTONOMY_OPTIONS: SegmentedOption<AutonomyMode>[] = [
  { value: "off", label: "Off" },
  { value: "scheduled", label: "Scheduled" },
  { value: "event-driven", label: "Event-driven" },
  { value: "continuous", label: "Continuous" },
];

export default function AgentConfig() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const isNew = agentId === "new";

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [name, setName] = useState("");
  useCrumbLabel(isNew ? "New agent" : name); // toolbar breadcrumb leaf
  const [persona, setPersona] = useState("");
  const [human, setHuman] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [autonomyMode, setAutonomyMode] = useState<AutonomyMode>("off");
  const [cronExpression, setCronExpression] = useState("");
  const [eventSources, setEventSources] = useState<string[]>([]);
  const [personaPath, setPersonaPath] = useState("");
  const [resolvedBlocks, setResolvedBlocks] = useState<string[]>([]);
  const [resolvedNamespaces, setResolvedNamespaces] = useState<string[]>([]);
  const [resolving, setResolving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (isNew) return;
    fetch(`/api/agents/${agentId}`)
      .then((r) => r.json())
      .then((a: AgentDetail) => {
        setAgent(a);
        setName(a.name);
        const blocks = a.memory?.blocks || [];
        setPersona(blocks.find((b) => b.label === "persona")?.value || "");
        setHuman(blocks.find((b) => b.label === "human")?.value || "");
        const md = a.metadata_ ?? a.metadata;
        setTools(md?.tools || []);
        setAutonomyMode((md?.autonomy_mode as AutonomyMode) || "off");
        setCronExpression(md?.cron_expression || "");
        setEventSources(md?.event_sources || []);
        const pp = md?.persona_path || "";
        setPersonaPath(pp);
        setResolvedBlocks(md?.effective_blocks || []);
        setResolvedNamespaces(md?.effective_namespaces || []);
      })
      .catch((e) => setErr(String(e)));
  }, [agentId, isNew]);

  function toggleTool(t: string) {
    setTools((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  function toggleSource(s: string) {
    setEventSources((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  }

  async function resolvePersonaPath(path: string) {
    if (!path.trim()) {
      setResolvedBlocks([]);
      setResolvedNamespaces([]);
      return;
    }
    setResolving(true);
    try {
      const r = await fetch(`/api/agents/memory/effective?persona_path=${encodeURIComponent(path)}`);
      if (r.ok) {
        const d = await r.json();
        setResolvedBlocks(d.blocks || []);
        setResolvedNamespaces(d.archival_namespaces || []);
      }
    } catch {
      /* ignore */
    } finally {
      setResolving(false);
    }
  }

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      if (isNew) {
        const r = await fetch("/api/agents/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            persona,
            human,
            tools,
            autonomy_mode: autonomyMode,
            cron_expression: autonomyMode === "scheduled" ? cronExpression : "",
            event_sources: autonomyMode === "event-driven" ? eventSources : [],
            persona_path: personaPath,
          }),
        });
        if (!r.ok) throw new Error(await r.text());
        navigate("/agents");
      } else {
        // Save memory blocks first. Collect errors but don't bail — metadata
        // (autonomy_mode etc.) must persist even if a memory PATCH fails so
        // the operator can still toggle autonomy on an agent whose blocks are
        // in an unexpected shape.
        const memErrs: string[] = [];
        const existingLabels = new Set((agent?.memory?.blocks || []).map((b) => b.label));
        for (const [label, value] of [
          ["persona", persona],
          ["human", human],
        ] as const) {
          if (!existingLabels.has(label)) continue; // skip blocks the agent doesn't have
          const r = await fetch(`/api/agents/${agentId}/memory/${label}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ value }),
          });
          if (!r.ok) memErrs.push(`${label}: ${await r.text()}`);
        }
        const r = await fetch(`/api/agents/${agentId}/metadata`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tools,
            autonomy_mode: autonomyMode,
            cron_expression: autonomyMode === "scheduled" ? cronExpression : "",
            event_sources: autonomyMode === "event-driven" ? eventSources : [],
            persona_path: personaPath,
          }),
        });
        if (!r.ok) throw new Error(await r.text());
        if (memErrs.length > 0) {
          setErr(`Saved metadata, but memory blocks failed: ${memErrs.join("; ")}`);
        } else {
          // Flash "saved" so the operator sees the action completed; navigate
          // after a beat so the confirmation is visible on mobile.
          setSavedFlash(true);
          setTimeout(() => navigate("/agents"), 700);
        }
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function destroy() {
    if (!agentId || isNew) return;
    const r = await fetch(`/api/agents/${agentId}`, { method: "DELETE" });
    if (r.ok) navigate("/agents");
    else {
      setErr(await r.text());
      setConfirmDelete(false);
    }
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <PageHeader title={isNew ? "New agent" : `Edit ${agent?.name || agentId}`} />
      {err && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <StateBlock variant="error" title="Save error" hint={err} />
        </div>
      )}

      <div className="ws-stack">
        <Field label="Name" htmlFor="ac-name">
          <Input id="ac-name" value={name} onChange={(e) => setName(e.target.value)} disabled={!isNew} />
        </Field>

        <Field label="Persona" htmlFor="ac-persona">
          <Textarea id="ac-persona" className="ws-mono" value={persona} onChange={(e) => setPersona(e.target.value)} rows={10} />
        </Field>

        <Field label="Human (operator context)" htmlFor="ac-human">
          <Textarea id="ac-human" className="ws-mono" value={human} onChange={(e) => setHuman(e.target.value)} rows={10} />
        </Field>

        <div>
          <SectionLabel>MCP / Integration grants</SectionLabel>
          <Row wrap>
            {ALL_TOOLS.map((t) => (
              <Checkbox key={t} checked={tools.includes(t)} onChange={() => toggleTool(t)} label={t} />
            ))}
          </Row>
        </div>

        <div>
          <SectionLabel>Autonomy mode</SectionLabel>
          {/* View-preference within the form — the actual write happens on Save. */}
          <Segmented options={AUTONOMY_OPTIONS} value={autonomyMode} onChange={setAutonomyMode} ariaLabel="Autonomy mode" />

          {autonomyMode === "scheduled" && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <Field label="Cron expression" htmlFor="ac-cron">
                <Input
                  id="ac-cron"
                  className="ws-mono"
                  value={cronExpression}
                  onChange={(e) => setCronExpression(e.target.value)}
                  placeholder="*/20 * * * *"
                />
              </Field>
            </div>
          )}

          {autonomyMode === "event-driven" && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <SectionLabel>Subscribe to event sources</SectionLabel>
              <Row wrap>
                {ALL_SOURCES.map((s) => (
                  <Checkbox key={s} checked={eventSources.includes(s)} onChange={() => toggleSource(s)} label={s} />
                ))}
              </Row>
            </div>
          )}

          {autonomyMode === "continuous" && (
            <p className="ws-muted" style={{ marginTop: "var(--space-2)", fontSize: "var(--fs-sm)" }}>
              Agent runs indefinitely under the supervisor. Supervisor enrolls it when{" "}
              <code>metadata.autonomy = "continuous"</code> is set.
            </p>
          )}
        </div>

        <div>
          <SectionLabel>Memory scope</SectionLabel>
          <Row style={{ alignItems: "flex-end" }}>
            <Field label="Persona path" htmlFor="ac-pp" className="ws-spacer">
              <Input
                id="ac-pp"
                className="ws-mono"
                value={personaPath}
                onChange={(e) => setPersonaPath(e.target.value)}
                placeholder="ops/personal-ai"
              />
            </Field>
            <Button type="button" onClick={() => resolvePersonaPath(personaPath)} loading={resolving}>
              Resolve
            </Button>
          </Row>
          {(resolvedBlocks.length > 0 || resolvedNamespaces.length > 0) && (
            <div className="ws-stack ws-stack--sm" style={{ marginTop: "var(--space-3)", fontSize: "var(--fs-sm)" }}>
              {resolvedBlocks.length > 0 && (
                <Row wrap>
                  <span className="ws-muted">Global blocks:</span>
                  {resolvedBlocks.map((b) => (
                    <Tag key={b} hue="blue">
                      {b}
                    </Tag>
                  ))}
                </Row>
              )}
              {resolvedNamespaces.length > 0 && (
                <Row wrap>
                  <span className="ws-muted">Archival namespaces:</span>
                  {resolvedNamespaces.map((n) => (
                    <Tag key={n} hue="turquoise">
                      {n}
                    </Tag>
                  ))}
                </Row>
              )}
            </div>
          )}
          {personaPath && resolvedBlocks.length === 0 && resolvedNamespaces.length === 0 && !resolving && (
            <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: "var(--space-2) 0 0" }}>
              No blocks or namespaces resolved. Hit Resolve to check, or the path may not exist in the tree.
            </p>
          )}
        </div>
      </div>

      <ActionBar flash={savedFlash ? "Saved ✓" : undefined}>
        <Button variant="primary" onClick={save} loading={saving} disabled={!name}>
          Save
        </Button>
        <Button variant="ghost" type="button" onClick={() => navigate("/agents")}>
          Cancel
        </Button>
        {!isNew && (
          <Button variant="danger-ghost" type="button" onClick={() => setConfirmDelete(true)}>
            <Trash2 aria-hidden /> Delete
          </Button>
        )}
      </ActionBar>

      <ConfirmDialog
        open={confirmDelete}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={destroy}
        title={`Delete agent ${agent?.name || ""}?`}
        body="This permanently removes the agent and its memory blocks from Letta. This can't be undone."
        confirmLabel="Delete agent"
      />
    </div>
  );
}
