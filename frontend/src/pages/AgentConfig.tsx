import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";

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
const AUTONOMY_MODES: AutonomyMode[] = ["off", "scheduled", "event-driven", "continuous"];

export default function AgentConfig() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const isNew = agentId === "new";

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [name, setName] = useState("");
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
    setTools((prev) => prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]);
  }

  function toggleSource(s: string) {
    setEventSources((prev) => prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]);
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
    } catch { /* ignore */ } finally {
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
            name, persona, human, tools,
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
        for (const [label, value] of [["persona", persona], ["human", human]] as const) {
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
          // after a beat so the confirmation is visible on mobile (where the
          // viewport doesn't otherwise change to acknowledge the save).
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
    if (!confirm(`Delete agent ${agent?.name}?`)) return;
    const r = await fetch(`/api/agents/${agentId}`, { method: "DELETE" });
    if (r.ok) navigate("/agents");
    else setErr(await r.text());
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <h2>{isNew ? "New agent" : `Edit ${agent?.name || agentId}`}</h2>
      {err && <p className="err">{err}</p>}

      <label>Name
        <input value={name} onChange={(e) => setName(e.target.value)} disabled={!isNew} style={inputStyle} />
      </label>

      <label>Persona
        <textarea value={persona} onChange={(e) => setPersona(e.target.value)} rows={10} style={textareaStyle} />
      </label>

      <label>Human (operator context)
        <textarea value={human} onChange={(e) => setHuman(e.target.value)} rows={10} style={textareaStyle} />
      </label>

      <div style={{ marginBottom: "1rem" }}>
        <div style={{ marginBottom: "0.4rem", fontWeight: 500 }}>MCP / Integration grants</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {ALL_TOOLS.map((t) => (
            <label key={t} style={chipStyle(tools.includes(t))}>
              <input
                type="checkbox"
                checked={tools.includes(t)}
                onChange={() => toggleTool(t)}
                style={{ marginRight: "0.35rem" }}
              />
              {t}
            </label>
          ))}
        </div>
      </div>

      <div style={{ marginBottom: "1rem" }}>
        <div style={{ marginBottom: "0.5rem", fontWeight: 500 }}>Autonomy mode</div>
        <div style={{ display: "flex", gap: "1.25rem", flexWrap: "wrap" }}>
          {AUTONOMY_MODES.map((m) => (
            <label key={m} style={{ display: "flex", alignItems: "center", gap: "0.35rem", cursor: "pointer" }}>
              <input
                type="radio"
                name="autonomy_mode"
                value={m}
                checked={autonomyMode === m}
                onChange={() => setAutonomyMode(m)}
              />
              {m}
            </label>
          ))}
        </div>

        {autonomyMode === "scheduled" && (
          <div style={{ marginTop: "0.75rem" }}>
            <label>Cron expression
              <input
                value={cronExpression}
                onChange={(e) => setCronExpression(e.target.value)}
                placeholder="*/20 * * * *"
                style={{ ...inputStyle, fontFamily: "ui-monospace, monospace" }}
              />
            </label>
          </div>
        )}

        {autonomyMode === "event-driven" && (
          <div style={{ marginTop: "0.75rem" }}>
            <div style={{ marginBottom: "0.4rem", fontSize: "0.9rem", color: "var(--fg-dim, #888)" }}>
              Subscribe to event sources
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
              {ALL_SOURCES.map((s) => (
                <label key={s} style={chipStyle(eventSources.includes(s))}>
                  <input
                    type="checkbox"
                    checked={eventSources.includes(s)}
                    onChange={() => toggleSource(s)}
                    style={{ marginRight: "0.35rem" }}
                  />
                  {s}
                </label>
              ))}
            </div>
          </div>
        )}

        {autonomyMode === "continuous" && (
          <p style={{ marginTop: "0.5rem", fontSize: "0.875rem", color: "var(--fg-dim, #888)" }}>
            Agent runs indefinitely under the supervisor. Supervisor enrolls it when{" "}
            <code>metadata.autonomy = "continuous"</code> is set.
          </p>
        )}
      </div>

      <div style={{ marginBottom: "1rem" }}>
        <div style={{ marginBottom: "0.4rem", fontWeight: 500 }}>Memory scope</div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end", marginBottom: "0.5rem" }}>
          <label style={{ flex: 1 }}>
            Persona path
            <input
              value={personaPath}
              onChange={(e) => setPersonaPath(e.target.value)}
              placeholder="ops/personal-ai"
              style={{ ...inputStyle, fontFamily: "ui-monospace, monospace", marginBottom: 0 }}
            />
          </label>
          <button
            type="button"
            onClick={() => resolvePersonaPath(personaPath)}
            disabled={resolving}
            style={{ height: 38, whiteSpace: "nowrap" }}
          >
            {resolving ? "…" : "Resolve"}
          </button>
        </div>
        {(resolvedBlocks.length > 0 || resolvedNamespaces.length > 0) && (
          <div style={{ fontSize: "0.875rem" }}>
            {resolvedBlocks.length > 0 && (
              <div style={{ marginBottom: "0.4rem" }}>
                <span style={{ color: "var(--fg-dim, #888)", marginRight: "0.5rem" }}>Global blocks:</span>
                {resolvedBlocks.map((b) => (
                  <span key={b} style={badgeStyle("blue")}>{b}</span>
                ))}
              </div>
            )}
            {resolvedNamespaces.length > 0 && (
              <div>
                <span style={{ color: "var(--fg-dim, #888)", marginRight: "0.5rem" }}>Archival namespaces:</span>
                {resolvedNamespaces.map((n) => (
                  <span key={n} style={badgeStyle("green")}>{n}</span>
                ))}
              </div>
            )}
          </div>
        )}
        {personaPath && resolvedBlocks.length === 0 && resolvedNamespaces.length === 0 && !resolving && (
          <p style={{ fontSize: "0.875rem", color: "var(--fg-dim, #888)", margin: "0.25rem 0 0" }}>
            No blocks or namespaces resolved. Hit Resolve to check, or the path may not exist in the tree.
          </p>
        )}
      </div>

      <div className="form-actions-sticky">
        <button onClick={save} disabled={saving || !name} className="primary">
          {saving ? "Saving…" : savedFlash ? "Saved ✓" : "Save"}
        </button>
        <button onClick={() => navigate("/agents")} type="button">Cancel</button>
        {savedFlash && <span className="saved-flash">saved</span>}
        {!isNew && (
          <button onClick={destroy} type="button" className="destructive">
            Delete
          </button>
        )}
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "0.5rem",
  margin: "0.25rem 0 1rem",
  background: "var(--card)",
  color: "var(--fg)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  fontFamily: "inherit",
  fontSize: "1rem",
};

const textareaStyle: React.CSSProperties = { ...inputStyle, fontFamily: "ui-monospace, monospace", fontSize: "0.875rem" };

function badgeStyle(color: "blue" | "green"): React.CSSProperties {
  const colors = {
    blue: { bg: "rgba(108,142,191,0.2)", border: "rgba(108,142,191,0.5)" },
    green: { bg: "rgba(80,160,110,0.2)", border: "rgba(80,160,110,0.5)" },
  };
  return {
    display: "inline-block",
    marginRight: "0.35rem",
    marginBottom: "0.2rem",
    padding: "0.15rem 0.55rem",
    borderRadius: 12,
    border: `1px solid ${colors[color].border}`,
    background: colors[color].bg,
    fontFamily: "ui-monospace, monospace",
    fontSize: "0.8rem",
  };
}

function chipStyle(active: boolean): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    padding: "0.3rem 0.7rem",
    borderRadius: 16,
    border: `1px solid ${active ? "var(--accent, #6c8ebf)" : "var(--border)"}`,
    background: active ? "var(--accent-bg, rgba(108,142,191,0.15))" : "var(--card)",
    color: "var(--fg)",
    cursor: "pointer",
    fontSize: "0.875rem",
    userSelect: "none",
  };
}
