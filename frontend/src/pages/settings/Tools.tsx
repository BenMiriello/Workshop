import { useCallback, useEffect, useMemo, useState } from "react";
import { Lock, Pencil, Plus, Trash2, Wrench } from "lucide-react";
import {
  ActionBar,
  Button,
  Card,
  ConfirmDialog,
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
  TOOL_KINDS,
  canSave,
  capRows,
  catalogSummary,
  createBody,
  draftFromTool,
  provenanceLabel,
  updateBody,
  type ToolDraft,
  type ToolRecord,
} from "../../lib/tools";

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
  const shown = capRows(all, expanded);
  const summary = catalogSummary(all);

  return (
    <div>
      <PageHeader
        title="Tools"
        sub="Every capability this system can name, and who owns each one."
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
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{summary}</span>
            )}
          </Row>

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

            {/* content */}
            {!loadErr && all.length > 0 && (
              <Stack gap="sm">
                <TableScroll>
                  {/* The kit's 520px floor is a sensible default for a six
                      column diagnostics table. This one has three, and on a
                      phone the floor pushed the Edit and Delete controls off
                      the right edge of a row the operator owns. */}
                  <Table style={{ minWidth: 0 }}>
                    <thead>
                      <tr>
                        <th>Tool</th>
                        <th>Owner</th>
                      </tr>
                    </thead>
                    <tbody>
                      {/* Two rows per tool: the columns you scan, then the
                          description on its own full-width line. A description
                          is written for a model as well as a person, so as a
                          fifth column it set the table's width and pushed the
                          rest off a phone. */}
                      {shown.rows.flatMap((t) => [
                        <tr key={t.id}>
                          <td>
                            <span className="ws-wrap">{t.title || t.id}</span>
                            {/* The id is what an allowlist writes, so it is on
                                the row rather than behind a disclosure. Kind
                                rides with it instead of taking a column of its
                                own: three columns is what fits a phone before
                                the table starts scrolling sideways. */}
                            {/* ws-wrap: an id like mcp__workshop__list_attachments
                                is one unbreakable token, and it sets the
                                column's min-content width if it is allowed to. */}
                            <span className="ws-muted ws-wrap" style={{ display: "block", fontSize: "var(--fs-xs)" }}>
                              <span className="ws-mono">{t.id}</span>
                              {" \u00b7 "}
                              {t.kind}
                            </span>
                          </td>
                          {/* Owner and editability are ONE fact: the API
                              derives `editable` from provenance and nothing
                              else. They share a cell so a phone can hold both,
                              and so a row never says who owns it in one place
                              and what that means in another. */}
                          <td>
                            {/* flex-start, or the Stack stretches a Tag into a
                                480px bar on a wide screen. */}
                            <Stack gap="sm" style={{ alignItems: "flex-start" }}>
                            <Tag hue={catFor(t.provenance)}>{provenanceLabel(t.provenance)}</Tag>
                            {t.editable ? (
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
                            ) : (
                              // Not a disabled button: a control that cannot act
                              // is worse than no control. The sentence is the
                              // API's own ("can be used but not edited").
                              <span
                                className="ws-muted"
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "var(--space-2)",
                                  fontSize: "var(--fs-xs)",
                                }}
                              >
                                <Lock size={14} strokeWidth={1.8} aria-hidden />
                                Used, not edited
                              </span>
                            )}
                            </Stack>
                          </td>
                        </tr>,
                        t.description ? (
                          <tr key={`${t.id}:what`}>
                            <td colSpan={2} style={{ paddingTop: 0 }}>
                              {/* A measure, not the full row: a description
                                  set across 1200px is one long line the eye
                                  loses its place in. Narrower than the cell on
                                  a wide screen, wider than it on a phone, so
                                  the phone is unaffected. */}
                              <span
                                className="ws-muted"
                                style={{ display: "block", maxWidth: "80ch", fontSize: "var(--fs-sm)" }}
                              >
                                {t.description}
                              </span>
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
                      Show all {all.length}
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
