// UiCard: the interactive in-chat UI primitive.
//
// A provider's model can emit a ```ui-card fenced JSON block inside its reply.
// The chat renderer (Chat.tsx) extracts each block and renders it here as an
// editable widget built from the design system, instead of a code block. The
// card is NEVER auto-applied: buttons map to registered client handlers
// (uiCardActions.ts), which are the only thing a card can drive.
//
// Malformed JSON or an unknown kind renders an inert fallback (read-only, no
// live buttons) and keeps the raw text visible; rendering never throws.
//
// State-on-reload: cards are stateless server-side in Phase 1. A reloaded
// thread re-renders each card in edit state from the stored block; createProject
// handles 409 so a re-submit after reload stays safe. No server-side card state.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Bug, CheckSquare, CircleCheck, History, MessageCircleQuestion, MinusCircle,
  Sparkles,
} from "lucide-react";
import {
  Button,
  Card,
  Field,
  Input,
  ReadOnly,
  Row,
  Segmented,
  Select,
  Skeleton,
  Stack,
  Notice,
  Textarea,
  type SegmentedOption,
} from "@workshop/ui";
import { proposalGaps, readinessLine, scopeLabel } from "../../lib/tasks";
import { pickupFor, type PickupMap } from "../../lib/scopePickup";
import { Reveal } from "./Reveal";
import TicketCard from "./work/TicketCard";
import { displayDefault,
  KNOWN_KINDS,
  UI_CARD_ACTIONS,
  hasCardHandler,
  resolveCardActions,
  slugify,
  type UiCardAction,
  type UiCardCtx,
  type UiCardField,
  type UiCardSpec,
} from "./uiCardActions";
import { normalizeCardSpec } from "./cardSchema";
import type { components } from "../../api/schema.gen";

// ---- Extraction ------------------------------------------------------------
// A parsed answer is an ordered list of markdown runs and ui-card blocks. A
// card whose JSON fails to parse keeps `spec: null` and its `raw` body so the
// renderer can show an inert fallback without losing the original content.
// An `open` part is a trailing ```ui-card fence with no closing fence yet:
// while an answer is still streaming the renderer shows a placeholder instead
// of flashing the half-written JSON; on a stored (finished) message it renders
// as plain markdown so a cut-off reply loses nothing.
export type AnswerPart =
  | { t: "md"; text: string }
  | { t: "card"; spec: UiCardSpec | null; raw: string }
  | { t: "open"; text: string };

const UI_CARD_FENCE = /```ui-card[^\n]*\r?\n([\s\S]*?)```/g;

export function hasUiCard(text: string): boolean {
  return text.includes("```ui-card");
}

export function splitUiCards(text: string): AnswerPart[] {
  const parts: AnswerPart[] = [];
  let last = 0;
  // Fresh regex per call so the /g lastIndex can't leak between calls.
  const re = new RegExp(UI_CARD_FENCE.source, "g");
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push({ t: "md", text: text.slice(last, m.index) });
    let spec: UiCardSpec | null = null;
    try {
      const parsed = JSON.parse(m[1]);
      if (parsed && typeof parsed === "object") spec = parsed as UiCardSpec;
    } catch {
      spec = null; // malformed -> inert fallback, raw kept below
    }
    parts.push({ t: "card", spec, raw: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    // Every closed block was consumed above, so any ```ui-card left in the
    // tail is an unclosed fence (mid-stream, or a cut-off reply).
    const rest = text.slice(last);
    const open = rest.indexOf("```ui-card");
    if (open !== -1) {
      if (open > 0) parts.push({ t: "md", text: rest.slice(0, open) });
      parts.push({ t: "open", text: rest.slice(open) });
    } else {
      parts.push({ t: "md", text: rest });
    }
  }
  if (parts.length === 0) parts.push({ t: "md", text });
  return parts;
}

// Placeholder for a card whose fenced block is still streaming in.
export function UiCardPending() {
  return (
    <Card className="chat-card">
      <Stack gap="sm">
        <Skeleton width="32%" />
        <Skeleton width="100%" />
        <Skeleton width="58%" />
      </Stack>
    </Card>
  );
}

// ---- Template field: its own fetch + four states ---------------------------
type TemplateInfo = { slug: string; label: string; hint: string };

function TemplateField({
  field,
  value,
  onChange,
}: {
  field: UiCardField;
  value: string;
  onChange: (v: string) => void;
}) {
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const label = field.label || "Template";

  useEffect(() => {
    let cancelled = false;
    setErr(null);
    setTemplates(null);
    fetch("/api/projects/templates")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((items: { slug: string; label?: string; hint?: string }[]) => {
        if (cancelled) return;
        const mapped = items.map((t) => ({ slug: t.slug, label: t.label || t.slug, hint: t.hint || "" }));
        // Empty state: always guarantee at least `blank`.
        setTemplates(mapped.length ? mapped : [{ slug: "blank", label: "Blank", hint: "" }]);
      })
      .catch((e) => { if (!cancelled) setErr(String(e)); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  if (err) {
    return (
      <Field label={label}>
        {/* Stands in for a <Select>, so it is a line. A region here put a
            120px dashed panel where a dropdown belongs. */}
        <Notice
          tone="error"
          where="GET /api/projects/templates"
          action={<Button size="sm" variant="ghost" onClick={() => setReloadKey((k) => k + 1)}>Retry</Button>}
        >
          Couldn't load templates.
        </Notice>
      </Field>
    );
  }
  if (templates === null) {
    return (
      <Field label={label}>
        <Skeleton width="100%" />
      </Field>
    );
  }
  return (
    <Field label={label} help={field.help}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {templates.map((t) => (
          <option key={t.slug} value={t.slug}>
            {t.label}{t.hint ? ` · ${t.hint}` : ""}
          </option>
        ))}
      </Select>
    </Field>
  );
}

// ---- Scope field: self-fetching select over the task scopes ----------------
// Mirrors TemplateField's four-state fetch. The task-proposal card uses it so
// the model does not have to enumerate scopes; the prefilled `value` (this
// chat's own scope) stays selected as long as it is one of the returned scopes.
function TaskScopeField({
  field,
  value,
  onChange,
}: {
  field: UiCardField;
  value: string;
  onChange: (v: string) => void;
}) {
  const [scopes, setScopes] = useState<string[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const label = field.label || "Scope";

  useEffect(() => {
    let cancelled = false;
    setErr(null);
    setScopes(null);
    fetch("/api/tasks/scopes")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((j: components["schemas"]["ScopesOut"]) => {
        if (cancelled) return;
        const list = j.scopes ?? [];
        // Always keep the prefilled scope selectable even if it has no store yet.
        const withValue = value && !list.includes(value) ? [value, ...list] : list;
        setScopes(withValue.length ? withValue : ["workshop"]);
      })
      .catch((e) => { if (!cancelled) setErr(String(e)); });
    return () => { cancelled = true; };
  }, [reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (err) {
    return (
      <Field label={label}>
        <Notice
          tone="error"
          where="GET /api/tasks/scopes"
          action={<Button size="sm" variant="ghost" onClick={() => setReloadKey((k) => k + 1)}>Retry</Button>}
        >
          Couldn't load scopes.
        </Notice>
      </Field>
    );
  }
  if (scopes === null) {
    return (
      <Field label={label}>
        <Skeleton width="100%" />
      </Field>
    );
  }
  return (
    <Field label={label} help={field.help}>
      <Select value={value || scopes[0]} onChange={(e) => onChange(e.target.value)}>
        {scopes.map((s) => (
          <option key={s} value={s}>{scopeLabel(s)}</option>
        ))}
      </Select>
    </Field>
  );
}

const SENSITIVITY_OPTIONS: SegmentedOption<string>[] = [
  { value: "private", label: "Private" },
  { value: "shareable", label: "Shareable" },
  { value: "public", label: "Public" },
];

// ---- One field, mapped to a DS control -------------------------------------
function CardField({
  field,
  value,
  onChange,
}: {
  field: UiCardField;
  value: string;
  onChange: (v: string) => void;
}) {
  const label = field.label || field.name;
  const help = field.help;
  switch (field.type) {
    case "textarea":
      return (
        <Field label={label} help={help}>
          <Textarea value={value} rows={6} onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
    case "slug":
      return (
        <Field label={label} help={help || "kebab-case · used as the directory name + route segment"}>
          <Input value={value} spellCheck={false} onChange={(e) => onChange(slugify(e.target.value))} />
        </Field>
      );
    case "template":
      return <TemplateField field={field} value={value} onChange={onChange} />;
    case "taskscope":
      return <TaskScopeField field={field} value={value} onChange={onChange} />;
    case "sensitivity":
      return (
        <Field label={label} help={help}>
          <Segmented
            options={SENSITIVITY_OPTIONS}
            value={value}
            onChange={onChange}
            ariaLabel={label}
          />
        </Field>
      );
    case "select":
      return (
        <Field label={label} help={help}>
          <Select value={value} onChange={(e) => onChange(e.target.value)}>
            {(field.options || []).map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
        </Field>
      );
    case "choice":
      // Tap-friendly options for a prompt card's answer. Same control the
      // sensitivity field uses; the options come from the card.
      return (
        <Field label={label} help={help}>
          <Segmented
            options={(field.options || []).map((o) => ({ value: o.value, label: o.label }))}
            value={value}
            onChange={onChange}
            ariaLabel={label}
          />
        </Field>
      );
    default:
      return (
        <Field label={label} help={help}>
          <Input value={value} onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
  }
}

// ---- The card --------------------------------------------------------------
type Phase = "edit" | "success" | "dismissed";
type SuccessInfo = { slug?: string; title?: string; note?: string; actions?: UiCardAction[] };

function ActionButtons({
  actions,
  busyId,
  onRun,
}: {
  actions: UiCardAction[];
  busyId: string | null;
  onRun: (a: UiCardAction) => void;
}) {
  // Only ever render an action this build can actually run. A model-authored
  // card may name an id with no handler (a typo, or an id from a newer build);
  // a dead disabled button explains nothing, so it is dropped rather than
  // shown. The edit phase already substitutes the kind's canonical actions
  // (resolveCardActions) when this would otherwise be empty, so a known card
  // still shows its real buttons; success actions come from a handler and
  // always resolve. This filter is the last guarantee, wherever actions come in.
  const live = actions.filter((a) => hasCardHandler(a.id));
  if (live.length === 0) return null;
  return (
    <Row wrap>
      {live.map((a) => (
        <Button
          key={a.id}
          variant={a.variant ?? "default"}
          loading={busyId === a.id}
          disabled={busyId != null}
          onClick={() => onRun(a)}
        >
          {a.label}
        </Button>
      ))}
    </Row>
  );
}

// Stable identity for a card, derived from its own block. The card has no id of
// its own, and its position in the transcript shifts as messages arrive, so the
// content IS the identity: the same card block acted on once should stay acted
// on. djb2 over the raw text, hex — short, stable, and collision-safe enough for
// per-thread keys.
export function cardKey(raw: string): string {
  let h = 5381;
  for (let i = 0; i < raw.length; i++) h = ((h * 33) ^ raw.charCodeAt(i)) >>> 0;
  return `c${h.toString(16)}${raw.length.toString(16)}`;
}

// SUPERSESSION, derived from the whole transcript rather than stored anywhere.
//
// When the agent re-proposes work it proposed before, the newer card carries the
// SAME top-level `id`, or a later card sets `supersedes` to the earlier card's
// `id`. The older, still-unresolved proposal should close. This walks every
// assistant body in transcript order, keys each fully-parsed card the same way
// the card itself does (`cardKey` over the fence's raw JSON), and returns the
// set of keys a later card has replaced.
//
// Only fully-parsed cards with a non-empty `id` take part: a card mid-stream has
// no closed fence to key, and one with no id cannot be matched to a later
// proposal. A card whose recorded phase is `success` or `dismissed` is already
// resolved and is NEVER superseded — it keeps its resolved render.
export function computeSupersededKeys(
  bodies: string[],
  cardStates: Record<string, { phase?: string } | undefined> | undefined,
): Set<string> {
  type Entry = { id: string; key: string; phase: string | undefined; supersedes?: string };
  const entries: Entry[] = [];
  for (const body of bodies) {
    if (!body || !body.includes("```ui-card")) continue;
    for (const part of splitUiCards(body)) {
      if (part.t !== "card" || !part.spec) continue;
      const id = typeof part.spec.id === "string" ? part.spec.id.trim() : "";
      if (!id) continue;
      const key = cardKey(part.raw);
      entries.push({
        id,
        key,
        phase: cardStates?.[key]?.phase,
        supersedes: typeof part.spec.supersedes === "string" ? part.spec.supersedes.trim() : "",
      });
    }
  }
  const superseded = new Set<string>();
  const resolved = (e: Entry) => e.phase === "success" || e.phase === "dismissed";

  const byId = new Map<string, Entry[]>();
  for (const e of entries) {
    const list = byId.get(e.id);
    if (list) list.push(e);
    else byId.set(e.id, [e]);
  }
  // Same id more than once: every instance but the last is replaced.
  for (const group of byId.values()) {
    if (group.length < 2) continue;
    for (let i = 0; i < group.length - 1; i++) {
      if (!resolved(group[i])) superseded.add(group[i].key);
    }
  }
  // An explicit `supersedes` closes the card(s) it names.
  for (const e of entries) {
    if (!e.supersedes) continue;
    const targets = byId.get(e.supersedes);
    if (!targets) continue;
    for (const t of targets) {
      if (t.key === e.key) continue;
      if (!resolved(t)) superseded.add(t.key);
    }
  }
  return superseded;
}

// The resolved render (a filed ticket, a dismissed line, a superseded line)
// grows into place when the resolution just happened, and appears settled on a
// reload. `animate` is the whole difference: the caller passes true only when
// the phase changed under the operator's hand this session, so a thread opened
// on an already-filed card does not replay the fill.
function ResolvedCard({ animate, children }: { animate: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(!animate);
  useEffect(() => { if (animate) setOpen(true); }, [animate]);
  if (!animate) return <>{children}</>;
  return <Reveal open={open} className="chat-card-appear">{children}</Reveal>;
}

// The ticket kinds a proposal can carry, each with a lucide icon that names it.
// Local override: the segmented value seeds from the card's `type` (falling back
// to `task`) and files whatever the operator leaves it on.
const TYPE_OPTIONS: SegmentedOption<string>[] = [
  { value: "bug", label: <span className="chat-card__typeopt"><Bug size={14} strokeWidth={1.8} aria-hidden /> Bug</span> },
  { value: "feature", label: <span className="chat-card__typeopt"><Sparkles size={14} strokeWidth={1.8} aria-hidden /> Feature</span> },
  { value: "task", label: <span className="chat-card__typeopt"><CheckSquare size={14} strokeWidth={1.8} aria-hidden /> Task</span> },
];

export function UiCard({ spec: rawSpec, raw, ctx }: { spec: UiCardSpec | null; raw: string; ctx: UiCardCtx }) {
  // THE CLIENT OWNS THE FIELD LIST FOR A STORE-BOUND KIND. The model authors the
  // card as free-form JSON, so without this a field name it invented would
  // render as a real editable control whose value nothing reads. See cardSchema.
  const normalized = useMemo(() => normalizeCardSpec(rawSpec), [rawSpec]);
  const spec = normalized?.spec ?? null;
  // A PROMPT CARD ALWAYS OFFERS A TYPED ANSWER, not only whatever field types
  // the model happened to include. A tap-only card (just `choice`/`select`)
  // otherwise gives no way to answer with your own words, and the operator
  // should not have to know that typing a message straight into the composer
  // instead of the card also works. If the model already gave a free-text
  // field there is nothing to add; composePromptAnswer already ignores an
  // untouched field, so an unused typed-answer box changes nothing when the
  // operator taps a choice instead.
  const fields = useMemo(() => {
    const base = spec?.fields ?? [];
    if (spec?.kind !== "prompt") return base;
    const hasFreeText = base.some((f) => !f.type || f.type === "text" || f.type === "textarea");
    const hasTapOnly = base.some((f) => f.type === "choice" || f.type === "select");
    if (hasFreeText || !hasTapOnly) return base;
    return [...base, { name: "__freeform_answer", label: "Or type your own answer", type: "text" as const }];
  }, [spec]);
  const key = useMemo(() => cardKey(raw), [raw]);
  // What the server says already happened to this card. This is what stops a
  // filed-and-run proposal from coming back as an untouched one after a reload.
  const recorded = ctx.cardStates?.[key];
  const [values, setValues] = useState<Record<string, string>>(() => {
    // Carried values first: a value the schema has no field for stays readable
    // by the handler (an older transcript's `priority`/`effort` still file) but
    // never becomes a control, and a canonical field always wins the key.
    const seed: Record<string, string> = { ...(normalized?.carried ?? {}) };
    for (const f of fields) seed[f.name] = f.value ?? displayDefault(f);
    if (recorded?.slug) seed.slug = recorded.slug;
    // The ticket kind is metadata, not a field, so it lives in values (where the
    // filing handler reads it) without becoming an editable control. It seeds
    // from the card's own `type`, falling back to `task`.
    if (spec?.kind === "task-proposal") seed.type = spec.type || "task";
    return seed;
  });
  const [phase, setPhase] = useState<Phase>(
    recorded?.phase === "success" ? "success" : recorded?.phase === "dismissed" ? "dismissed" : "edit",
  );
  const [success, setSuccess] = useState<SuccessInfo | null>(
    recorded?.phase === "success"
      ? { slug: recorded.slug ?? undefined, title: recorded.title ?? undefined, note: recorded.note ?? undefined }
      : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Was this card resolved by the operator THIS session (as opposed to arriving
  // already-resolved from the server on a reload)? Only a live resolution
  // animates; a reloaded thread renders its filed and dismissed cards settled.
  const actedRef = useRef(false);
  // Whether this card was ALREADY superseded on first render. A card that
  // becomes superseded while the reader is watching collapses; one that opens a
  // thread already superseded appears settled. `null` until the first render
  // captures it.
  const supersededMountRef = useRef<boolean | null>(null);
  // What will actually happen to a ticket filed from this card. The drafts
  // dialog has always asked this before offering "File and run"; the card path
  // did not, so a non-runnable scope produced the after-the-fact footnote the
  // predicate exists to prevent.
  const [pickupMap, setPickupMap] = useState<PickupMap | null>(null);
  const isTaskProposal = spec?.kind === "task-proposal";
  useEffect(() => {
    if (!isTaskProposal) return;
    let cancelled = false;
    fetch("/api/tasks/scopes")
      .then(async (r) => (r.ok ? ((await r.json()) as components["schemas"]["ScopesOut"]) : null))
      .then((j) => { if (!cancelled) setPickupMap((j?.pickup ?? null) as PickupMap | null); })
      .catch(() => { /* falls back to the pure predicate */ });
    return () => { cancelled = true; };
  }, [isTaskProposal]);

  // ONE pickup answer per render, read by all three consumers: the warning
  // sentence, whether "File and run" is offered at all, and the handler that
  // runs when it is pressed. Computing it separately in the handler is what let
  // the button and its own handler disagree.
  const pickup = isTaskProposal
    ? pickupFor(pickupMap, (values.scope || "workshop").trim())
    : null;

  // A card rendered before its thread's states arrived (or acted on in another
  // tab) reconciles when the record shows up, rather than staying stale.
  useEffect(() => {
    if (!recorded) return;
    if (recorded.phase === "success") {
      setPhase("success");
      setSuccess((s) => s ?? {
        slug: recorded.slug ?? undefined,
        title: recorded.title ?? undefined,
        note: recorded.note ?? undefined,
      });
    } else if (recorded.phase === "dismissed") {
      setPhase("dismissed");
    }
  }, [recorded]);

  const known = !!spec && KNOWN_KINDS.has(spec.kind);
  const setValue = (name: string, v: string) => setValues((prev) => ({ ...prev, [name]: v }));

  const runAction = async (action: UiCardAction) => {
    if (!spec) return;
    const handler = UI_CARD_ACTIONS[action.id];
    if (!handler) return; // unknown action id -> inert, no request
    setBusyId(action.id);
    setError(null);
    try {
      const result = await handler({ spec, values }, { ...ctx, pickup });
      if (result.status === "success") {
        if (result.slug) setValues((prev) => ({ ...prev, slug: result.slug as string }));
        if (result.values) setValues((prev) => ({ ...prev, ...result.values }));
        setSuccess({ slug: result.slug, title: result.title, note: result.note, actions: result.actions });
        actedRef.current = true; // a live resolution, so the resolved render animates in
        setPhase("success");
        ctx.recordCardState?.(key, {
          phase: "success", slug: result.slug ?? null, title: result.title ?? null, note: result.note ?? null,
        });
      } else if (result.status === "dismiss") {
        actedRef.current = true; // animate the collapse to the dismissed line
        setPhase("dismissed");
        ctx.recordCardState?.(key, { phase: "dismissed" });
      } else if (result.status === "error") {
        setError(result.message);
      }
      // "noop" -> navigation-only handler; leave the card as-is.
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  };

  const errorBlock = error ? (
    <Notice tone="error" detail={error}>That action failed.</Notice>
  ) : null;

  // Inert fallback: malformed JSON or an unknown kind. Read-only, no live
  // buttons, raw text preserved so nothing is lost.
  if (!spec || !known) {
    return (
      <Card className="chat-card">
        <Stack gap="sm">
          <span className="ws-card__title">{spec?.title || "Card"}</span>
          <span className="ws-card__meta">This card can't be acted on here.</span>
          {spec?.fields?.length ? (
            spec.fields.map((f) => (
              <Field key={f.name} label={f.label || f.name}>
                <ReadOnly value={f.value} />
              </Field>
            ))
          ) : (
            <pre className="ws-mono">{raw.trim()}</pre>
          )}
        </Stack>
      </Card>
    );
  }

  // A LATER PROPOSAL REPLACED THIS ONE (same id, or a later card's `supersedes`).
  // Derived from the whole transcript in Chat.tsx. A resolved card is never
  // superseded, so this only ever applies while phase is still `edit`; it renders
  // a muted, non-interactive line in place of the form.
  const isSuperseded = !!(spec && ctx.supersededKeys?.has(key));
  if (supersededMountRef.current === null) supersededMountRef.current = isSuperseded;
  const animateSupersede = isSuperseded && supersededMountRef.current === false;

  if (phase === "dismissed") {
    return (
      <ResolvedCard animate={actedRef.current}>
        <Card className="chat-card">
          <Row className="ws-card__meta">
            <MinusCircle size={14} strokeWidth={1.8} aria-hidden /> Dismissed
          </Row>
        </Card>
      </ResolvedCard>
    );
  }

  if (phase === "success") {
    // A FILED TICKET IS A TICKET CARD, for the life of the thread. It used to be
    // a one-line "Filed a ticket in X" heading plus two buttons, and the same
    // sentence again in a bar above the transcript; between them they said where
    // the ticket went and nothing about what happened to it next. The card is
    // the subject's home: one ticket, one card, in the place the filing
    // happened. `slug` carries the ticket id across a reload (uiCardActions).
    const filedId = (values.__task_id || values.slug || "").trim();
    if (isTaskProposal && filedId) {
      const filedScope = (values.scope || "workshop").trim();
      return (
        <ResolvedCard animate={actedRef.current}>
          <TicketCard
            id={filedId}
            scope={filedScope}
            filedTitle={success?.title || values.title || ""}
            filedNote={success?.note ?? null}
            pickup={pickupFor(pickupMap, filedScope)}
          />
        </ResolvedCard>
      );
    }
    // A PROMPT CARD KEEPS THE QUESTION. Collapsing straight to a receipt like
    // "Answer sent." threw away the one thing worth re-reading here: what was
    // asked and what was answered. The answer is also posted as its own
    // message below (see submitPrompt), so this is the compact record of the
    // exchange staying where it happened, not a second copy of it.
    if (spec.kind === "prompt") {
      return (
        <ResolvedCard animate={actedRef.current}>
          <Card className="chat-card">
            <Stack gap="sm">
              <Row>
                <MessageCircleQuestion size={16} strokeWidth={1.8} aria-hidden />
                <span className="ws-card__title">{spec.title || "Question"}</span>
              </Row>
              <Row>
                <CircleCheck size={16} strokeWidth={1.8} aria-hidden />
                <span>{success?.note || "Answered"}</span>
              </Row>
              {errorBlock}
            </Stack>
          </Card>
        </ResolvedCard>
      );
    }
    const heading = success?.note || (success?.title ? `Created ${success.title}` : "Done");
    return (
      <ResolvedCard animate={actedRef.current}>
        <Card className="chat-card">
          <Stack>
            <Row>
              <CircleCheck size={16} strokeWidth={1.8} aria-hidden />
              <span>{heading}</span>
            </Row>
            <ActionButtons actions={success?.actions ?? []} busyId={busyId} onRun={(a) => void runAction(a)} />
            {errorBlock}
          </Stack>
        </Card>
      </ResolvedCard>
    );
  }

  // A LATER PROPOSAL REPLACED THIS ONE, and it is still unresolved (both resolved
  // phases returned above, so phase is `edit` here). It collapses to a muted,
  // non-interactive line instead of offering a form that would file duplicate
  // work.
  if (isSuperseded) {
    return (
      <ResolvedCard animate={animateSupersede}>
        <Card className="chat-card">
          <Row className="ws-card__meta">
            <History size={14} strokeWidth={1.8} aria-hidden /> Replaced by a newer proposal
          </Row>
        </Card>
      </ResolvedCard>
    );
  }

  // Edit state.
  //
  // What the proposal does not yet say, as the SAME one line the ticket page
  // shows. Here it is cheapest to act on: the fields are on screen and nothing
  // has been filed, so closing a gap is one edit rather than a round trip
  // through the ticket. Advisory only, exactly as on the ticket page: it never
  // disables File.
  const notReady = isTaskProposal ? readinessLine(proposalGaps(values)) : "";
  // The actions this card offers: the model's own, minus any with no handler,
  // falling back to the kind's canonical set if it named none this build can
  // run, and without the run action where the scope cannot run (the same rule
  // TicketDraftsDialog applies when it hides "File and run now"). A known card
  // can no longer render a dead button or end up with no way to act.
  const editActions = resolveCardActions(spec.kind, spec.actions, {
    runnable: !pickup || pickup.runnable,
  });

  return (
    <Card className="chat-card">
      <Stack>
        <Row>
          {spec.kind === "prompt"
            ? <MessageCircleQuestion size={16} strokeWidth={1.8} aria-hidden />
            : <Sparkles size={16} strokeWidth={1.8} aria-hidden />}
          <span className="ws-card__title">{spec.title || "Card"}</span>
        </Row>
        {isTaskProposal && (
          // The ticket kind, as a compact segmented control in the header. It is
          // metadata, not one of the three form fields, and defaults to the
          // card's own `type` (or `task`); the operator can override it and the
          // choice files as `type`.
          <Segmented
            options={TYPE_OPTIONS}
            value={values.type || "task"}
            onChange={(v) => setValue("type", v)}
            ariaLabel="Ticket type"
          />
        )}
        <Stack>
          {fields.map((f) => (
            <CardField key={f.name} field={f} value={values[f.name] ?? ""} onChange={(v) => setValue(f.name, v)} />
          ))}
        </Stack>
        {notReady && <Notice tone="warn">{notReady}</Notice>}
        {errorBlock}
        <ActionButtons actions={editActions} busyId={busyId} onRun={(a) => void runAction(a)} />
      </Stack>
    </Card>
  );
}
