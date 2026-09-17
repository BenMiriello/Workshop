import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { foldControls, type FoldMode } from "./foldControls";
import { Link } from "react-router-dom";
import { Check, ChevronRight, Pencil, RefreshCw } from "lucide-react";
import {
  ActionBar, Button, Modal, SectionLabel, Skeleton, StackedBar, StateBlock,
  StatusDot, StatusPill, Toggle, catFor, useEditMode,
  Notice,
} from "@workshop/ui";
import {
  contextHeadline, contextSlices, formatPct, formatTokens, refreshedLabel,
  type ContextDetail,
} from "./contextBreakdown";
import {
  healthSummaryLine, mergeMcpServers, worstKind,
  type McpHealth, type McpRow,
} from "./mcpHealth";
import {
  decode, encode, isEnabled, selectionSummary, toggle as toggleServer,
  type McpSelection,
} from "./mcpSelection";

// What is filling this conversation's context, and the two things you can do
// about it.
//
// REPLACES `/context`, which dumped sixty lines of markdown table into the
// transcript, permanently, where it scrolled away and was never read again. This
// is the same data asked the same question, in the place the question occurs:
// behind the percentage the operator is already looking at.
//
// FREE TO OPEN. The CLI computes `/context` locally — measured 2026-08-10 at
// zero tokens, zero API calls, ~2.8s — so refreshing costs nothing but the wait.
// It is now also CACHED per thread server-side, so the wait is usually gone
// too; the footer prints the reading's age, and Refresh forces a new one.
//
// THE NUMBER AT THE TOP IS THE METER'S OWN. See contextBreakdown: the CLI
// supplies the shape, our measurement supplies the size. A panel opened from a
// meter reading 44% may not say 22% two pixels away.
//
// MCP IS THE SECOND QUERY. Cost comes from `/context`, health from
// `claude mcp list`, and a broken server appears only in the latter — it loads
// no tools, so it costs nothing and was therefore invisible. See mcpHealth.
//
// IT OPENS SMALL. The dialog is reached by tapping a percentage, and what the
// tap is asking is "how full is this, and can I do something about it" — the
// number, the bar, and the two buttons. Everything else (the per-category rows,
// the MCP roster, memory files, skills, subagents, the freshness footer) is the
// follow-up question, so it is behind Details and the dialog is a `default`
// rather than a `lg` until it is asked for.
//
// This was not a taste call. Expanded, on a phone, the dialog filled the screen
// edge to edge, which is the shape of a PAGE, and a reader who has lost the
// sense that they are on a layer has to go hunting for the way back. Operator,
// 2026-09-16: "refactor the compact modal so that it just shows the 3 buttons
// and there's a button to view more and THEN it shows all that rest of the info
// but also in a smaller modal... scrollable within... though the buttons should
// always be visible and the buttons aren't in the scrollable area."
//
// THE LAST CLAUSE WAS ALREADY TRUE and stays true for free: `.ws-modal__body`
// is the flex child that scrolls and `.ws-modal__actions` is `flex: 0 0 auto`
// beside it, so the buttons have never been inside the scroll area. What was
// missing was a height at which scrolling begins, which is a kit-level cap
// (components.css, the 560px block) rather than anything this file does.
//
// AND THE MCP PROBE FOLLOWS THE DISCLOSURE. `claude mcp list` is a subprocess
// on the host; running it on every open to fill a roster nobody asked to see
// was work done for a closed drawer. It runs when Details is opened, once.
//
// MCP IS ALSO WRITABLE, and that half is behind Edit mode (rule 7). Turning a
// server off changes what the next turn can DO, which puts it firmly on the
// config side of the commit table — a stray tap must not silently remove a
// chat's access to Plaid. View state (which server is expanded) still commits
// on click; the switches do not commit until Save.
//
// The default is ALL servers and it must stay indistinguishable from never
// having chosen; see mcpSelection for why that asymmetry is load-bearing.

// How often the "Refreshed Nm ago" line re-renders. Half a minute: the label's
// own resolution is a minute, so this can never be more than one tick behind.
const AGE_TICK_MS = 30_000;

function tokenColor(hue: string): string {
  return `var(--cat-${hue})`;
}

function Row({ label, tokens, window: win, hue, sub }: {
  label: string; tokens: number; window: number; hue?: string; sub?: ReactNode;
}) {
  return (
    <div className="ctx-row">
      <span className="ctx-row__swatch" style={hue ? { background: tokenColor(hue) } : undefined} aria-hidden />
      <span className="ctx-row__label">
        {label}
        {sub != null && <span className="ctx-row__sub">{sub}</span>}
      </span>
      <span className="ctx-row__tokens">{formatTokens(tokens)}</span>
      <span className="ctx-row__pct">{formatPct(win > 0 ? tokens / win : 0)}</span>
    </div>
  );
}

/** A server's own line: how many tools it contributes, and whether it works.
 *  The state is the CLI's own words, so the panel never paraphrases a vendor
 *  into something more or less alarming than what it said. */
function ServerSub({ row, off }: { row: McpRow; off?: boolean }) {
  const tools = `${row.tools.length} tool${row.tools.length === 1 ? "" : "s"}`;
  // "Off for this chat" is said in words, not by a sixth status color: it is a
  // choice the operator made, not a condition of the server (rule 3).
  const chosen = off ? <span className="ctx-row__sub">off for this chat</span> : null;
  if (!row.health) return <>{tools}{chosen}</>;
  return (
    <>
      <StatusDot status={row.health.kind}>{row.health.state}</StatusDot>
      <span className="ctx-row__sub">{tools}</span>
      {chosen}
    </>
  );
}

export default function ContextModal({
  open, onClose, threadId, onCompact, onClear, foldMode,
}: {
  open: boolean;
  onClose: () => void;
  threadId: string | null;
  onCompact: () => void;
  onClear: () => void;
  /** Which fold is running, from the server as well as this tab.
   *  Null means none, and only then is the action offered. */
  foldMode: FoldMode | null;
}) {
  // One rule for both fold buttons; see foldControls.
  const folds = foldControls(foldMode);
  const [data, setData] = useState<ContextDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [health, setHealth] = useState<McpHealth | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);
  const [healthFailed, setHealthFailed] = useState(false);
  const [openServer, setOpenServer] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [saveFailed, setSaveFailed] = useState<string | null>(null);
  // CLOSED ON EVERY OPEN, never remembered. A dialog that reopens in whatever
  // shape it was left in makes its own size unpredictable, and this one is
  // opened from a meter reading for a glance far more often than for an audit.
  const [showDetail, setShowDetail] = useState(false);

  // STILL TWO INDEPENDENT READINGS, and neither may hold the other up: a failing
  // MCP probe must not blank the breakdown, and vice versa. What changed is that
  // they are no longer issued together, because they are no longer wanted at the
  // same moment. The breakdown is the headline and loads on open; the roster is
  // behind Details and loads when Details is opened.
  const loadBreakdown = useCallback(async (force = false) => {
    if (!threadId) return;
    const q = force ? "?refresh=1" : "";
    const id = encodeURIComponent(threadId);
    setLoading(true);
    setFailed(false);
    try {
      const r = await fetch(`/api/chat/threads/${id}/context/detail${q}`);
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
    } catch {
      // A failed read is not an empty context. Say which it is.
      setFailed(true);
    } finally {
      setLoading(false);
      setNow(Date.now());
    }
  }, [threadId]);

  const loadMcp = useCallback(async (force = false) => {
    if (!threadId) return;
    const q = force ? "?refresh=1" : "";
    const id = encodeURIComponent(threadId);
    setHealthLoading(true);
    setHealthFailed(false);
    try {
      const r = await fetch(`/api/chat/threads/${id}/mcp/health${q}`);
      if (!r.ok) throw new Error(String(r.status));
      setHealth(await r.json());
    } catch {
      setHealthFailed(true);
    } finally {
      setHealthLoading(false);
      setNow(Date.now());
    }
  }, [threadId]);

  /** Refresh whatever is actually on screen. The roster is not re-probed while
   *  it is hidden: a Refresh the reader cannot see the result of is a subprocess
   *  spawned for nothing. */
  const load = useCallback(async (force = false) => {
    await Promise.all([loadBreakdown(force), showDetail ? loadMcp(force) : Promise.resolve()]);
  }, [loadBreakdown, loadMcp, showDetail]);

  useEffect(() => {
    if (!open) return;
    // Every open starts collapsed and re-reads the headline.
    setShowDetail(false);
    void loadBreakdown();
  }, [open, loadBreakdown]);

  // The roster, once, when the drawer is first opened. `health` being null is
  // the "never fetched" state; a probe that FAILED leaves `healthFailed` set and
  // its own Try again, so this does not retry it on every re-render.
  useEffect(() => {
    if (!open || !showDetail) return;
    if (health !== null || healthLoading || healthFailed) return;
    void loadMcp();
  }, [open, showDetail, health, healthLoading, healthFailed, loadMcp]);

  // The age line has to keep counting while the panel sits open, or "Refreshed
  // just now" stays on screen for an hour.
  useEffect(() => {
    if (!open) return;
    const t = window.setInterval(() => setNow(Date.now()), AGE_TICK_MS);
    return () => window.clearInterval(t);
  }, [open]);

  const win = data?.window_tokens ?? 0;
  const slices = data ? contextSlices(data, catFor) : [];
  const servers = mergeMcpServers(data?.mcp, health?.available ? health.servers : undefined);
  const worst = worstKind(servers);
  const summary = healthSummaryLine(servers);

  // ---- the per-chat MCP selection ----------------------------------------
  // Only servers whose launch spec the backend could find can be switched: a
  // subset runs under --strict-mcp-config, which means the survivors have to be
  // re-declared, and a server we hold no spec for cannot be. Offering a switch
  // that silently did nothing (or worse, dropped a different server) would be
  // the exact class of quiet lie this panel exists to remove.
  const switchable = useMemo(
    () => servers.filter((s) => s.health?.spec_available).map((s) => s.name),
    [servers]);
  const stored: McpSelection = health?.enabled ?? null;
  // The draft is a STRING, not an array: useEditMode compares drafts shallowly,
  // and two arrays with equal contents are never shallow-equal, so an array
  // draft would read as dirty from the moment Edit opened.
  const current = useMemo(() => ({ sel: encode(stored) }), [stored]);

  const edit = useEditMode(current, async (draft) => {
    setSaveFailed(null);
    const id = encodeURIComponent(threadId ?? "");
    const r = await fetch(`/api/chat/threads/${id}/mcp/servers`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ servers: decode(draft.sel) }),
    });
    if (!r.ok) {
      setSaveFailed(`PUT /api/chat/threads/${threadId ?? ""}/mcp/servers returned ${r.status}`);
      throw new Error(String(r.status));
    }
    const body = await r.json();
    // Take the SERVER's answer, not the draft: it is the side that collapses
    // "every box ticked" back to "all", and the panel must show what was
    // actually stored rather than what was asked for.
    setHealth((h) => (h ? { ...h, enabled: body.enabled ?? null } : h));
  });

  const draftSel = edit.editing ? decode(edit.draft.sel) : stored;
  const selSummary = selectionSummary(draftSel, switchable);
  // The oldest of the two readings is the one the footer must date: saying the
  // panel was refreshed a minute ago when half of it is five minutes old would
  // be the same lie the freshness line exists to prevent.
  const ages = [data?.checked_at, health?.checked_at].filter(
    (v): v is number => typeof v === "number" && v > 0);
  const age = ages.length ? refreshedLabel(Math.min(...ages), now) : "";
  // Rule 9's overflow case: eight servers is where the list stops being a
  // glance and starts being a scroll, and this host already has eight.
  const mcpOverflow = servers.length > 8;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Context"
      // Only the expanded shape needs the wide measure: collapsed, this is a
      // number, a bar and three buttons.
      size={showDetail ? "lg" : "default"}
      actions={
        <>
          {/* THE DISCLOSURE LIVES IN THE ACTION ROW, not in the body, so it is
              reachable at any scroll position and cannot itself scroll away.
              `actions-lead` puts it at the far end from the two controls that
              change the chat, because it changes only this dialog. Hidden while
              editing: collapsing the drawer out from under an unsaved server
              selection would strand it. */}
          {!edit.editing && (
            <Button
              variant="ghost"
              className="ws-modal__actions-lead"
              aria-expanded={showDetail}
              onClick={() => setShowDetail((v) => !v)}
            >
              {showDetail ? "Less" : "Details"}
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>Close</Button>
          {/* The two things that change the number above them. They live here
              because this is where the operator is when the number worries
              them. Clear is the more destructive of the two (a fold keeps a
              summary; a clear does not) so it sits away from the primary. */}
          {/* Both controls come from one rule (foldControls), so Clear cannot
              drift back into being reachable while Compact is inert. */}
          <Button
            variant="ghost"
            onClick={folds.clear.inert ? undefined : onClear}
            aria-disabled={folds.clear.inert || undefined}
            title={folds.clear.reason}
          >
            {folds.clear.label}
          </Button>
          {/* Inactive while EITHER fold runs, with the reason on hover. The
              server publishes the operation now, so a fold another tab started
              disables this too; it used to be refused only after the fact, by
              an error string. A running CLEAR used to make this read
              "Compacting", which is why the mode is passed and not a boolean. */}
          {/* aria-disabled, NOT disabled: a natively disabled button drops
              pointer events, so its reason never fires on a tap and does not
              fire at all in some browsers, and the reason is the point. The kit
              styles both the same (components.css) and Menu.tsx does this too. */}
          <Button
            onClick={folds.compact.inert ? undefined : onCompact}
            aria-disabled={folds.compact.inert || undefined}
            title={folds.compact.reason}
          >
            {folds.compact.label}
          </Button>
        </>
      }
    >
      {loading && !data && (
        <div className="ctx-body">
          <Skeleton /><Skeleton /><Skeleton />
        </div>
      )}

      {failed && (
        <StateBlock
          variant="error"
          title="Couldn't read the breakdown"
          hint={`GET /api/chat/threads/${threadId ?? ""}/context/detail failed.`}
          action={<Button variant="ghost" onClick={() => void load(true)}>Try again</Button>}
        />
      )}

      {!failed && data && (
        <div className="ctx-body">
          <div className="ctx-head">
            <span className="ctx-head__total">{contextHeadline(data)}</span>
            <span className="ctx-head__pct">
              {formatPct(win > 0 ? data.used_tokens / win : 0)}
            </span>
          </div>

          <StackedBar
            className="ctx-bar"
            max={win || undefined}
            segments={[
              ...slices.map((s) => ({
                key: s.key,
                value: s.tokens,
                color: tokenColor(s.hue),
              })),
              // The remainder, drawn in the inset tone so it reads as absence
              // rather than as another category.
              { key: "Free", value: Math.max(0, win - data.used_tokens), color: "var(--bg-inset)" },
            ]}
          />

          {!data.available && (
            <StateBlock
              title="No breakdown for this chat"
              hint={data.reason || "The detail could not be read, but the total above is measured and current."}
            />
          )}

          {/* EVERYTHING BELOW IS THE FOLLOW-UP QUESTION. The headline and the bar
              above answer "how full is it"; these answer "with what", which is a
              different question and a much taller one. See the note at the top of
              this file for why it is closed by default. */}
          {showDetail && (<>
          {data.available && (
            <>
              {/* No label over this list. It is the first thing under the bar
                  that the bar is a picture of — naming it said nothing the
                  reader did not already have. */}
              <div className="ctx-list">
                {slices.map((s) => (
                  <Row key={s.key} label={s.key} tokens={s.tokens} window={win} hue={s.hue} />
                ))}
                <Row label="Free" tokens={Math.max(0, win - data.used_tokens)} window={win} />
              </div>
            </>
          )}

          {/* MCP stands OUTSIDE the `available` gate on purpose: which servers
              are configured and whether they connect is a fact about the
              machine, and it is still true — still worth showing — for a chat
              whose own breakdown could not be read. */}
          <div className="ctx-mcp-head">
            <SectionLabel>MCP servers</SectionLabel>
            {healthLoading && servers.length > 0 && (
              <StatusPill status="running">Checking</StatusPill>
            )}
            {!healthLoading && worst && summary && (
              <StatusPill status={worst}>{summary}</StatusPill>
            )}
            {!healthLoading && selSummary && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{selSummary}</span>
            )}
            {!edit.editing && switchable.length > 0 && (
              <Button size="sm" variant="ghost" onClick={edit.begin}>
                <Pencil aria-hidden /> Edit
              </Button>
            )}
            {edit.flash && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                <Check size={13} strokeWidth={1.8} aria-hidden /> Saved
              </span>
            )}
          </div>

          {healthLoading && servers.length === 0 && (
            <div className="ctx-list">
              <Skeleton /><Skeleton /><Skeleton />
            </div>
          )}

          {healthFailed && (
            <Notice
              tone="error"
              where={`/api/chat/threads/${threadId ?? ""}/mcp/health`}
              action={<Button size="sm" variant="ghost" onClick={() => void load(true)}>Try again</Button>}
            >
              Couldn't check MCP servers.
            </Notice>
          )}

          {!healthFailed && health && !health.available && (
            <StateBlock
              title="MCP health is unavailable"
              hint={health.reason || "`claude mcp list` could not be run on this host."}
            />
          )}

          {!healthFailed && !healthLoading && servers.length === 0 && health?.available && (
            <StateBlock
              title="No MCP servers configured"
              hint="Add one with `claude mcp add <name> <command>`, then refresh."
            />
          )}

          {servers.length > 0 && (
            <div className={`ctx-list${mcpOverflow ? " ctx-list--scroll" : ""}`}>
              {servers.map((s) => {
                const on = isEnabled(draftSel, s.name);
                const row = (
                  <Row
                    label={s.name}
                    sub={<ServerSub row={s} off={!on} />}
                    tokens={s.tokens}
                    window={win}
                    hue={catFor(s.key)}
                  />
                );
                const canSwitch = !!s.health?.spec_available;
                return (
                  <div key={s.key} className={on ? undefined : "ctx-off"}>
                    {edit.editing ? (
                      // Edit mode: the row stops being a disclosure and becomes
                      // a switch. One gesture per mode — a row that both
                      // expanded and toggled would make the tap ambiguous.
                      <div className="ctx-expand ctx-expand--edit">
                        {canSwitch ? (
                          <Toggle
                            className="ctx-toggle"
                            checked={on}
                            onChange={() => edit.set(
                              "sel",
                              encode(toggleServer(draftSel, s.name, switchable)))}
                          />
                        ) : (
                          <span className="ctx-expand__chev" aria-hidden />
                        )}
                        {row}
                      </div>
                    ) : s.tools.length > 0 ? (
                      <button
                        type="button"
                        className={`ctx-expand${openServer === s.key ? " is-open" : ""}`}
                        aria-expanded={openServer === s.key}
                        onClick={() => setOpenServer((v) => (v === s.key ? null : s.key))}
                      >
                        <ChevronRight size={14} strokeWidth={1.8} aria-hidden className="ctx-expand__chev" />
                        {row}
                      </button>
                    ) : (
                      // A server whose tools never loaded — the failing ones —
                      // has nothing to expand onto. A plain row, not a disabled
                      // control: a control that opens onto nothing is worse
                      // than no control, and rule 10 has no cursor for "inert".
                      <div className="ctx-expand ctx-expand--leaf">
                        <span className="ctx-expand__chev" aria-hidden />
                        {row}
                      </div>
                    )}
                    {/* Why it is broken, in the CLI's words. Always visible
                        rather than behind a disclosure: this is the sentence
                        the operator opened the panel to find. */}
                    {s.health?.detail && <div className="ctx-detail">{s.health.detail}</div>}
                    {/* And why it has no switch, when it has none. Saying "we
                        do not know where this server is configured" is the
                        honest version of an absent control. */}
                    {edit.editing && !canSwitch && (
                      <div className="ctx-nospec">
                        No launch spec found for this server, so it cannot be turned off for one
                        chat.
                      </div>
                    )}
                    {!edit.editing && openServer === s.key && s.tools.length > 0 && (
                      <div className="ctx-sublist">
                        {s.tools.map((t) => (
                          <Row key={t.name} label={t.name} tokens={t.tokens} window={win} />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {edit.editing && (
            <>
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                Applies from the next turn. A run already in flight had its tools fixed when it
                started. Every server is on until you turn one off.{" "}
                <Link to="/settings/mcp" style={{ color: "var(--accent)" }}>
                  Settings → MCP
                </Link>{" "}
                lists them all, with what each costs.
              </p>
              <ActionBar flash={edit.dirty ? "unsaved" : null}>
                <Button onClick={() => void edit.save()} disabled={edit.saving}>
                  {edit.saving ? "Saving" : "Save"}
                </Button>
                <Button variant="ghost" onClick={() => { setSaveFailed(null); edit.cancel(); }}>
                  Cancel
                </Button>
              </ActionBar>
            </>
          )}

          {saveFailed && (
            <Notice
              tone="error"
              detail={saveFailed}
              action={<Button size="sm" variant="ghost" onClick={() => void edit.save()}>Try again</Button>}
            >
              Couldn't save the server selection.
            </Notice>
          )}

          {!edit.editing && switchable.length > 0 && (
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
              <Link to="/settings/mcp" style={{ color: "var(--accent)" }}>Settings → MCP</Link>
              {" "}lists every server on this machine, where it comes from, and what it costs.
            </p>
          )}

          {data.available && (
            <>
              {(data.memory?.length ?? 0) > 0 && (
                <>
                  <SectionLabel>Memory files</SectionLabel>
                  <div className="ctx-list">
                    {data.memory!.map((m) => (
                      <Row key={m.path} label={m.path} sub={m.kind} tokens={m.tokens} window={win} />
                    ))}
                  </div>
                </>
              )}

              {(data.skills?.length ?? 0) > 0 && (
                <>
                  <SectionLabel>Skills ({data.skills!.length})</SectionLabel>
                  <div className="ctx-list">
                    {data.skills!.map((s) => (
                      <Row key={s.name} label={s.name} sub={s.source} tokens={s.tokens} window={win} />
                    ))}
                  </div>
                </>
              )}

              {(data.agents?.length ?? 0) > 0 && (
                <>
                  <SectionLabel>Subagents</SectionLabel>
                  <div className="ctx-list">
                    {data.agents!.map((a) => (
                      <Row key={a.name} label={a.name} sub={a.source} tokens={a.tokens} window={win} />
                    ))}
                  </div>
                </>
              )}
            </>
          )}

          </>)}

          {/* THE FOOT STAYS OUT OF THE DRAWER, deliberately. It dates the number
              the reader is about to act on, and this file already argues that an
              undated number which might be five minutes old is worse than a slow
              fresh one. Hiding the date behind a disclosure would leave the
              headline looking more certain than it is. */}
          <div className="ctx-foot">
            <button type="button" className="ctx-refresh" onClick={() => void load(true)} disabled={loading || healthLoading}>
              <RefreshCw size={13} strokeWidth={1.8} aria-hidden />
              {loading || healthLoading ? "Reading" : "Refresh"}
            </button>
            {/* Dated, because the reading may be a cached one. An undated
                number that might be five minutes old is worse than a slow
                fresh one. */}
            {age && <span className="ctx-foot__age">{age}</span>}
            {data.model && <span className="ctx-foot__model">{data.model}</span>}
          </div>
        </div>
      )}
    </Modal>
  );
}
