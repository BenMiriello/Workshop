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
import { useEffect, useMemo, useState } from "react";
import { CircleCheck, MessageCircleQuestion, MinusCircle, Sparkles } from "lucide-react";
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
import { pickupFor, pickupNote, type PickupMap } from "../../lib/scopePickup";
import TicketCard from "./work/TicketCard";
import {
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
    <Card>
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
      .then((j: { scopes?: string[] }) => {
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
            value={value || "shareable"}
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
            value={value || field.options?.[0]?.value || ""}
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
function cardKey(raw: string): string {
  let h = 5381;
  for (let i = 0; i < raw.length; i++) h = ((h * 33) ^ raw.charCodeAt(i)) >>> 0;
  return `c${h.toString(16)}${raw.length.toString(16)}`;
}

export function UiCard({ spec: rawSpec, raw, ctx }: { spec: UiCardSpec | null; raw: string; ctx: UiCardCtx }) {
  // THE CLIENT OWNS THE FIELD LIST FOR A STORE-BOUND KIND. The model authors the
  // card as free-form JSON, so without this a field name it invented would
  // render as a real editable control whose value nothing reads. See cardSchema.
  const normalized = useMemo(() => normalizeCardSpec(rawSpec), [rawSpec]);
  const spec = normalized?.spec ?? null;
  const fields = useMemo(() => spec?.fields ?? [], [spec]);
  const key = useMemo(() => cardKey(raw), [raw]);
  // What the server says already happened to this card. This is what stops a
  // filed-and-run proposal from coming back as an untouched one after a reload.
  const recorded = ctx.cardStates?.[key];
  const [values, setValues] = useState<Record<string, string>>(() => {
    // Carried values first: a value the schema has no field for stays readable
    // by the handler (an older transcript's `priority`/`effort` still file) but
    // never becomes a control, and a canonical field always wins the key.
    const seed: Record<string, string> = { ...(normalized?.carried ?? {}) };
    for (const f of fields) seed[f.name] = f.value ?? "";
    if (recorded?.slug) seed.slug = recorded.slug;
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
      .then((r) => (r.ok ? r.json() : { pickup: null }))
      .then((j) => { if (!cancelled) setPickupMap(j.pickup ?? null); })
      .catch(() => { /* falls back to the pure predicate */ });
    return () => { cancelled = true; };
  }, [isTaskProposal]);

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
      const result = await handler({ spec, values }, ctx);
      if (result.status === "success") {
        if (result.slug) setValues((prev) => ({ ...prev, slug: result.slug as string }));
        if (result.values) setValues((prev) => ({ ...prev, ...result.values }));
        setSuccess({ slug: result.slug, title: result.title, note: result.note, actions: result.actions });
        setPhase("success");
        ctx.recordCardState?.(key, {
          phase: "success", slug: result.slug ?? null, title: result.title ?? null, note: result.note ?? null,
        });
      } else if (result.status === "dismiss") {
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
      <Card>
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

  if (phase === "dismissed") {
    return (
      <Card>
        <Row className="ws-card__meta">
          <MinusCircle size={14} strokeWidth={1.8} aria-hidden /> Dismissed
        </Row>
      </Card>
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
        <TicketCard
          id={filedId}
          scope={filedScope}
          filedTitle={success?.title || values.title || ""}
          filedNote={success?.note ?? null}
          pickup={pickupFor(pickupMap, filedScope)}
        />
      );
    }
    const heading = success?.note || (success?.title ? `Created ${success.title}` : "Done");
    return (
      <Card>
        <Stack>
          <Row>
            <CircleCheck size={16} strokeWidth={1.8} aria-hidden />
            <span>{heading}</span>
          </Row>
          <ActionButtons actions={success?.actions ?? []} busyId={busyId} onRun={(a) => void runAction(a)} />
          {errorBlock}
        </Stack>
      </Card>
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
  const pickup = isTaskProposal ? pickupFor(pickupMap, (values.scope || "workshop").trim()) : null;
  const pickupWarning = pickup ? pickupNote(pickup) : null;
  // The actions this card offers: the model's own, minus any with no handler,
  // falling back to the kind's canonical set if it named none this build can
  // run, and without the run action where the scope cannot run (the same rule
  // TicketDraftsDialog applies when it hides "File and run now"). A known card
  // can no longer render a dead button or end up with no way to act.
  const editActions = resolveCardActions(spec.kind, spec.actions, {
    runnable: !pickup || pickup.runnable,
  });

  return (
    <Card>
      <Stack>
        <Row>
          {spec.kind === "prompt"
            ? <MessageCircleQuestion size={16} strokeWidth={1.8} aria-hidden />
            : <Sparkles size={16} strokeWidth={1.8} aria-hidden />}
          <span className="ws-card__title">{spec.title || "Card"}</span>
        </Row>
        <Stack>
          {fields.map((f) => (
            <CardField key={f.name} field={f} value={values[f.name] ?? ""} onChange={(v) => setValue(f.name, v)} />
          ))}
        </Stack>
        {notReady && <Notice tone="warn">{notReady}</Notice>}
        {pickupWarning && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{pickupWarning}</span>
        )}
        {errorBlock}
        <ActionButtons actions={editActions} busyId={busyId} onRun={(a) => void runAction(a)} />
      </Stack>
    </Card>
  );
}
