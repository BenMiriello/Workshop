import { useCallback, useEffect, useMemo, useState } from "react";
import { Pencil, Plus, Trash2, Wrench } from "lucide-react";
import {
  ActionBar,
  Button,
  Card,
  Clamp,
  ConfirmDialog,
  Field,
  Input,
  Notice,
  PageHeader,
  Row,
  SectionLabel,
  Select,
  SettingsRow,
  Skeleton,
  Stack,
  StateBlock,
  Table,
  TableScroll,
  Tag,
  Textarea,
  catFor,
  shortHttpError,
  useEditMode,
} from "@workshop/ui";
import {
  BLANK_TOOL,
  NO_TOOL_FILTER,
  TOOL_KINDS,
  canSave,
  capRows,
  catalogSummary,
  createBody,
  draftFromTool,
  filterTools,
  provenanceLabel,
  toolFacet,
  updateBody,
  type ToolDraft,
  type ToolFilter,
  type ToolRecord,
} from "../../lib/tools";
import McpServers from "./McpServers";

// Settings -> Tools. Every capability this instance can name, who owns each,
// and CRUD for the ones the operator owns.
//
// WHAT THIS PAGE OWNS, and the McpServers question. That page is deliberately
// read-only because the switch it might have grown (per-chat on/off) already
// exists in the chat, and "does not grow a second, disagreeing switch". The
// same test, applied here, splits the other way for HALF the surface and the
// same way for the other half:
//
//   - The RECORD is owned here. `.runtime/tools.json` has no other editor, so a
//     form on this page is the first and only switch for a user tool. It writes.
//   - The GRANT is not, and there is no control for it anywhere on this screen.
//     `llm_router.tool_flags` is the one enforcement point, narrowed by role
//     (agent_authority) and by the persona ceiling. A per-tool "enabled" toggle
//     here would look like it granted something and would grant nothing, which
//     is the disagreeing switch in its worst form: one that lies about a
//     capability. Declaring a tool grants nothing (doc section 2.3), and the
//     page says so in its own words rather than implying otherwise with a
//     control.
//
// Platform and app tools carry no Edit button, because a dead control is worse
// than an absent one, and no disabled cursor, because the forbidden-cursor
// keyword check_design.sh bans says "broken" where the truth is "not yours".
// They say what is true instead: used, not edited.
//
// The pure rules (argv parsing, the write bodies, the counts, the row cap) are
// in ../../lib/tools.ts with tests. This file is the arrangement.

const ENDPOINT = "/api/tools";

type FormTarget = { mode: "create" } | { mode: "edit"; tool: ToolRecord };

async function failure(r: Response): Promise<string> {
  return shortHttpError(r.status, r.statusText, await r.text().catch(() => ""));
}

/** Create or edit a user tool. Mounted only while the form is open, so it
 *  enters edit mode on mount: there is no read-only state to sit in. */
function ToolForm({
  initial,
  isCreate,
  onSave,
  onCancel,
}: {
  initial: ToolDraft;
  isCreate: boolean;
  onSave: (draft: ToolDraft) => Promise<void>;
  onCancel: () => void;
}) {
  // A rejected save is a LINE beside the form that is still on screen, never a
  // region: the fields, and the id that was refused, stay right there to fix.
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const edit = useEditMode<ToolDraft>(initial, async (draft) => {
    setSaveErr(null);
    try {
      await onSave(draft);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
      throw e;
    }
  });

  const { begin } = edit;
  useEffect(() => { begin(); }, [begin]);

  return (
    <Stack>
      {isCreate && (
        <SettingsRow
          label="id"
          hint="The name an allowlist writes. It cannot be changed later, and it cannot be one a platform or app tool already holds."
        >
          <Input
            value={edit.draft.id}
            onChange={(e) => edit.set("id", e.target.value)}
            placeholder="preview"
            aria-label="Tool id"
            autoFocus
          />
        </SettingsRow>
      )}
      {!isCreate && (
        <SettingsRow label="id" hint="An id cannot change. Renaming a tool is deleting one and declaring another.">
          <span className="ws-mono">{edit.draft.id}</span>
        </SettingsRow>
      )}
      <SettingsRow label="title" hint="The human label, shown in this list.">
        <Input
          value={edit.draft.title}
          onChange={(e) => edit.set("title", e.target.value)}
          placeholder="Preview a page"
          aria-label="Tool title"
          autoFocus={!isCreate}
        />
      </SettingsRow>
      <SettingsRow label="description" hint="Read by a person here, and by a model deciding whether to reach for it.">
        <Textarea
          value={edit.draft.description}
          onChange={(e) => edit.set("description", e.target.value)}
          rows={3}
          aria-label="Tool description"
        />
      </SettingsRow>
      <SettingsRow
        label="kind"
        hint="command is a declared command line. builtin names a tool the harness already has. mcp names a tool on an MCP server."
      >
        <Select
          value={edit.draft.kind}
          onChange={(e) => edit.set("kind", e.target.value)}
          aria-label="Tool kind"
        >
          {TOOL_KINDS.map((k) => (
            <option key={k} value={k}>{k}</option>
          ))}
        </Select>
      </SettingsRow>
      {edit.draft.kind === "command" && (
        <SettingsRow
          label="command"
          hint="The argv, run in the turn's own sandbox. Quote an argument that contains a space."
        >
          <Input
            value={edit.draft.command}
            onChange={(e) => edit.set("command", e.target.value)}
            placeholder='scripts/shoot.sh "/settings/tools"'
            aria-label="Tool command line"
            className="ws-mono"
          />
        </SettingsRow>
      )}

      {saveErr && <Notice tone="error">{saveErr}</Notice>}

      <ActionBar flash={edit.flash ? "Saved" : undefined}>
        <Button variant="ghost" onClick={onCancel} disabled={edit.saving}>Cancel</Button>
        <Button
          variant="primary"
          loading={edit.saving}
          disabled={!canSave(edit.draft, isCreate, edit.dirty)}
          onClick={() => { void edit.save().catch(() => {}); }}
        >
          {isCreate ? "Create tool" : "Save"}
        </Button>
      </ActionBar>
    </Stack>
  );
}

export default function Tools() {
  const [tools, setTools] = useState<ToolRecord[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [form, setForm] = useState<FormTarget | null>(null);
  const [deleting, setDeleting] = useState<ToolRecord | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [filter, setFilter] = useState<ToolFilter>(NO_TOOL_FILTER);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadErr(null);
    try {
      const r = await fetch(ENDPOINT);
      if (!r.ok) throw new Error(await failure(r));
      setTools(((await r.json()) as { tools: ToolRecord[] }).tools);
    } catch (e) {
      // A failed read is not an empty catalog. Say which it is.
      setLoadErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const save = useCallback(async (draft: ToolDraft) => {
    const editing = form?.mode === "edit" ? form.tool : null;
    const r = await fetch(editing ? `${ENDPOINT}/${encodeURIComponent(editing.id)}` : ENDPOINT, {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(editing ? updateBody(draft, editing) : createBody(draft)),
    });
    // The registry's refusal is the message worth showing: a 409 names the
    // conflicting id and its provenance, which is the whole reason the write
    // was refused. Never replace it with "could not save".
    if (!r.ok) throw new Error(await failure(r));
    setForm(null);
    await load();
  }, [form, load]);

  const remove = useCallback(async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    setDeleteErr(null);
    try {
      const r = await fetch(`${ENDPOINT}/${encodeURIComponent(deleting.id)}`, { method: "DELETE" });
      if (!r.ok) {
        setDeleteErr(await failure(r));
        return;
      }
      setDeleting(null);
      await load();
    } catch (e) {
      setDeleteErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteBusy(false);
    }
  }, [deleting, load]);

  const initialDraft = useMemo(
    () => (form?.mode === "edit" ? draftFromTool(form.tool) : BLANK_TOOL),
    [form],
  );

  const all = tools ?? [];
  // The facets come off the rows, so a provenance or kind the API grows shows
  // up in the menus without an edit here.
  const kinds = toolFacet(all, "kind");
  const provenances = toolFacet(all, "provenance");
  // A FACET WITH ONE VALUE BEHIND IT IS AN INERT CONTROL: every choice returns
  // the whole catalog. Offered only where the rows actually differ, which is
  // the same question that decides whether owner is worth a hue below.
  const ownerVaries = provenances.length > 1;
  const kindVaries = kinds.length > 1;
  // Whether any row has an action, which is what the second column holds.
  const anyEditable = all.some((t) => t.editable);
  const matched = filterTools(all, filter);
  const shown = capRows(matched, expanded);
  const summary = catalogSummary(all);
  const filtering = filter.kind !== "all" || filter.provenance !== "all" || filter.q.trim() !== "";

  return (
    <div>
      {/* ONE PAGE FOR ONE QUESTION. Tools and MCP were two Settings rows and
          two answers to "what can this instance call"; the split described
          where each record lives, which is not something the operator is
          asking. Section 6 of threads-branches-and-pages-2026-08-29 collapses
          them, and the MCP surface renders below as a section of this page
          rather than as a page of its own. */}
      <PageHeader
        title="Tools and MCP"
        sub="Every capability this system can call, and who owns each one."
        actions={
          form === null ? (
            <Button size="sm" variant="ghost" onClick={() => setForm({ mode: "create" })}>
              <Plus size={16} strokeWidth={1.8} aria-hidden /> New tool
            </Button>
          ) : undefined
        }
      />

      <Stack>
        {form !== null && (
          <section>
            <SectionLabel>{form.mode === "create" ? "New tool" : `Editing ${form.tool.id}`}</SectionLabel>
            <Card>
              <ToolForm
                key={form.mode === "edit" ? form.tool.id : "new"}
                initial={initialDraft}
                isCreate={form.mode === "create"}
                onSave={save}
                onCancel={() => setForm(null)}
              />
            </Card>
          </section>
        )}

        <section>
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <SectionLabel icon={<Wrench size={14} strokeWidth={1.8} aria-hidden />}>Catalog</SectionLabel>
            {summary && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                {filtering ? `${matched.length} of ${all.length} \u00b7 ${summary}` : summary}
              </span>
            )}
          </Row>

          {/* SAID ONCE. It used to be a lock line on every row of a column that
              was the same on every row. It is one fact about the whole catalog
              while no tool here is yours, so it belongs beside the catalog and
              not beside each of its 21 members. */}
          {!loadErr && all.length > 0 && !anyEditable && (
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-1)" }}>
              Platform and app tools are used, not edited. Declare one with New tool and it gets Edit and Delete here.
            </p>
          )}

          {/* The axes the rows ACTUALLY differ on, plus free text: the same
              filter /stores already carries, on the page that most needs one.
              The catalog only grows, and 21 rows with no way to narrow them is
              a list you read once and then scroll past. A VIEW PREFERENCE, so
              every control commits on change (design system rule 7).

              Each select appears only while its field has more than one value
              in it. A menu whose every option returns the whole list is a
              control that does nothing, which is worse than an absent one. */}
          {!loadErr && all.length > 0 && (
            <Row wrap between style={{ marginBottom: "var(--space-3)", gap: "var(--space-2)" }}>
              <Row wrap style={{ gap: "var(--space-2)" }}>
                {kindVaries && (
                  <Field label="Kind">
                    <Select
                      value={filter.kind}
                      onChange={(e) => setFilter((f) => ({ ...f, kind: e.target.value }))}
                    >
                      <option value="all">All kinds</option>
                      {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
                    </Select>
                  </Field>
                )}
                {ownerVaries && (
                  <Field label="Owner">
                    <Select
                      value={filter.provenance}
                      onChange={(e) => setFilter((f) => ({ ...f, provenance: e.target.value }))}
                    >
                      <option value="all">All owners</option>
                      {provenances.map((p) => <option key={p} value={p}>{provenanceLabel(p)}</option>)}
                    </Select>
                  </Field>
                )}
              </Row>
              <Field>
                <Input
                  placeholder="Filter by name, id or description"
                  value={filter.q}
                  onChange={(e) => setFilter((f) => ({ ...f, q: e.target.value }))}
                />
              </Field>
            </Row>
          )}

          <Card>
            {/* loading */}
            {loading && !tools && (
              <Stack gap="sm">
                <Skeleton width="62%" /><Skeleton width="48%" /><Skeleton width="55%" />
              </Stack>
            )}

            {/* error */}
            {loadErr && (
              <StateBlock
                variant="error"
                title="Couldn't load tools"
                hint={`GET ${ENDPOINT} failed (${loadErr}).`}
                action={<Button size="sm" variant="ghost" onClick={() => void load()}>Try again</Button>}
              />
            )}

            {/* empty */}
            {!loadErr && tools && all.length === 0 && (
              <StateBlock
                title="No tools declared"
                hint="Every install ships platform tools, so an empty catalog usually means the registry could not be read. Use New tool to declare one of your own."
              />
            )}

            {/* empty, but only because of the filter. A different sentence
                from "no tools declared": one is a catalog with nothing in it,
                the other is a query with nothing behind it, and the action
                that fixes them is not the same. */}
            {!loadErr && all.length > 0 && matched.length === 0 && (
              <StateBlock
                title="No tools match"
                hint="Clear the filter to see the whole catalog."
                action={<Button size="sm" variant="ghost" onClick={() => setFilter(NO_TOOL_FILTER)}>Clear filter</Button>}
              />
            )}

            {/* content */}
            {!loadErr && matched.length > 0 && (
              <Stack gap="sm">
                <TableScroll>
                  {/* The kit's 520px floor is a sensible default for a six
                      column diagnostics table. This one has one or two, and the
                      floor alone was enough to push the second off the right
                      edge of a phone. */}
                  <Table style={{ minWidth: 0 }}>
                    <thead>
                      <tr>
                        <th>Tool</th>
                        {/* A COLUMN THAT SAYS THE SAME THING ON EVERY ROW IS
                            NOT A COLUMN. This was Owner, and on a catalog with
                            no user tools in it that is 21 identical `Platform`
                            tags over 21 identical `Used, not edited` lines: a
                            fifth of the table's width spent restating the
                            summary line above it. Owner moves onto the row's
                            own meta line, and what is left here is the thing
                            that actually differs between rows, which is whether
                            this one is yours to change. When none of them is,
                            there is no second column at all. */}
                        {anyEditable && <th>Yours</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {/* Two rows per tool: the columns you scan, then the
                          description on its own full-width line. A description
                          is written for a model as well as a person, so as a
                          column of its own it set the table's width and pushed
                          everything else off a phone. */}
                      {shown.rows.flatMap((t) => [
                        <tr key={t.id}>
                          <td>
                            <span className="ws-wrap">{t.title || t.id}</span>
                            {/* The id is what an allowlist writes, so it is on
                                the row rather than behind a disclosure, and kind
                                rides with it rather than taking a column: two
                                columns is what fits a phone without the table
                                scrolling sideways.
                                ws-wrap because an id like
                                mcp__workshop__list_attachments is one
                                unbreakable token, and it sets the column's
                                min-content width if it is allowed to. */}
                            <span className="ws-muted ws-wrap" style={{ display: "block", fontSize: "var(--fs-xs)" }}>
                              <span className="ws-mono">{t.id}</span>
                              {" \u00b7 "}
                              {t.kind}
                              {/* OWNER RIDES HERE, and it is a Tag only while
                                  it varies. A categorical hue is information
                                  when it tells rows apart; on a catalog that is
                                  all one provenance it tints every row and
                                  labels nothing, which is the same rule the
                                  design system applies to every other hue. */}
                              {" \u00b7 "}
                              {ownerVaries
                                ? <Tag hue={catFor(t.provenance)}>{provenanceLabel(t.provenance)}</Tag>
                                : provenanceLabel(t.provenance)}
                            </span>
                          </td>
                          {/* Only what differs. `editable` is derived from
                              provenance by the API and nothing else, so a row
                              that is not yours has nothing to put here: not a
                              disabled button (a control that cannot act is
                              worse than no control) and not a lock line either,
                              because 21 identical lock lines is the column this
                              replaced. That platform and app tools are used
                              rather than edited is said once, above. */}
                          {anyEditable && (
                            <td>
                              {t.editable && (
                                <Row wrap style={{ gap: "var(--space-2)" }}>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    aria-label={`Edit ${t.id}`}
                                    onClick={() => setForm({ mode: "edit", tool: t })}
                                  >
                                    <Pencil size={14} strokeWidth={1.8} aria-hidden /> Edit
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="danger-ghost"
                                    aria-label={`Delete ${t.id}`}
                                    onClick={() => { setDeleteErr(null); setDeleting(t); }}
                                  >
                                    <Trash2 size={14} strokeWidth={1.8} aria-hidden /> Delete
                                  </Button>
                                </Row>
                              )}
                            </td>
                          )}
                        </tr>,
                        t.description ? (
                          <tr key={`${t.id}:what`}>
                            <td colSpan={anyEditable ? 2 : 1} style={{ paddingTop: 0 }}>
                              {/* A measure, not the full row: a description
                                  set across 1200px is one long line the eye
                                  loses its place in. Narrower than the cell on
                                  a wide screen, wider than it on a phone, so
                                  the phone is unaffected. */}
                              {/* CLAMPED. A tool description is written for a
                                  model as well as a person, so several of them
                                  are full paragraphs, and 21 paragraphs is a
                                  wall rather than a catalog. Two lines, with
                                  the rest one click away and the button
                                  present only when there IS a rest. */}
                              <Clamp max={40} moreLabel="More" lessLabel="Less">
                                <span
                                  className="ws-muted"
                                  style={{ display: "block", maxWidth: "80ch", fontSize: "var(--fs-sm)" }}
                                >
                                  {t.description}
                                </span>
                              </Clamp>
                            </td>
                          </tr>
                        ) : null,
                      ])}
                    </tbody>
                  </Table>
                </TableScroll>

                {/* overflow */}
                {shown.hidden > 0 && (
                  <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
                    <Button size="sm" variant="ghost" onClick={() => setExpanded(true)}>
                      {/* The count of what the filter MATCHED, not of the
                          catalog: "Show all 21" under a filtered list of six
                          would name a number the button does not produce. */}
                      Show all {matched.length}
                    </Button>
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                      {shown.hidden} more not shown
                    </span>
                  </Row>
                )}
              </Stack>
            )}
          </Card>
        </section>

        <section>
          <SectionLabel>What a record does, and does not, do</SectionLabel>
          <Card>
            <Stack gap="sm">
              <span style={{ fontSize: "var(--fs-sm)" }}>
                A tool here is a declaration with an owner: a name, what it does, and how it launches.
                Declaring one grants it to nothing. Which tools an agent may actually call is decided
                when its turn is built, narrowed by the agent's role and by its persona ceiling, so
                there is no on/off switch on this page.
              </span>
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                Platform tools ship with the system and app tools arrive with an installed app. Both
                can be used but not edited. Your own tools live in `.runtime/tools.json` and this page
                is the only editor they have.
              </span>
            </Stack>
          </Card>
        </section>
      </Stack>

      {/* The MCP half. Read-only on purpose, and unchanged by the move: the
          per-chat on/off switch it might have grown already exists in the
          chat, and this page does not grow a second, disagreeing one. */}
      <McpServers embedded />

      <ConfirmDialog
        open={deleting !== null}
        onCancel={() => { setDeleting(null); setDeleteErr(null); }}
        onConfirm={() => { void remove(); }}
        title="Delete this tool?"
        body={
          <Stack gap="sm">
            <span>
              {`"${deleting?.title || deleting?.id}" stops being declared. `}
              {`Any agent definition or allowlist naming ${deleting?.id} will no longer resolve to anything. `}
              This cannot be undone.
            </span>
            {deleteErr && <Notice tone="error">{deleteErr}</Notice>}
          </Stack>
        }
        confirmLabel="Delete tool"
        busy={deleteBusy}
      />
    </div>
  );
}
