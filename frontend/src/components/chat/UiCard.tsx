// UiCard — the interactive in-chat UI primitive.
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
import { CircleCheck, MinusCircle, Sparkles } from "lucide-react";
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
  StateBlock,
  Textarea,
  type SegmentedOption,
} from "../../ui";
import {
  KNOWN_KINDS,
  UI_CARD_ACTIONS,
  hasCardHandler,
  slugify,
  type UiCardAction,
  type UiCardCtx,
  type UiCardField,
  type UiCardSpec,
} from "./uiCardActions";

// ---- Extraction ------------------------------------------------------------
// A parsed answer is an ordered list of markdown runs and ui-card blocks. A
// card whose JSON fails to parse keeps `spec: null` and its `raw` body so the
// renderer can show an inert fallback without losing the original content.
export type AnswerPart =
  | { t: "md"; text: string }
  | { t: "card"; spec: UiCardSpec | null; raw: string };

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
  if (last < text.length) parts.push({ t: "md", text: text.slice(last) });
  if (parts.length === 0) parts.push({ t: "md", text });
  return parts;
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
        <StateBlock
          variant="error"
          title="Couldn't load templates."
          hint={
            <Stack gap="sm">
              <span className="ws-mono">GET /api/projects/templates</span>
              <Row>
                <Button size="sm" variant="ghost" onClick={() => setReloadKey((k) => k + 1)}>Retry</Button>
              </Row>
            </Stack>
          }
        />
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
    <Field label={label}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {templates.map((t) => (
          <option key={t.slug} value={t.slug}>
            {t.label}{t.hint ? ` — ${t.hint}` : ""}
          </option>
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
  switch (field.type) {
    case "textarea":
      return (
        <Field label={label}>
          <Textarea value={value} rows={6} onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
    case "slug":
      return (
        <Field label={label} help="kebab-case · used as the directory name + route segment">
          <Input value={value} spellCheck={false} onChange={(e) => onChange(slugify(e.target.value))} />
        </Field>
      );
    case "template":
      return <TemplateField field={field} value={value} onChange={onChange} />;
    case "sensitivity":
      return (
        <Field label={label}>
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
        <Field label={label}>
          <Select value={value} onChange={(e) => onChange(e.target.value)}>
            {(field.options || []).map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
        </Field>
      );
    default:
      return (
        <Field label={label}>
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
  requireHandler,
}: {
  actions: UiCardAction[];
  busyId: string | null;
  onRun: (a: UiCardAction) => void;
  requireHandler?: boolean;
}) {
  if (actions.length === 0) return null;
  return (
    <Row wrap>
      {actions.map((a) => {
        const live = hasCardHandler(a.id);
        return (
          <Button
            key={a.id}
            variant={a.variant ?? "default"}
            loading={busyId === a.id}
            disabled={busyId != null || (requireHandler ? !live : false)}
            onClick={() => onRun(a)}
          >
            {a.label}
          </Button>
        );
      })}
    </Row>
  );
}

export function UiCard({ spec, raw, ctx }: { spec: UiCardSpec | null; raw: string; ctx: UiCardCtx }) {
  const fields = useMemo(() => spec?.fields ?? [], [spec]);
  const [values, setValues] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {};
    for (const f of fields) seed[f.name] = f.value ?? "";
    return seed;
  });
  const [phase, setPhase] = useState<Phase>("edit");
  const [success, setSuccess] = useState<SuccessInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

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
        setSuccess({ slug: result.slug, title: result.title, note: result.note, actions: result.actions });
        setPhase("success");
      } else if (result.status === "dismiss") {
        setPhase("dismissed");
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
    <StateBlock variant="error" title="That action failed." hint={<code>{error}</code>} />
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
  return (
    <Card>
      <Stack>
        <Row>
          <Sparkles size={16} strokeWidth={1.8} aria-hidden />
          <span className="ws-card__title">{spec.title || "Card"}</span>
        </Row>
        <Stack>
          {fields.map((f) => (
            <CardField key={f.name} field={f} value={values[f.name] ?? ""} onChange={(v) => setValue(f.name, v)} />
          ))}
        </Stack>
        {errorBlock}
        <ActionButtons actions={spec.actions ?? []} busyId={busyId} onRun={(a) => void runAction(a)} requireHandler />
      </Stack>
    </Card>
  );
}
