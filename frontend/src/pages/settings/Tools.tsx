import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus, Trash2, Wrench } from "lucide-react";
import {
  ActionBar,
  Button,
  Card,
  ConfirmDialog,
  Field,
  Input,
  Notice,
  PageHeader,
  SectionLabel,
  Select,
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

// Settings / Tools: the full catalog of named capabilities, and CRUD for the
// ones the operator owns.
//
// Platform and app tools are read-only by design ("can only be used not edited",
// operator 2026-08-23). They are shown without any edit affordance -- no
// disabled button, no dimmed row. The `editable` flag on the record is the
// single truth: only the rows whose `editable` is true get action buttons.
//
// A 409 on create names the conflicting id (the backend raises it with the id
// in the message). That message surfaces directly so the operator knows which
// platform tool the chosen id collides with.

type ToolRecord = components["schemas"]["ToolRecord"];

const ENDPOINT = "/api/tools";
const KIND_OPTIONS = ["builtin", "mcp", "command"] as const;
const OVERFLOW_AT = 15;

// ---- Create form -----------------------------------------------------------

type CreateDraft = { id: string; title: string; description: string; kind: string };
const CREATE_INITIAL: CreateDraft = { id: "", title: "", description: "", kind: "command" };

function CreateToolForm({
  onDone,
  onCancel,
}: {
  onDone: (t: ToolRecord) => void;
  onCancel: () => void;
}) {
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const edit = useEditMode<CreateDraft>(CREATE_INITIAL, async (draft) => {
    setSaveErr(null);
    const r = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    const j = await r.json().catch(() => ({})) as { detail?: string };
    if (!r.ok) {
      const msg = j.detail ?? `HTTP ${r.status}`;
      setSaveErr(msg);
      throw new Error(msg);
    }
    onDone(j as unknown as ToolRecord);
  });

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { edit.begin(); }, []);

  return (
    <Card style={{ marginBottom: "var(--space-4)" }}>
      <Stack gap="sm">
        <Field label="ID" htmlFor="tool-id" required help="Stable identifier. Cannot be changed after creation.">
          <Input
            id="tool-id"
            value={edit.draft.id}
            onChange={(e) => edit.set("id", e.target.value)}
            placeholder="my-tool"
            autoFocus
          />
        </Field>
        <Field label="Title" htmlFor="tool-title">
          <Input
            id="tool-title"
            value={edit.draft.title}
            onChange={(e) => edit.set("title", e.target.value)}
            placeholder="My tool"
          />
        </Field>
        <Field label="Kind" htmlFor="tool-kind">
          <Select
            id="tool-kind"
            value={edit.draft.kind}
            onChange={(e) => edit.set("kind", e.target.value)}
          >
            {KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </Select>
        </Field>
        <Field label="Description" htmlFor="tool-desc">
          <Textarea
            id="tool-desc"
            value={edit.draft.description}
            onChange={(e) => edit.set("description", e.target.value)}
            placeholder="What this tool does."
            rows={3}
          />
        </Field>
        {saveErr && (
          <Notice tone="error" detail={saveErr}>Could not create tool.</Notice>
        )}
      </Stack>
      <ActionBar flash={edit.flash ? "Saved" : undefined}>
        <Button variant="ghost" onClick={onCancel} disabled={edit.saving}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={() => { void edit.save().catch(() => {}); }}
          loading={edit.saving}
          disabled={edit.saving || !edit.draft.id.trim()}
        >
          Create tool
        </Button>
      </ActionBar>
    </Card>
  );
}

// ---- Edit form -------------------------------------------------------------

type EditDraft = { title: string; description: string; kind: string };

function EditToolForm({
  tool,
  onDone,
  onCancel,
}: {
  tool: ToolRecord;
  onDone: (t: ToolRecord) => void;
  onCancel: () => void;
}) {
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const initial: EditDraft = {
    title: tool.title,
    description: tool.description,
    kind: tool.kind,
  };

  const edit = useEditMode<EditDraft>(initial, async (draft) => {
    setSaveErr(null);
    const r = await fetch(`${ENDPOINT}/${tool.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    const j = await r.json().catch(() => ({})) as { detail?: string };
    if (!r.ok) {
      const msg = j.detail ?? `HTTP ${r.status}`;
      setSaveErr(msg);
      throw new Error(msg);
    }
    onDone(j as unknown as ToolRecord);
  });

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { edit.begin(); }, []);

  return (
    <Card style={{ marginBottom: "var(--space-4)" }}>
      <Stack gap="sm">
        <div>
          <span className="ws-muted">Editing </span>
          <span className="ws-mono">{tool.id}</span>
        </div>
        <Field label="Title" htmlFor="tool-edit-title">
          <Input
            id="tool-edit-title"
            value={edit.draft.title}
            onChange={(e) => edit.set("title", e.target.value)}
            placeholder="My tool"
            autoFocus
          />
        </Field>
        <Field label="Kind" htmlFor="tool-edit-kind">
          <Select
            id="tool-edit-kind"
            value={edit.draft.kind}
            onChange={(e) => edit.set("kind", e.target.value)}
          >
            {KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </Select>
        </Field>
        <Field label="Description" htmlFor="tool-edit-desc">
          <Textarea
            id="tool-edit-desc"
            value={edit.draft.description}
            onChange={(e) => edit.set("description", e.target.value)}
            placeholder="What this tool does."
            rows={3}
          />
        </Field>
        {saveErr && (
          <Notice tone="error" detail={saveErr}>Could not save tool.</Notice>
        )}
      </Stack>
      <ActionBar flash={edit.flash ? "Saved" : undefined}>
        <Button variant="ghost" onClick={onCancel} disabled={edit.saving}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={() => { void edit.save().catch(() => {}); }}
          loading={edit.saving}
          disabled={edit.saving}
        >
          Save changes
        </Button>
      </ActionBar>
    </Card>
  );
}

// ---- Main page -------------------------------------------------------------

export default function Tools() {
  const [tools, setTools] = useState<ToolRecord[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [editingTool, setEditingTool] = useState<ToolRecord | null>(null);
  const [deletingTool, setDeletingTool] = useState<ToolRecord | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadErr(null);
    try {
      const r = await fetch(ENDPOINT);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json() as { tools: ToolRecord[] };
      setTools(data.tools);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const onToolCreated = (tool: ToolRecord) => {
    setTools((prev) => [...(prev ?? []), tool]);
    setCreating(false);
  };

  const onToolUpdated = (tool: ToolRecord) => {
    setTools((prev) => prev?.map((t) => t.id === tool.id ? tool : t) ?? []);
    setEditingTool(null);
  };

  const startEditing = (tool: ToolRecord) => {
    setCreating(false);
    setEditingTool(tool);
  };

  const confirmDelete = async () => {
    if (!deletingTool) return;
    setDeleting(true);
    try {
      await fetch(`${ENDPOINT}/${deletingTool.id}`, { method: "DELETE" });
      setTools((prev) => prev?.filter((t) => t.id !== deletingTool.id) ?? []);
    } finally {
      setDeleting(false);
      setDeletingTool(null);
    }
  };

  const showingForm = creating || editingTool != null;

  return (
    <div>
      <PageHeader
        title="Tools"
        actions={
          !showingForm ? (
            <Button size="sm" variant="ghost" onClick={() => setCreating(true)}>
              <Plus aria-hidden /> New tool
            </Button>
          ) : undefined
        }
      />

      {creating && (
        <CreateToolForm
          onDone={onToolCreated}
          onCancel={() => setCreating(false)}
        />
      )}

      {editingTool && (
        <EditToolForm
          key={editingTool.id}
          tool={editingTool}
          onDone={onToolUpdated}
          onCancel={() => setEditingTool(null)}
        />
      )}

      <section>
        <SectionLabel icon={<Wrench size={14} strokeWidth={1.8} aria-hidden />}>
          Catalog
        </SectionLabel>
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

          {!loadErr && tools?.length === 0 && (
            <StateBlock
              title="No tools declared"
              hint='Create a user tool with "New tool" above, or install an app that declares one.'
            />
          )}

          {!loadErr && tools && tools.length > 0 && (
            <TableScroll className={tools.length > OVERFLOW_AT ? "tools-scroll" : undefined}>
              <Table>
                <thead>
                  <tr>
                    <th>Tool</th>
                    <th>Description</th>
                    <th>Kind</th>
                    <th>Source</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {tools.map((tool) => (
                    <tr key={tool.id}>
                      <td>
                        <div>{tool.title || <span className="ws-muted">untitled</span>}</div>
                        <div className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                          {tool.id}
                        </div>
                      </td>
                      <td>
                        <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                          {tool.description || <span style={{ fontStyle: "italic" }}>no description</span>}
                        </span>
                      </td>
                      <td>
                        <Tag hue={catFor(tool.kind)}>{tool.kind}</Tag>
                      </td>
                      <td>
                        <span className="ws-muted">{tool.provenance}</span>
                      </td>
                      <td>
                        {tool.editable && (
                          <span style={{ display: "inline-flex", gap: "var(--space-2)" }}>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => startEditing(tool)}
                              aria-label={`Edit ${tool.id}`}
                            >
                              <Pencil size={14} strokeWidth={1.8} aria-hidden /> Edit
                            </Button>
                            <Button
                              size="sm"
                              variant="danger-ghost"
                              onClick={() => setDeletingTool(tool)}
                              aria-label={`Delete ${tool.id}`}
                            >
                              <Trash2 size={14} strokeWidth={1.8} aria-hidden /> Delete
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
        </Card>
      </section>

      <ConfirmDialog
        open={!!deletingTool}
        onCancel={() => setDeletingTool(null)}
        onConfirm={confirmDelete}
        title={`Delete "${deletingTool?.title || deletingTool?.id}"?`}
        body={`Removes the tool "${deletingTool?.id}". Any agent that references it in its ceiling or scopes will need updating. This can't be undone.`}
        confirmLabel="Delete tool"
        busy={deleting}
      />
    </div>
  );
}
