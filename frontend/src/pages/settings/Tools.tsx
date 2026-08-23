import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus, Trash2, Wrench } from "lucide-react";
import {
  ActionBar,
  Button,
  Card,
  ConfirmDialog,
  Input,
  Notice,
  PageHeader,
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
  useEditMode,
} from "@workshop/ui";
import type { components } from "../../api/schema.gen";

// Settings -> Tools. The full catalog of declared capabilities: platform tools
// that ship with the system, app tools declared by installed apps, and user
// tools the operator created at runtime. Only user tools are editable or
// deletable -- platform and app tools are read-only by design (they can only
// be used, not edited: docs/platform-redesign/tools-are-declared-2026-08-23.md
// sec 2.2).
//
// The page shows provenance clearly so the operator sees at a glance which
// category each tool belongs to, without needing a separate "platform" or "yours"
// indicator beyond the provenance Tag.
//
// NOT an enforcement point. The catalog here is an enumeration and an owner
// record; it does not grant any tool to any agent. services/llm_router.tool_flags
// is the only thing that grants a tool.

type ToolRecord = components["schemas"]["ToolRecord"];

const ENDPOINT = "/api/tools";
const OVERFLOW_AT = 20;

type ToolDraft = {
  id: string;
  title: string;
  description: string;
  kind: string;
  command: string;
};

const BLANK: ToolDraft = {
  id: "",
  title: "",
  description: "",
  kind: "command",
  command: "",
};

function recordToDraft(t: ToolRecord): ToolDraft {
  return {
    id: t.id,
    title: t.title,
    description: t.description,
    kind: t.kind,
    command: (t.spec.command ?? []).join(" "),
  };
}

type FormTarget = { mode: "create" } | { mode: "edit"; tool: ToolRecord };

// ToolForm: create or edit a user tool. Always enters editing mode on mount,
// since the component exists only when the form is open.
function ToolForm({
  initial,
  isCreate,
  onSave,
  onCancel,
}: {
  initial: ToolDraft;
  isCreate: boolean;
  onSave: (d: ToolDraft) => Promise<void>;
  onCancel: () => void;
}) {
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const edit = useEditMode<ToolDraft>(initial, async (draft) => {
    setSaveErr(null);
    try {
      await onSave(draft);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setSaveErr(msg);
      throw e;
    }
  });

  // Always start in editing mode. The component only mounts when the form is open.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { edit.begin(); }, []);

  return (
    <Stack>
      {isCreate && (
        <SettingsRow
          label="id"
          hint="Letters, digits, dots, hyphens, underscores. Cannot match a platform tool id."
        >
          <Input
            value={edit.draft.id}
            onChange={(e) => edit.set("id", e.target.value)}
            placeholder="my-tool"
            autoFocus
          />
        </SettingsRow>
      )}
      <SettingsRow label="title">
        <Input
          value={edit.draft.title}
          onChange={(e) => edit.set("title", e.target.value)}
          placeholder="My tool"
          autoFocus={!isCreate}
        />
      </SettingsRow>
      <SettingsRow label="description">
        <Textarea
          value={edit.draft.description}
          onChange={(e) => edit.set("description", e.target.value)}
          rows={3}
        />
      </SettingsRow>
      <SettingsRow label="kind">
        <Select
          value={edit.draft.kind}
          onChange={(e) => edit.set("kind", e.target.value)}
        >
          <option value="command">command</option>
          <option value="builtin">builtin</option>
          <option value="mcp">mcp</option>
        </Select>
      </SettingsRow>
      {edit.draft.kind === "command" && (
        <SettingsRow label="command" hint="Shell command with arguments. Runs in the turn sandbox.">
          <Input
            value={edit.draft.command}
            onChange={(e) => edit.set("command", e.target.value)}
            placeholder="/path/to/script arg1"
          />
        </SettingsRow>
      )}
      {saveErr && (
        <Notice tone="error" detail={saveErr}>
          Couldn't save tool.
        </Notice>
      )}
      <ActionBar flash={edit.flash ? "Saved" : undefined}>
        <Button variant="ghost" onClick={onCancel} disabled={edit.saving}>
          Cancel
        </Button>
        <Button
          variant="primary"
          loading={edit.saving}
          disabled={isCreate ? !edit.draft.id.trim() : !edit.dirty}
          onClick={() => {
            void edit.save().catch(() => {});
          }}
        >
          Save
        </Button>
      </ActionBar>
    </Stack>
  );
}

export default function Tools() {
  const [tools, setTools] = useState<ToolRecord[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [formTarget, setFormTarget] = useState<FormTarget | null>(null);
  const [deleting, setDeleting] = useState<ToolRecord | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadErr(null);
    try {
      const r = await fetch(ENDPOINT);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setTools(((await r.json()) as { tools: ToolRecord[] }).tools);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = useCallback(
    async (draft: ToolDraft) => {
      const isCreate = formTarget?.mode === "create";
      const toolId = isCreate
        ? draft.id
        : (formTarget as { mode: "edit"; tool: ToolRecord }).tool.id;
      const spec =
        draft.kind === "command" && draft.command.trim()
          ? { command: draft.command.trim().split(/\s+/) }
          : {};
      const body = isCreate
        ? {
            id: draft.id,
            title: draft.title,
            description: draft.description,
            kind: draft.kind,
            spec,
          }
        : {
            title: draft.title,
            description: draft.description,
            kind: draft.kind,
            spec,
          };

      const r = await fetch(isCreate ? ENDPOINT : `${ENDPOINT}/${toolId}`, {
        method: isCreate ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = (await r.json().catch(() => ({}))) as { detail?: string };
        throw new Error(j.detail ?? `HTTP ${r.status}`);
      }
      setFormTarget(null);
      await load();
    },
    [formTarget, load],
  );

  const handleDelete = useCallback(async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    setDeleteErr(null);
    try {
      const r = await fetch(`${ENDPOINT}/${deleting.id}`, { method: "DELETE" });
      if (!r.ok) {
        const j = (await r.json().catch(() => ({}))) as { detail?: string };
        setDeleteErr(j.detail ?? `HTTP ${r.status}`);
        return;
      }
      setDeleting(null);
      await load();
    } finally {
      setDeleteBusy(false);
    }
  }, [deleting, load]);

  const editingId =
    formTarget?.mode === "edit" ? formTarget.tool.id : null;

  return (
    <div>
      <PageHeader
        title="Tools"
        actions={
          formTarget === null ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setFormTarget({ mode: "create" })}
            >
              <Plus size={16} strokeWidth={1.8} aria-hidden /> New tool
            </Button>
          ) : undefined
        }
      />

      <Stack>
        <section>
          <SectionLabel icon={<Wrench size={14} strokeWidth={1.8} aria-hidden />}>
            Catalog
          </SectionLabel>

          {formTarget?.mode === "create" && (
            <Card style={{ marginBottom: "var(--space-4)" }}>
              <ToolForm
                initial={BLANK}
                isCreate={true}
                onSave={handleSave}
                onCancel={() => setFormTarget(null)}
              />
            </Card>
          )}

          <Card>
            {loading && !tools && (
              <Stack gap="sm">
                <Skeleton width="60%" />
                <Skeleton width="45%" />
                <Skeleton width="52%" />
              </Stack>
            )}

            {loadErr && (
              <StateBlock
                variant="error"
                title="Couldn't load tools"
                hint={`GET ${ENDPOINT} failed (${loadErr}).`}
                action={
                  <Button size="sm" variant="ghost" onClick={() => void load()}>
                    Try again
                  </Button>
                }
              />
            )}

            {!loadErr && tools && tools.length === 0 && (
              <StateBlock
                title="No tools declared"
                hint="Platform tools appear here once the registry is seeded. Use New tool to declare your own."
              />
            )}

            {!loadErr && tools && tools.length > 0 && (
              <TableScroll
                className={tools.length > OVERFLOW_AT ? "tools-scroll" : undefined}
              >
                <Table>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Kind</th>
                      <th>Provenance</th>
                      <th>Description</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {tools.map((t) => (
                      <tr key={t.id}>
                        <td>
                          <span
                            className="ws-mono"
                            style={{ fontSize: "var(--fs-sm)" }}
                          >
                            {t.id}
                          </span>
                          {t.title && t.title !== t.id && (
                            <span
                              className="ws-muted"
                              style={{
                                display: "block",
                                fontSize: "var(--fs-xs)",
                              }}
                            >
                              {t.title}
                            </span>
                          )}
                        </td>
                        <td>
                          <Tag hue={catFor(t.kind)}>{t.kind}</Tag>
                        </td>
                        <td>
                          <Tag hue={catFor(t.provenance)}>{t.provenance}</Tag>
                        </td>
                        <td>
                          <span
                            className="ws-muted"
                            style={{ fontSize: "var(--fs-sm)" }}
                          >
                            {t.description}
                          </span>
                        </td>
                        <td>
                          {t.editable && (
                            <span
                              style={{
                                display: "inline-flex",
                                gap: "var(--space-2)",
                              }}
                            >
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() =>
                                  setFormTarget({ mode: "edit", tool: t })
                                }
                                disabled={formTarget !== null}
                              >
                                <Pencil
                                  size={14}
                                  strokeWidth={1.8}
                                  aria-hidden
                                />{" "}
                                Edit
                              </Button>
                              <Button
                                size="sm"
                                variant="danger-ghost"
                                onClick={() => {
                                  setDeleteErr(null);
                                  setDeleting(t);
                                }}
                                disabled={formTarget !== null}
                              >
                                <Trash2
                                  size={14}
                                  strokeWidth={1.8}
                                  aria-hidden
                                />{" "}
                                Delete
                              </Button>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </TableScroll>
            )}

            {deleteErr && (
              <div style={{ marginTop: "var(--space-3)" }}>
                <Notice tone="error" detail={deleteErr}>
                  Couldn't delete tool.
                </Notice>
              </div>
            )}
          </Card>

          {formTarget?.mode === "edit" && (
            <Card style={{ marginTop: "var(--space-4)" }}>
              <ToolForm
                key={editingId}
                initial={recordToDraft(formTarget.tool)}
                isCreate={false}
                onSave={handleSave}
                onCancel={() => setFormTarget(null)}
              />
            </Card>
          )}
        </section>
      </Stack>

      <ConfirmDialog
        open={!!deleting}
        onCancel={() => {
          setDeleting(null);
          setDeleteErr(null);
        }}
        onConfirm={() => {
          void handleDelete();
        }}
        title="Delete this tool?"
        body={`"${deleting?.title || deleting?.id}" will be removed from your declared tools. Any agent that references it by this id will stop finding it. This cannot be undone.`}
        confirmLabel="Delete tool"
        busy={deleteBusy}
      />
    </div>
  );
}
