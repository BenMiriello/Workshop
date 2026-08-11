import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Plug, RefreshCw } from "lucide-react";
import {
  Button, Card, PageHeader, Row, SectionLabel, Skeleton, Stack, StateBlock,
  StatusDot, StatusPill, Table, TableScroll, Tag, catFor,
} from "@workshop/ui";
import { formatTokens, refreshedLabel } from "../../components/chat/contextBreakdown";
import type { StatusKind } from "@workshop/ui";

// Settings -> MCP. Which servers this machine has, whether they work, what they
// expose, and what they cost every turn.
//
// WHY IT IS A SCREEN AND NOT A LINE IN THE CONTEXT MODAL. On 2026-08-10 three
// servers on this host were failing to connect and nothing in the app said so.
// A server that fails to connect costs a startup attempt on every turn and
// returns nothing, silently; the context modal shows this per chat, but "is
// google-docs broken" is a fact about the MACHINE and belongs somewhere a chat
// does not have to be open to ask it.
//
// THE COST COLUMN CAN BE ABSENT, AND THAT IS NOT ZERO. Cost comes from a second
// probe (`/context`); a server the probe never saw prints an em-dash, a server
// it saw contributing nothing prints 0. Those are different claims and the
// screen must not merge them — 0 for a connected server means "its tools are
// deferred and cost nothing yet", which is real information.
//
// Per-chat on/off lives in the chat's own context modal, not here: it is a
// property of a conversation, not of the machine. This page links there by
// saying so, and does not grow a second, disagreeing switch.

type Tool = { name: string; tokens: number };

type Server = {
  name: string;
  target: string;
  origin: string;
  transport: string;
  kind: StatusKind;
  state: string;
  detail?: string;
  /** Whether a launch spec was found in the real config. A server without one
   *  cannot be re-declared, so it cannot be part of a per-chat subset. */
  spec_available: boolean;
  /** user | local | project | plugin. Empty when no spec was found. */
  scope: string;
  source: string;
  shadowed: boolean;
  /** null = the cost probe never saw this server. 0 = it saw it cost nothing. */
  tokens: number | null;
  tools: Tool[];
};

type Payload = {
  available: boolean;
  reason?: string;
  servers: Server[];
  counts?: Record<string, number>;
  checked_at?: number | null;
  cost_available?: boolean;
  cost_checked_at?: number | null;
  cwd?: string;
};

const ENDPOINT = "/api/mcp/servers";

// Where a server comes from, in one word the operator can act on. The CLI
// prints no scope column, so `origin` is derived from the line and `scope` from
// which config file actually holds the spec — two different questions, and the
// second is the one that says where to go and edit it.
const SCOPE_HELP: Record<string, string> = {
  user: "~/.claude.json — available in every directory",
  local: "~/.claude.json — this directory only",
  project: ".mcp.json in the project",
  plugin: "declared by an installed plugin",
};

// Eight rows is where the list stops being a glance and starts being a scroll;
// this host already has four and the marketplace makes more one command away.
const OVERFLOW_AT = 8;

function ToolList({ tools }: { tools: Tool[] }) {
  if (!tools.length) return null;
  return (
    <div className="mcp-tools">
      {tools.map((t) => (
        <div className="mcp-tools__row" key={t.name}>
          <span className="ws-mono">{t.name}</span>
          <span className="ws-muted">{formatTokens(t.tokens)}</span>
        </div>
      ))}
    </div>
  );
}

export default function McpServers() {
  const [data, setData] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async (force = false) => {
    setLoading(true);
    setFailed(null);
    try {
      const r = await fetch(`${ENDPOINT}${force ? "?refresh=1" : ""}`);
      if (!r.ok) throw new Error(`${r.status}`);
      setData(await r.json());
      setNow(Date.now());
    } catch (e) {
      // A failed read is not an empty machine. Say which it is.
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const servers = data?.servers ?? [];
  const broken = servers.filter((s) => s.kind === "danger" || s.kind === "warn").length;
  const age = refreshedLabel(data?.checked_at ?? null, now);
  const priced = data?.cost_available !== false;

  return (
    <div>
      <PageHeader
        title="MCP servers"
        actions={
          <Button variant="ghost" size="sm" onClick={() => void load(true)} disabled={loading}>
            <RefreshCw aria-hidden /> {loading ? "Checking" : "Refresh"}
          </Button>
        }
      />

      <Stack>
        <section>
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <SectionLabel icon={<Plug aria-hidden />}>Installed</SectionLabel>
            {loading && <StatusPill status="running">Checking</StatusPill>}
            {!loading && servers.length > 0 && (
              <StatusPill status={broken ? "danger" : "ok"}>
                {broken
                  ? `${broken} of ${servers.length} not working`
                  : `${servers.length} server${servers.length === 1 ? "" : "s"}, all connected`}
              </StatusPill>
            )}
            {age && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{age}</span>}
          </Row>

          <Card>
            {/* loading */}
            {loading && !data && (
              <Stack gap="sm">
                <Skeleton width="60%" /><Skeleton width="45%" /><Skeleton width="52%" />
              </Stack>
            )}

            {/* error */}
            {failed && (
              <StateBlock
                variant="error"
                title="Couldn't list MCP servers"
                hint={`GET ${ENDPOINT} failed (${failed}).`}
                action={<Button size="sm" variant="ghost" onClick={() => void load(true)}>Try again</Button>}
              />
            )}

            {/* the CLI ran but could not answer — its own words, never a guess */}
            {!failed && data && !data.available && (
              <StateBlock
                title="MCP health is unavailable"
                hint={data.reason || "`claude mcp list` could not be run on this host."}
              />
            )}

            {/* empty */}
            {!failed && data?.available && servers.length === 0 && (
              <StateBlock
                title="No MCP servers configured"
                hint="Add one with `claude mcp add <name> <command>`, or install a plugin that brings one. Then refresh."
              />
            )}

            {/* content */}
            {!failed && servers.length > 0 && (
              <TableScroll className={servers.length > OVERFLOW_AT ? "mcp-scroll" : undefined}>
                <Table>
                  <thead>
                    <tr>
                      <th>Server</th>
                      <th>Origin</th>
                      <th>Transport</th>
                      <th>Health</th>
                      <th>Tools</th>
                      <th>Per turn</th>
                    </tr>
                  </thead>
                  <tbody>
                    {servers.map((s) => {
                      const expanded = open === s.name;
                      return [
                        <tr key={s.name}>
                          <td>
                            <button
                              type="button"
                              className={`mcp-name${expanded ? " is-open" : ""}`}
                              aria-expanded={expanded}
                              onClick={() => setOpen((v) => (v === s.name ? null : s.name))}
                            >
                              <ChevronRight size={14} strokeWidth={1.8} aria-hidden className="mcp-name__chev" />
                              <span className="ws-mono">{s.name}</span>
                            </button>
                          </td>
                          <td><Tag hue={catFor(s.origin || "unknown")}>{s.origin || "unknown"}</Tag></td>
                          <td><span className="ws-muted">{s.transport}</span></td>
                          <td><StatusDot status={s.kind}>{s.state}</StatusDot></td>
                          <td>
                            <span className="ws-muted">
                              {priced ? `${s.tools.length}` : "—"}
                            </span>
                          </td>
                          <td className="ws-td-num">
                            {s.tokens === null ? "—" : formatTokens(s.tokens)}
                          </td>
                        </tr>,
                        // Why it is broken, in the CLI's own words. Always
                        // visible rather than behind the disclosure: this is the
                        // sentence the operator opened the page to find.
                        s.detail ? (
                          <tr key={`${s.name}:why`} className="mcp-why">
                            <td colSpan={6}><span className="ws-mono">{s.detail}</span></td>
                          </tr>
                        ) : null,
                        expanded ? (
                          <tr key={`${s.name}:more`} className="mcp-more">
                            <td colSpan={6}>
                              <Stack gap="sm">
                                <div>
                                  <span className="ws-muted">command </span>
                                  <span className="ws-mono">{s.target || "not reported"}</span>
                                </div>
                                <div>
                                  <span className="ws-muted">defined in </span>
                                  <span className="ws-mono">{s.source || "not found"}</span>
                                  {s.scope && (
                                    <span className="ws-muted"> — {SCOPE_HELP[s.scope] ?? s.scope}</span>
                                  )}
                                </div>
                                {s.shadowed && (
                                  <StatusDot status="warn">
                                    <span className="ws-muted">
                                      defined in more than one place; which one the CLI uses is not reported
                                    </span>
                                  </StatusDot>
                                )}
                                {!s.spec_available && (
                                  <StatusDot status="warn">
                                    <span className="ws-muted">
                                      no launch spec found, so this server cannot be turned off for a
                                      single chat — only globally, with `claude mcp remove`
                                    </span>
                                  </StatusDot>
                                )}
                                {priced && s.tools.length === 0 && (
                                  <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                                    Contributed no tools to the reading. A server that fails to connect
                                    loads none — it costs a startup attempt every turn and returns nothing.
                                  </span>
                                )}
                                <ToolList tools={s.tools} />
                              </Stack>
                            </td>
                          </tr>
                        ) : null,
                      ];
                    })}
                  </tbody>
                </Table>
              </TableScroll>
            )}

            {/* The cost probe is the second of two readings and fails
                independently. Saying so beats printing dashes with no reason. */}
            {!failed && data?.available && data.cost_available === false && (
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                Tool counts and token costs are unavailable: the `/context` reading could not be
                taken in {data.cwd || "the chat directory"}. Health above is unaffected.
              </p>
            )}
          </Card>
        </section>

        <section>
          <SectionLabel>Per-chat</SectionLabel>
          <Card>
            <Stack gap="sm">
              <span style={{ fontSize: "var(--fs-sm)" }}>
                A single chat can run with a subset of these servers. That switch lives in the
                chat, on the context panel behind the meter, because it is a property of the
                conversation rather than of this machine.
              </span>
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                To remove a server everywhere instead, use `claude mcp remove &lt;name&gt;` — this
                page reports the CLI's configuration, it does not hold its own.
              </span>
              <div>
                <Link to="/chats" style={{ color: "var(--accent)" }}>Open a chat ↗</Link>
              </div>
            </Stack>
          </Card>
        </section>
      </Stack>
    </div>
  );
}
