# Chat effort and mode: design plan

Status: **built**, 2026-08-09. P1 (effort) shipped in `1584843`; the approval
channel and the mode picker followed in the same session. P4's leftovers — the
operator-pref tier in §4/§6, the per-agent frontmatter in §7, and plan mode
reverting to the PREVIOUS mode rather than a fixed one — landed 2026-08-10.
What was built differs from this plan in three places, recorded in §11 rather
than quietly edited into the sections above.

Adds two per-chat controls under the composer, to the left of the model picker:
an **effort** ladder and a **mode** selector. Both are vendor-declared, not
hardcoded in the frontend. The hard part is not the pickers, it is that some
modes require a live approval channel back into a running turn.

Everything under "Verified" below was measured against the installed CLI
(`claude 2.1.143`) on 2026-08-09, not read from docs.

---

## 1. What the vendor actually offers

### Verified against the installed CLI

```
--effort <level>            low | medium | high | xhigh | max
--permission-mode <mode>    default | acceptEdits | plan | auto | dontAsk | bypassPermissions
--permission-prompt-tool    (undocumented in --help, works)
```

Four findings that shape the design:

1. **`--effort` is validated against one fixed list for every model.** Haiku 4.5
   accepted `max` without complaint. The CLI does no per-model gating, so
   which levels are *meaningful* for a given model is our table to own, not
   something we can read off the vendor.

2. **A non-bypass permission mode without a permission channel silently
   auto-denies.** With `--permission-mode default` and no prompt tool, a Write
   came back as `"Claude requested permissions to write to ..., but you haven't
   granted it yet."`, the turn ended `success`, and the file was never created.
   The result event carries a `permission_denials` array. This is the single
   most dangerous failure mode in this feature: a naive implementation that just
   forwards the flag produces a chat that looks like it is working and quietly
   refuses every write.

3. **`--permission-prompt-tool stdio` turns on a real approval round-trip** over
   the existing stream-json stdio control protocol. Verified payload:

   ```json
   {"type":"control_request","request_id":"bea4c4a4-...",
    "request":{"subtype":"can_use_tool","tool_name":"Write","display_name":"Write",
      "input":{"file_path":"...","content":"hi"},
      "description":"hello.txt",
      "permission_suggestions":[{"type":"setMode","mode":"acceptEdits","destination":"session"}],
      "tool_use_id":"toolu_018s..."}}
   ```

   answered on stdin with:

   ```json
   {"type":"control_response","response":{"subtype":"success","request_id":"bea4c4a4-...",
     "response":{"behavior":"allow","updatedInput":{...}}}}
   ```

   `{"behavior":"deny","message":"..."}` also works and the model reads the
   message. The verified working combination is: `--input-format stream-json`,
   an `initialize` control_request sent first, and `--permission-prompt-tool
   stdio`. I did not isolate whether the `initialize` handshake is strictly
   required; send it, since that is what was measured.

4. **Plan mode is enforced by the CLI, not by prompt text.** In `plan`, a direct
   "create this file" instruction was refused. Note that the model does not
   always call `ExitPlanMode`: in one probe it simply wrote the plan as prose and
   ended the turn. The transcript must read correctly with no approval card.

### From the vendor docs

Permission modes ([Claude Code docs](https://code.claude.com/docs/en/permission-modes)):

| value | UI label | runs without asking |
|---|---|---|
| `default` | Manual | reads only |
| `acceptEdits` | Edit automatically | reads, file edits, common fs commands |
| `plan` | Plan | reads, plus classifier-approved commands |
| `auto` | Auto | everything, with background safety checks |
| `dontAsk` | (skip) | only pre-approved tools; auto-denies the rest |
| `bypassPermissions` | Bypass | everything |

`default` is labeled **Manual** everywhere in the vendor's own UI and the CLI
accepts `manual` as an alias. `auto` is account-gated and can be disabled by
managed settings. `dontAsk` is a CI posture, not a chat posture: leave it out of
the picker.

Effort, on the model side: Opus 5 exposes `output_config.effort` with `low,
medium, high, xhigh, max`, defaulting to `high`, over adaptive thinking that is
on by default ([Opus 5 notes](https://platform.claude.com/docs/en/about-claude/models/whats-new-opus-5)).
OpenAI's equivalent is `reasoning_effort` with a model-dependent set drawn from
`none, minimal, low, medium, high, xhigh, max`
([OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)).
The two vendors overlap but are not identical, which is exactly why the level
list has to travel with the model rather than live in the frontend.

Prior art for the surface: Conductor ships a compact model picker with inline
effort and mode controls, and separates Plan mode ("make a plan before editing")
from Fast mode ([Conductor agent modes](https://www.conductor.build/docs/concepts/agent-modes)).

---

## 2. Capability descriptor

One shape, served with the provider list, consumed blindly by the UI.

```ts
type TurnCaps = {
  vendor: string;                    // "anthropic-claude-code"
  efforts: { levels: string[]; default: string | null };   // [] = no control
  modes:   { options: string[]; default: string };
};
```

Backend: a new `backend/services/model_caps.py` holding `CAPS_BY_MODEL`, keyed
by full model id with a vendor-family fallback. Served by extending
`_provider_entry()` (`backend/services/chat_context.py:1547`) with
`model_caps: {model_id: TurnCaps}`, so `GET /api/chat/providers` stays the one
fetch the composer already makes.

Table, from the vendor's
[effort docs](https://platform.claude.com/docs/en/build-with-claude/effort)
(supported-model list and per-level availability), not from probing:

| model | effort levels | default |
|---|---|---|
| `claude-opus-5` | low, medium, high, xhigh, max | high |
| `claude-sonnet-5` | low, medium, high, xhigh, max | high |
| `claude-fable-5` | low, medium, high, xhigh, max | high |
| `claude-opus-4-8` | low, medium, high, xhigh, max | high |
| `claude-sonnet-4-6` | low, medium, high, max | high |
| `claude-haiku-4-5-*` | none, control hidden | n/a |

Two things to note. **`claude-sonnet-4-6` supports `max` but not `xhigh`**, so
its ladder is four rungs, not five: the level list is not a contiguous prefix of
one global ladder, which is why the UI must size itself from the list it is
handed rather than from a fixed count. And **Haiku 4.5 is absent from the
vendor's supported-model list**, so it gets no control even though the CLI
accepts the flag for it.

Permission modes are a CLI-level concept and do not vary by model. They belong
to the vendor entry, not the per-model entry; every Claude Code model gets the
same mode list.

The earlier draft of this plan listed the 4.x row as unverified pending a
measurement. The measurement was inconclusive for the wrong reason (the probe
prompt was small enough that effort had no room to move the output), and the
docs answer it directly: 4.x support effort, Haiku does not.

**Frontend renders exactly what the payload lists.** Labels come from one lookup
module per axis (`effort.ts`, `permissionModes.ts`) beside the existing
`models.ts` and `chatModes.ts`, with an unknown id falling back to its raw
value rather than disappearing. No frontend enum of vendor values.

---

## 3. Naming

The chat record already has `mode` and it means direct/orchestrated. That field
is untouched. The new field is **`permission_mode`**.

There is no label collision in the UI: the direct/orchestrated control already
presents as **Tickets** (`frontend/src/components/chat/chatModes.ts`), so the
word "Mode" is free for the composer control. Use the vendor's own labels
(Manual / Plan / Edit automatically / Auto / Bypass) so that what the operator
reads here matches what they read in the terminal.

---

## 4. Persistence and resolution

Both values live on the thread and are overridable per turn, following the
existing `model` pattern exactly.

- `chat_store.create_thread` (`backend/services/chat_store.py:324`) gains
  `effort` and `permission_mode`. No migration: `_document()` back-fills and
  `patch_meta_sync` has no field whitelist.
- Setters beside `set_thread_model` (`chat_store.py:544`).
- `StartTurnIn` (`backend/routes/chat.py:1488`) gains optional `effort` and
  `permission_mode`; when present they are persisted onto the thread the same
  way the model is at `routes/chat.py:1719`.
- Operator defaults join `prefs.json` next to `default_mode`:
  `default_effort`, `default_permission_mode`, `allow_bypass` (see §6).

Resolution order per turn: turn body, then thread, then operator pref, then the
model's declared default. An unsupported value for the selected model is
dropped, not passed through, and the UI re-picks the nearest supported level
when the model changes.

**Plan is a one-turn mode.** Selecting Plan applies to the current turn and
reverts to the previous mode once the plan is approved, matching the vendor's
desktop behavior and the `setMode` permission suggestion. Every other mode is
sticky on the thread.

---

## 5. Backend threading

`build_streaming.start_turn()` (`backend/services/build_streaming.py:693`) gains
`effort` and `permission_mode`, both carried in the durable per-turn `meta`
dict (`:734`) so a resumed turn keeps its posture.

At the argv site (`build_streaming.py:754-786`):

- append `--effort <level>` when the resolved caps list it;
- replace the hardcoded `--permission-mode bypassPermissions` (`:781`) with the
  resolved mode;
- append `--permission-prompt-tool stdio` whenever the resolved mode is not
  `bypassPermissions`.

`llm_router.tool_flags()` (`backend/services/llm_router.py:435-484`) also forces
`bypassPermissions` at `:472`. Give it a `permission_mode` parameter defaulting
to `bypassPermissions`, so the voice worker, job runners and mention turns keep
today's behavior untouched and only the chat path opts in.

### The approval channel

Today the FIFO into the CLI carries the prompt and closes. It becomes a
persistent writer for the life of the turn, and the stdout reader gains a branch
for `type == "control_request"`.

1. On turn start, write the `initialize` control_request, then the prompt.
2. On `can_use_tool`, persist the request against the turn (`request_id`,
   `tool_name`, `display_name`, `description`, `input`, `permission_suggestions`,
   `tool_use_id`) and append an `approval` block to the chat log.
3. New route `POST /api/chat/threads/{id}/turns/{turn_id}/permission` with
   `{request_id, behavior: "allow"|"deny", updated_input?, message?, apply_suggestion?}`
   writes the `control_response` frame into the FIFO and marks the block
   resolved. Answering the same `request_id` twice is a no-op, not an error.
4. Staleness is mandatory, because `CHAT_TIMEOUT_S = 0` means a blocked turn
   waits forever. Mark every pending approval `stale` when the turn process
   exits, when the turn is interrupted, and on backend restart (the FIFO writer
   does not survive it). Interrupting a turn sends `deny` first so the CLI is
   not left blocked on a request nobody will answer.

---

## 6. UI

### Composer row

Insertion point is `frontend/src/components/chat/ChatComposer.tsx:136`, directly
after the flex spacer at `:132`, giving:

```
[effort]  [mode]  [model]  [voice]  [slash]  [send]
```

Both new controls follow the row's house rules: `Menu` + `MenuItem checked`
rather than `<select>`, `side="top" align="end"`, the `.chat-model` visual idiom
(transparent, muted, 2.1rem tall, `--card-hover` on hover), and
`onMouseDown preventDefault` so tapping never blurs the textarea
(`ChatComposer.tsx:18-22`). No borders, per the design system.

**EffortMeter.** A row of N bars where N is the number of levels the model
declares, filled up to the selected index. Five bars for a 5-series model, three
for a vendor offering three: "filled up to your level" then means the same thing
everywhere. Filled bars use `--accent`, empty use `--border`; no hex. The
trigger is a button (`aria-label="Effort: high"`) that opens a drop-up listing
each level with a one-line gloss ("Low, fast and shallow" through "Max, deepest
and slowest"). Left/Right arrows adjust while the trigger is focused. When the
model declares no levels the control **renders nothing**, matching how the model
picker renders nothing at zero options rather than showing a dead disabled
control.

**Mode selector.** Lucide icon plus short label, with the full description in
the menu row. Bypass renders as a `danger`-toned item. Below `--bp-sm` the
effort control drops to bars only and mode drops to icon only, both keeping
44px tap targets.

Bypass appears in the menu only when `allow_bypass` is set in prefs, mirroring
the vendor's own desktop gate. That is one settings toggle, not a per-use
confirmation dialog, and it is the only gating this feature adds.

### The approval block in the transcript

The operator's question was whether to reuse the ui-card system. **Reuse the
renderer, not the channel.**

ui-cards are model-authored fences parsed out of message text
(`splitUiCards`, `UiCard.tsx:65`) against a fixed `KNOWN_KINDS` registry. A
permission request is not authored by the model: it is a runtime event carrying
a `request_id` that must be answered exactly once, that blocks a live process,
and that goes stale when that process dies. Routing it through message-text
parsing would make de-duplication, staleness and single-shot answering all
accidental properties of string handling.

So:

- a new chat-log block `kind: "approval"` carrying the payload plus a state of
  `pending | allowed | denied | stale`;
- a new `ApprovalBlock` component built from the same primitives, sharing
  `CardField` and `ActionButtons` with `UiCard` by lifting those two into a
  shared `cardKit` module. That is the generalization worth doing, and it is
  small: the presentational pieces move, the fence parser and the action
  registry stay where they are;
- actions: **Allow**, **Allow and stop asking** (which is just the
  `permission_suggestions` `setMode` passed back), **Deny** with an optional
  reason in a `Textarea`;
- for `ExitPlanMode`, render the plan from the request input as `Markdown` with
  **Approve** and **Keep planning** (deny plus reason);
- it renders inline in the log, under the message that caused it. Not in
  `InboxStrip`: that strip is for things pointing out of the chat, and a
  permission request is the most in-chat thing there is.

---

## 7. Agents

An @mentioned agent already runs its own turn with narrowed tools
(`routes/chat.py:1538`). Add `effort` and `permission_mode` to agent frontmatter
(`backend/services/agents_registry.py:331`); when an agent speaks, its own
values win over the chat's, since a reviewer should be able to be careful in a
chat that is otherwise on Auto.

Leave the existing `depth` field (`quick|standard|deep`) alone. It is a dispatch
hint that no turn path reads, and collapsing it into effort would silently
change sweep behavior. Note it as a cleanup candidate, not part of this work.

The CLI's `--agent` flag is out of scope.

---

## 8. Phases

Each phase is independently shippable and P1 does not depend on P2.

- **P1, effort.** Caps table and endpoint, thread fields, `--effort` in argv,
  `EffortMeter`. No approval channel, so mode stays pinned at today's
  `bypassPermissions`.
- **P2, approval channel.** Persistent stdin, initialize handshake,
  `--permission-prompt-tool stdio`, control_request routing, approval block and
  route, staleness on exit/interrupt/restart. Ships behind the existing
  bypass default: nothing changes until P3 lets a mode be picked.
- **P3, mode picker.** Selector UI, plan-as-one-turn, `setMode` passthrough,
  per-agent overrides.
- **P4, polish.** Operator defaults, bypass opt-in, mobile collapse.

## 9. Risks

1. Passing a mode without the prompt tool silently denies every write. Verified.
   The argv builder must couple the two, and a smoke test must assert it.
2. A blocked turn holds a chat forever, because `CHAT_TIMEOUT_S = 0`. Staleness
   handling is not optional.
3. `--permission-prompt-tool` is absent from `claude --help` in 2.1.143. It
   works, but it is an unadvertised surface. Pin it with a smoke test that fails
   loudly on CLI upgrade rather than degrading quietly.
4. The CLI accepts every effort level for every model, including models the API
   does not support effort on. Our caps table is the only thing standing between
   the operator and a control that does nothing, and it has to be kept current
   against the vendor's supported-model list as models are added.
5. `auto` is account-gated and can be turned off by managed settings. Treat its
   absence as normal and let the caps payload omit it.

## 11. What was built differently, and why

Three deviations from the plan above. Each was a decision made against the real
code, not a shortcut.

**P2 and P3 shipped together, not in sequence.** The plan phased the approval
channel ahead of the mode picker. A channel with no selectable mode cannot be
exercised end to end: in `bypassPermissions` nothing ever asks, so every line of
it would have been untested until the next phase. They went in together so the
path could be verified as a path.

**There is no `allow_bypass` opt-in toggle, and Bypass is not in the menu at
all.** This landed in two steps on 2026-08-09 and the second one reversed the
first, so both are recorded here.

The first decision was to drop the gate the plan proposed (§6: "Bypass appears in
the menu only when `allow_bypass` is set in prefs"). Bypass was what every chat in
this system had always run at, so a preference in front of it would not have been
protecting the operator from a new capability; it would have been a confirmation
prompt in front of the status quo, on a single-operator system whose own
conventions forbid exactly that (`CLAUDE.md`, "no defensive cruft for single-user
setups"). Bypass shipped in the menu, in the danger tone, as the default.

The operator then instructed that this system should not offer Bypass at all —
it is `--dangerously-skip-permissions`, and the approval channel that shipped in
the same session is precisely what makes a mode that can stop and ask usable in
a chat. So:

- `bypassPermissions` was removed from the picker (`model_caps.OFFERED_MODES`).
  It stays in `PERMISSION_MODES` as a valid VALUE, because the unattended paths
  pass it explicitly — job runners, sweeps and the voice worker have no operator
  to ask, and for them a mode that can stop and ask is not "safer", it is a turn
  that auto-denies everything and reports success. A chat that had chosen Bypass
  before it was withdrawn still loads and still renders under its own name
  (`permissionModes.ts` keeps the entry the picker no longer lists).
- The default is now **`auto`** (`model_caps.DEFAULT_PERMISSION_MODE`):
  everything, with background safety checks. Measured the same day with the
  approval channel live, `auto` approved a read-only `ls` by classifier and
  asked once about a file write.

With no Bypass to gate, `allow_bypass` has nothing left to do, and it was never
built. The prefs file carries `default_mode`, `default_effort` and
`default_permission_mode` — the last two being the operator-pref tier §4 asks
for, added 2026-08-10 — and no bypass flag.

**No shared `cardKit` was extracted.** The plan proposed lifting `CardField` and
`ActionButtons` out of `UiCard.tsx` for the approval block to reuse. On reading
them: `CardField` is a switch over ui-card field types (`template`, `taskscope`,
`sensitivity`) that an approval never uses, and `ActionButtons` is bound to the
ui-card action registry, which an approval must not go through. Extracting them
would have shared nothing. `ApprovalBlock` builds on the `src/ui` primitives
directly, which is what the design system asks for anyway. The plan's *reasoning*
about the ui-card channel still holds and is why the approval is a chat-log
segment rather than a fenced block in message text.

## 10. Out of scope

- The `mode` field (direct/orchestrated) and its semantics.
- Job and ticket workers' permission posture: they stay unattended on
  `bypassPermissions`.
- A per-tool allow-rule editor.
- `dontAsk` mode.
- Any confirmation prompt beyond the vendor's own.
