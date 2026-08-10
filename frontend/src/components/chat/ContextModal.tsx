import { useCallback, useEffect, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import {
  Button, Modal, SectionLabel, Skeleton, StackedBar, StateBlock, catFor,
} from "../../ui";
import {
  contextHeadline, contextSlices, formatPct, formatTokens, mcpTotal,
  type ContextDetail,
} from "./contextBreakdown";

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
//
// THE NUMBER AT THE TOP IS THE METER'S OWN. See contextBreakdown: the CLI
// supplies the shape, our measurement supplies the size. A panel opened from a
// meter reading 44% may not say 22% two pixels away.

function tokenColor(hue: string): string {
  return `var(--cat-${hue})`;
}

function Row({ label, tokens, window: win, hue, sub }: {
  label: string; tokens: number; window: number; hue?: string; sub?: string;
}) {
  return (
    <div className="ctx-row">
      <span className="ctx-row__swatch" style={hue ? { background: tokenColor(hue) } : undefined} aria-hidden />
      <span className="ctx-row__label">
        {label}
        {sub && <span className="ctx-row__sub">{sub}</span>}
      </span>
      <span className="ctx-row__tokens">{formatTokens(tokens)}</span>
      <span className="ctx-row__pct">{formatPct(win > 0 ? tokens / win : 0)}</span>
    </div>
  );
}

export default function ContextModal({
  open, onClose, threadId, onCompact, onClear, compacting,
}: {
  open: boolean;
  onClose: () => void;
  threadId: string | null;
  onCompact: () => void;
  onClear: () => void;
  compacting: boolean;
}) {
  const [data, setData] = useState<ContextDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [openServer, setOpenServer] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!threadId) return;
    setLoading(true);
    setFailed(false);
    try {
      const r = await fetch(`/api/chat/threads/${encodeURIComponent(threadId)}/context/detail`);
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
    } catch {
      // A failed read is not an empty context. Say which it is.
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [threadId]);

  useEffect(() => { if (open) void load(); }, [open, load]);

  const win = data?.window_tokens ?? 0;
  const slices = data ? contextSlices(data, catFor) : [];
  const mcp = data?.mcp ?? [];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Context"
      size="lg"
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Close</Button>
          {/* The two things that change the number above them. They live here
              because this is where the operator is when the number worries
              them. Clear is the more destructive of the two (a fold keeps a
              summary; a clear does not) so it sits away from the primary. */}
          <Button variant="ghost" onClick={onClear}>Clear context</Button>
          <Button onClick={onCompact} disabled={compacting}>
            {compacting ? "Compacting" : "Compact now"}
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
          action={<Button variant="ghost" onClick={() => void load()}>Try again</Button>}
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

          {data.available && (
            <>
              <SectionLabel>What is using it</SectionLabel>
              <div className="ctx-list">
                {slices.map((s) => (
                  <Row key={s.key} label={s.key} tokens={s.tokens} window={win} hue={s.hue} />
                ))}
                <Row label="Free" tokens={Math.max(0, win - data.used_tokens)} window={win} />
              </div>

              {mcp.length > 0 && (
                <>
                  {/* The one line here that is actionable. An MCP server's tools
                      are a standing tax on every turn, paid whether or not they
                      are ever called, and until this panel existed it was
                      invisible. */}
                  <SectionLabel>
                    MCP servers ({mcp.length}, {formatTokens(mcpTotal(data))} every turn)
                  </SectionLabel>
                  <div className="ctx-list">
                    {mcp.map((s) => (
                      <div key={s.server}>
                        <button
                          type="button"
                          className={`ctx-expand${openServer === s.server ? " is-open" : ""}`}
                          aria-expanded={openServer === s.server}
                          onClick={() => setOpenServer((v) => (v === s.server ? null : s.server))}
                        >
                          <ChevronRight size={14} strokeWidth={1.8} aria-hidden className="ctx-expand__chev" />
                          <Row
                            label={s.server}
                            sub={`${s.tools.length} tool${s.tools.length === 1 ? "" : "s"}`}
                            tokens={s.tokens}
                            window={win}
                            hue={catFor(s.server)}
                          />
                        </button>
                        {openServer === s.server && (
                          <div className="ctx-sublist">
                            {s.tools.map((t) => (
                              <Row key={t.name} label={t.name} tokens={t.tokens} window={win} />
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}

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

          <div className="ctx-foot">
            <button type="button" className="ctx-refresh" onClick={() => void load()} disabled={loading}>
              <RefreshCw size={13} strokeWidth={1.8} aria-hidden />
              {loading ? "Reading" : "Refresh"}
            </button>
            {data.model && <span className="ctx-foot__model">{data.model}</span>}
          </div>
        </div>
      )}
    </Modal>
  );
}
