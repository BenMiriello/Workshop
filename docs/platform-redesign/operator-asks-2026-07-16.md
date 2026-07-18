# Operator asks and ideas, verbatim + indexed (2026-07-16)

> **STATUS: this doc is the RECORD of the ask, and is accurate. The work it indexes is
> PROPOSED, NOT BUILT.** The coverage audit in Part 4 means "a report addressed it",
> never "it was implemented". Nothing was implemented.

Source of truth for the 2026-07-16 thread on notes interface contracts, generalized
resource-access protocols, primitive agent roles, and work scopedness.

Why this doc exists: the reports distill and re-frame these asks, and distillation
loses things. This is the unfiltered record. When a report and this doc disagree about
what was asked, THIS doc wins. Ideas here that no report picked up are not rejected,
they are unaddressed, and that distinction should stay visible.

Companion docs (this thread):
- `_brief-agent-protocols-2026-07-16.md` (stage-setting brief given to the agents)
- `resource-access-protocol-2026-07-16.md` (hard gate + LLM-mediated access)
- `agent-roles-research-2026-07-16.md` (primitive roles, templates, conductors)
- `scopedness-and-escalation-2026-07-16.md` (work scopedness, blocked loop)
- plus a synthesis/integration report

---

## Part 1: the ask, verbatim

Operator message, 2026-07-16, reproduced exactly as written (including its own
self-corrections, which carry signal about what was meant):

> Notes: I want to make the notes app have interface contract with outside agents.
> Have a mix of hard gate api scoping and ai interface scoping. So like you can have
> different folders with different permission and access protocols and rules. 0 or
> multiple agents within notes that can be on call or autonomous, write only, read
> only, other. Other apps or agents can request notes by search or name or other, and
> in some configurations notes or note folders should have mixed permission as an
> option meaning no access unless an llm can review the request and compare against
> the strict allowlist of topics or scope. Must be a smart llm. If none available no
> access. It could either report verbatim or filter out only the relevant info or
> other options or redact or other. So agents or apps can communicate and share this
> way. We can figure out what parts of this protocol make sense as general workshop
> protocols or not. Since the notes app is a custom one we don't want to require
> people to use. So let's build these protocols to be generalized as possible. Need a
> way to have an agent or job flag if blocked by permission or anything and let
> operator review and change access or approve request or update rules - literal
> programmatic rules or textual rules - based on that request.
>
> And autonomous jobs and background worker agents - perhaps want a way to make the
> job or project be more strict or not - better phrased I mean a task or ticket may be
> well scoped or not. Like we want skills and rules to be sure they're well scoped.
> But some projects you may just want to specify the requirements and goals and let it
> figure things out. Other times you want to be more hands on. Some settings on a
> project level (and agent or job level? Inheriting from project?) should manage this.
>
> And you should be able to leave notes in general or in a project. And have it just
> be picked up. And there can be simple basic general worker autonomous job template
> agents that can be configured or also edited for a project and there can be a default
> set or templates including different groups of these and other settings. And one can
> just review new or updated notes, denounced, and ensure all notes are addressed and
> make tickets or escalate to other agents. Like ants having little jobs. And what are
> the other primitive roles or agents we can have? Let's research - deep research
> examples and research and ideas for this and distill it to a core set and a system
> for interaction. … then there's how conductor overseer agents interact and different
> scopes etc and much more to consider and integrate all this with the broader design.
> So follow these multiple threads, explore, research, document, think and ideate and
> propose a few reports for notes and the general agents idea and propose how to do
> this (not just what I proposed but the rest this implies and the rest needed from
> existing plans and how to integrate this into the system completely with full
> functionality and ui and condition handling. Go. And use fable subagent to do the
> thinking and reporting once you set the stage.

Follow-up, same session:

> document my asks and ideas too if not done already

---

## Part 2: indexed asks

Each ask gets a stable ID so reports can cite it and so we can audit coverage.

### Thread A: Notes interface contract and resource access

- **A1.** The notes app has an **interface contract with outside agents**.
- **A2.** A **mix** of hard gate API scoping AND AI interface scoping. Both, not either.
- **A3.** **Different folders** carry different permission and access protocols and
  rules. Per-folder granularity is explicit, not per-app.
- **A4.** **0 or multiple agents within notes.** Zero is a named valid case. Modes
  given: on call, autonomous, write only, read only, "other".
- **A5.** Other apps or agents **request notes by search or name or other**. Request
  is by query, not just by direct address.
- **A6.** **Mixed permission** as a per-config option: no access unless an LLM reviews
  the request and compares it against a **strict allowlist of topics or scope**.
- **A7.** **Must be a smart LLM. If none available, no access.** Fail-closed, with an
  explicit model-capability floor.
- **A8.** Response modes: **report verbatim / filter out only the relevant info /
  redact / "other options"**. The set is open-ended by intent.
- **A9.** Purpose framing: **"so agents or apps can communicate and share this way."**
  This is a communication substrate, not only a gate. Read A9 as the goal and A6/A7 as
  the constraint. A design that gates well but shares badly has failed the ask.
- **A10.** **"We can figure out what parts of this protocol make sense as general
  workshop protocols or not."** The general-vs-app split is itself a deliverable, and
  is explicitly delegated rather than pre-decided.
- **A11.** **"Since the notes app is a custom one we don't want to require people to
  use."** Notes must remain optional. The protocol must not assume it.
- **A12.** **"Build these protocols to be generalized as possible."**

### Thread B: blocked flagging and operator review

- **B1.** An **agent or job** can flag if **blocked by permission or anything**. Note
  the scope: "or anything" is broader than permissions alone.
- **B2.** The **operator reviews** and can: change access, approve the request, or
  update rules.
- **B3.** Rules are of two kinds: **literal programmatic rules OR textual rules**.
- **B4.** Rule updates are made **"based on that request"**, i.e. the block itself is
  the input to the rule change, not a separate authoring flow.

### Thread C: work scopedness

- **C1.** For autonomous jobs and background worker agents, a way to make **the job or
  project more strict or not**. Operator's own re-phrase: **"a task or ticket may be
  well scoped or not."**
- **C2.** **Skills and rules** are the mechanism "to be sure they're well scoped."
- **C3.** Two named modes: some projects, **specify requirements and goals and let it
  figure things out**; other times, **be more hands on**.
- **C4.** Settings at **project level**, with an open question the operator raised
  themselves: **"(and agent or job level? Inheriting from project?)"**. Inheritance is
  proposed, not decided.

### Thread D: notes get picked up, and worker templates

- **D1.** Leave notes **in general or in a project** and **have it just be picked up**.
  Emphasis on effortlessness: writing the note is the whole interaction.
- **D2.** **Simple basic general worker autonomous job-template agents.**
- **D3.** Templates are **configurable and also editable for a project**.
- **D4.** A **default set**, plus **templates including different groups of these**
  and other settings. Template GROUPS are explicit.
- **D5.** A concrete example worker: **reviews new or updated notes, ensures all notes
  are addressed, makes tickets, or escalates to other agents.**
- **D6.** Framing metaphor: **"Like ants having little jobs."**

### Thread E: roles, conductors, integration

- **E1.** **"What are the other primitive roles or agents we can have?"**
- **E2.** **Deep research** examples, research, and ideas.
- **E3.** **Distill to a core set** and **a system for interaction**. Both halves.
- **E4.** **How conductor/overseer agents interact**, and **different scopes**.
- **E5.** **"Much more to consider and integrate all this with the broader design."**

### Thread F: meta-instructions on the work itself

- **F1.** Follow **multiple threads**: explore, research, document, think, ideate.
- **F2.** Propose **a few reports** (plural), for notes AND the general agents idea.
- **F3.** Propose **not just what I proposed but the rest this implies**, plus **the
  rest needed from existing plans**.
- **F4.** **Integrate into the system completely**, with **full functionality, UI, and
  condition handling.**
- **F5.** Use the **fable subagent** for the thinking and reporting, once the stage is
  set.
- **F6.** Document the operator's asks and ideas (this doc).

---

## Part 3: operator ideas worth preserving that are not requirements

Things the operator floated that read as thinking-out-loud rather than specification.
Recorded so a report can adopt or reject them deliberately instead of silently.

- **"Like ants having little jobs"** is a metaphor offered, not a mandate to build a
  swarm. It signals a preference for many small, cheap, single-purpose workers over
  few large general ones. A report is free to conclude the ant model does not transfer
  to a handful of expensive agents, but must say so rather than quietly ignore it.
- **"or other" / "other options"** appears four times (A4, A5, A8, and the response
  modes). The operator is deliberately leaving the enumerations open. Treat the named
  items as the seed set, and treat extensibility of the set as a design requirement.
- **"perhaps want a way to make the job or project be more strict or not - better
  phrased I mean..."** The operator explicitly flagged that they had not found the
  right name for the axis. Naming it well is part of the deliverable.
- **"denounced"** in "review new or updated notes, denounced, and ensure all notes are
  addressed" appears to be a dictation artifact. Most probable intents: denoised, or
  de-duped, or a stray word. Not treated as a requirement. Flag to the operator if it
  turns out to matter.
- **"0 or multiple agents"** (A4) starting the count at zero is a real design signal:
  the resource must be fully functional with no resident agent. Agents are an optional
  layer over the contract, not the contract itself.
- **"We can figure out what parts of this protocol make sense as general workshop
  protocols or not"** (A10) grants latitude to conclude that some of the proposal
  should NOT be generalized. Latitude granted is not latitude that must be used, but
  the reports should use it where honest.

---

## Part 4: coverage audit

COMPLETED 2026-07-16. All four reports landed. Every ask ID is claimed by a named
report. Nothing was silently dropped; the deliberate rejections are listed below and in
`synthesis-agent-protocols-2026-07-16.md` Part 6.

| Thread | Asks | Owning report | Status |
| --- | --- | --- | --- |
| A (notes contract, access protocol) | A1-A12 | `resource-access-protocol-2026-07-16.md` | addressed |
| B (blocked, operator review, rules) | B1-B4 | `scopedness-and-escalation-2026-07-16.md` | addressed |
| C (work scopedness, inheritance) | C1-C4 | `scopedness-and-escalation-2026-07-16.md` | addressed |
| D (notes picked up, templates) | D1-D6 | `agent-roles-research-2026-07-16.md` | addressed |
| E (roles, conductors, integration) | E1-E5 | `agent-roles-research-2026-07-16.md` + synthesis | addressed |
| F (meta: reports, integration, UI) | F1-F6 | `synthesis-agent-protocols-2026-07-16.md` | addressed |

### Asks answered by rejecting the operator's framing (deliberate, not oversight)

- **D6 (the ant metaphor)**: rejected as a build model, one mechanism kept (stigmergy
  proper: coordinate through shared state, never agent-to-agent messaging). Reason:
  response-threshold models are population-scale statistics assuming thousands of free
  agents and no operator. Recorded per Part 3's rule that a report may reject the
  metaphor but must say so out loud.
- **E4 (conductor/overseer agents)**: there is no conductor role. The conductor is
  roughly 20 lines of Python; the overseer function is a `triager` at workshop scope.
  Consequence: `agents/workshop-overseer.md` is recommended for retirement.
- **A4 (0 or multiple resident agents)**: answered as a concept *reduction*. All four
  named modes decompose into (trigger mode) x (verb set), both of which already exist.
  Zero resident agents then falls out free.
- **A8 (response modes)**: `summarized` recommended NOT to ship in the seed set. It is
  the only proposed mode with no possible mechanical verifier. The set stays extensible
  per A8's own "or other".

### Asks that turned out to be blocked by live bugs rather than by missing design

- **D5 (the note-triage ant)**: cannot exist today. `scripts/dispatch_triggers.sh:329`
  branches on `role == "memory"` and skips every other assistant agent. The ant would
  register, appear in the roster, and never run.
- **C2 (skills and rules ensure well-scoped work)**: `touches` (`tasks.py:32`,
  documented as "fan-out safety") is enforced nowhere. The advisory-vs-enforced gap was
  already live in the tree.
- **A3 (per-folder permission protocols)**: unsatisfiable on the `~/notes` vault, which
  is not registered as a Store at all, and is the only resource with real folders.
- **B4 (mint rules from blocks)**: gated on the project-learnings `global` fallback
  (F3 in `review-findings-2026-07-15.md`, confirmed HIGH), or every project rule leaks
  into every agent.

### The dictation artifact

**"denounced"** is read by the roles report as "de-duped" and reported satisfied by the
`link` behavior either way. If it meant something else, this is the one cheap re-read.

---

## Appendix: session incident (2026-07-16)

The first dispatch of the three research agents was killed mid-flight when the disk hit
100% (4.7G free of 1.8T at recovery). No reports were produced by that dispatch and no
work was lost beyond tokens: the brief had already landed. The cause was not the agent
transcripts (`~/.claude` totals 348M) but pre-existing disk pressure elsewhere on the
volume. Recorded here only so a future reader does not re-diagnose the transcript store
as the culprit.
