// The two durable answers to a blocked ticket, in ONE place.
//
// These lived inside Work.tsx and were unreachable from the ticket detail page,
// which had bare buttons instead. That is why "Mint rule & release" on a ticket
// always 400'd: it posted `{action: "mint-rule"}` with no `rule`, and the
// backend hard-requires rule text (routes/tasks_all.py). A dialog is not
// decoration on this action; it is where the required field is typed.
import { useState } from "react";
import {
  Button, Checkbox, Field, Input, Modal, Row, Select, Stack, Textarea,
} from "@workshop/ui";
import { scopeLabel } from "../lib/tasks";

// Grant + continue: the answer to a block that named a resource and a verb.
// Prefilled from the block's own proposal, because the thing that stopped the
// job is the thing that becomes the rule that stops stopping it.
export function GrantDialog({
  proposal,
  count,
  onClose,
  onSubmit,
}: {
  proposal: { uri: string; verb: string };
  count: number;
  onClose: () => void;
  onSubmit: (fields: Record<string, unknown>) => void;
}) {
  const [uri, setUri] = useState(proposal.uri);
  const [verb, setVerb] = useState(proposal.verb);
  const [subtree, setSubtree] = useState(false);
  const [ttl, setTtl] = useState("");

  return (
    <Modal
      open
      onClose={onClose}
      title="Grant standing access"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!uri.trim()} onClick={() => onSubmit({
            store_uri: uri.trim(),
            verbs: [verb],
            subtree,
            ttl_hours: ttl.trim() ? Number(ttl) : null,
          })}>Grant and release</Button>
        </>
      }
    >
      <Stack gap="lg">
        <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
          Mints an enforced grant and releases {count === 1 ? "the ticket" : `all ${count} tickets`}. Checked mechanically on every store call from then on, with no model in the loop.
        </p>
        <Field label="Store URI" htmlFor="grant-uri">
          <Input id="grant-uri" value={uri} onChange={(e) => setUri(e.target.value)} data-autofocus />
        </Field>
        <Row wrap style={{ gap: "var(--space-4)", alignItems: "flex-end" }}>
          <Field label="Verb" htmlFor="grant-verb">
            <Select id="grant-verb" value={verb} onChange={(e) => setVerb(e.target.value)}>
              <option value="read">read</option>
              <option value="write">write</option>
              <option value="append">append</option>
            </Select>
          </Field>
          <Field label="Expires in" htmlFor="grant-ttl" help="Hours. Empty = standing.">
            <Input id="grant-ttl" type="number" value={ttl} onChange={(e) => setTtl(e.target.value)} style={{ maxWidth: "7rem" }} />
          </Field>
        </Row>
        <Checkbox checked={subtree} onChange={setSubtree}
                  label="Cover everything underneath this, not just this one thing" />
      </Stack>
    </Modal>
  );
}

// A grant minted from a promotion candidate, not from a block. Same fields, but
// there is no ticket to release, so the copy must not claim there is: the shared
// dialog used to say "releases the ticket" over a surface with no ticket at all.
export function PromoteGrantDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (fields: Record<string, unknown>) => void;
}) {
  const [uri, setUri] = useState("");
  const [verb, setVerb] = useState("write");
  const [subtree, setSubtree] = useState(false);
  const [ttl, setTtl] = useState("");

  return (
    <Modal
      open
      onClose={onClose}
      title="Make this rule enforced"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!uri.trim()} onClick={() => onSubmit({
            store_uri: uri.trim(),
            verbs: [verb],
            subtree,
            ttl_hours: ttl.trim() ? Number(ttl) : null,
          })}>Enforce it</Button>
        </>
      }
    >
      <Stack gap="lg">
        <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
          Turns prose that keeps being re-derived into a grant checked mechanically on every store call. The prose stays as the reason.
        </p>
        <Field label="Store URI" htmlFor="promote-uri" help="What the rule is actually about.">
          <Input id="promote-uri" value={uri} onChange={(e) => setUri(e.target.value)} data-autofocus />
        </Field>
        <Row wrap style={{ gap: "var(--space-4)", alignItems: "flex-end" }}>
          <Field label="Verb" htmlFor="promote-verb">
            <Select id="promote-verb" value={verb} onChange={(e) => setVerb(e.target.value)}>
              <option value="read">read</option>
              <option value="write">write</option>
              <option value="append">append</option>
            </Select>
          </Field>
          <Field label="Expires in" htmlFor="promote-ttl" help="Hours. Empty = standing.">
            <Input id="promote-ttl" type="number" value={ttl} onChange={(e) => setTtl(e.target.value)} style={{ maxWidth: "7rem" }} />
          </Field>
        </Row>
        <Checkbox checked={subtree} onChange={setSubtree}
                  label="Cover everything underneath this, not just this one thing" />
      </Stack>
    </Modal>
  );
}

// Mint rule: the durable answer to a block whose ask was a judgment. Scoped to
// the ticket's own scope by default. `global` is injected into every agent's
// context forever, so it stays a deliberate act, never a default.
export function RuleDialog({
  ask,
  scope,
  onClose,
  onSubmit,
}: {
  ask: string;
  scope: string;
  onClose: () => void;
  onSubmit: (fields: Record<string, unknown>) => void;
}) {
  const [rule, setRule] = useState("");
  const [ruleScope, setRuleScope] = useState("");

  return (
    <Modal
      open
      onClose={onClose}
      title="Mint a rule from this block"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!rule.trim()} onClick={() => onSubmit({
            rule: rule.trim(),
            rule_scope: ruleScope || null,
          })}>Record and release</Button>
        </>
      }
    >
      <Stack gap="lg">
        {ask && <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>Asked: {ask}</p>}
        <Field label="The standing answer" htmlFor="rule-text" help="Every future run in this scope reads this before it starts. It is guidance, not a hard limit.">
          <Textarea id="rule-text" rows={4} value={rule} onChange={(e) => setRule(e.target.value)}
                    placeholder="Prefer the existing UI primitive over a new one." data-autofocus />
        </Field>
        <Field label="Scope" htmlFor="rule-scope" help="Global rules are injected into every agent's context, forever.">
          <Select id="rule-scope" value={ruleScope} onChange={(e) => setRuleScope(e.target.value)}>
            <option value="">{scopeLabel(scope)} (this ticket's scope)</option>
            <option value="global">global (every scope, every run)</option>
          </Select>
        </Field>
      </Stack>
    </Modal>
  );
}
