import { useCallback, useEffect, useState } from "react";
import { Cpu, LayoutGrid } from "lucide-react";
import {
  Button, Notice, ReadOnly, Row as Line, Select, Skeleton, StateBlock,
  SettingsRow as Row, SettingsSection as Section,
} from "@workshop/ui";
import { chatApi, type ModelDefaults as Defaults, type ModelDefaultsPatch } from "../../lib/chat";
import {
  SCOPES_SHOWN, compactionFallbackLabel, fallbackLabel, nameOf, optionLabel, visibleScopes, withStored,
} from "./modelDefaults";

// Settings > Chat: which model a chat runs on when it has picked none, and the
// local model that writes compaction summaries, for every chat and per app.
//
// Commits on change, like the chat defaults above it: none of these touches a
// chat that already chose its model, so a mis-pick costs one more click (the
// "view preference" case of design rule 7, as decided for those controls on
// 2026-08-20).

const ENDPOINT = "/api/chat/model-defaults";
// "" is a chosen value: no setting of its own, so the next rung answers.
const UNSET = "";

const skeletonRow = (label: string) => (
  <Row key={label} label={label}>
    <Skeleton width="14rem" style={{ height: "var(--control-h)" }} />
  </Row>
);

export default function ModelDefaults() {
  const [data, setData] = useState<Defaults | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(() => {
    setErr(null);
    chatApi.getModelDefaults().then(setData).catch((e) => setErr(String(e)));
  }, []);
  useEffect(load, [load]);

  const save = useCallback(async (key: string, patch: ModelDefaultsPatch) => {
    setSaveErr(null);
    setSaving(key);
    try {
      setData(await chatApi.setModelDefaults(patch));
    } catch (e) {
      setSaveErr(String(e));
    } finally {
      setSaving(null);
    }
  }, []);

  if (err) {
    return (
      <Section icon={Cpu} title="Models">
        <StateBlock
          variant="error"
          title="Couldn't load model defaults"
          hint={`GET ${ENDPOINT} failed: ${err}`}
          action={<Button size="sm" variant="ghost" onClick={load}>Retry</Button>}
        />
      </Section>
    );
  }
  if (!data) {
    return (
      <>
        <Section icon={Cpu} title="Models">
          {skeletonRow("default model")}
          {skeletonRow("compaction model")}
        </Section>
        <Section icon={LayoutGrid} title="Model per app">
          {skeletonRow("apps")}
        </Section>
      </>
    );
  }

  const deployment = nameOf(data.deployment_model, data.models);
  const compaction = data.compaction_model ?? UNSET;
  const compactionChoices = withStored(data.compaction_models, data.compaction_model);
  const scopes = visibleScopes(data.scopes, showAll);

  return (
    <>
      <Section icon={Cpu} title="Models">
        <Row
          label="default model"
          hint="What a chat runs on when neither it nor its app has a model set."
        >
          <Select
            value={data.default_model ?? UNSET}
            onChange={(e) => { void save("default", { default_model: e.target.value }); }}
            disabled={saving === "default"}
            aria-label="Default model"
          >
            <option value={UNSET}>{`System default (${deployment})`}</option>
            {withStored(data.models, data.default_model).map((m) => (
              <option key={m.id} value={m.id}>{optionLabel(m)}</option>
            ))}
          </Select>
        </Row>

        <Row
          label="compaction model"
          hint="A local model that writes the summary when a local chat compacts. It is used only when its window holds the conversation and leaves the summary as much room as the chat's own model would. Otherwise the chat's own model writes it. Each app below can pick its own."
        >
          {compactionChoices.length === 0 ? (
            <ReadOnly value="No local model with a known window is available." />
          ) : (
            <Select
              value={compaction}
              onChange={(e) => { void save("compaction", { compaction_model: e.target.value }); }}
              disabled={saving === "compaction"}
              aria-label="Compaction model"
            >
              <option value={UNSET}>None: each chat compacts on its own model</option>
              {compactionChoices.map((m) => (
                <option key={m.id} value={m.id}>{optionLabel(m)}</option>
              ))}
            </Select>
          )}
        </Row>
      </Section>

      <Section icon={LayoutGrid} title="Model per app">
        {data.scopes.length === 0 ? (
          <StateBlock
            title="No apps or projects yet"
            hint="Install an app or create a project to give it a model of its own."
          />
        ) : (
          <>
            {scopes.map((s) => (
              <Row key={s.key} label={s.label}>
                <Line wrap>
                  <Select
                    value={s.model ?? UNSET}
                    onChange={(e) => {
                      void save(s.key, { scope_models: { [s.key]: e.target.value } });
                    }}
                    disabled={saving === s.key}
                    aria-label={`Model for ${s.label}`}
                  >
                    <option value={UNSET}>{fallbackLabel(s, data.models)}</option>
                    {withStored(data.models, s.model).map((m) => (
                      <option key={m.id} value={m.id}>{optionLabel(m)}</option>
                    ))}
                  </Select>
                  {compactionChoices.length > 0 && (
                    <Select
                      value={s.compaction_model ?? UNSET}
                      onChange={(e) => {
                        void save(`${s.key}:compaction`, {
                          scope_compaction_models: { [s.key]: e.target.value },
                        });
                      }}
                      disabled={saving === `${s.key}:compaction`}
                      aria-label={`Compaction model for ${s.label}`}
                    >
                      <option value={UNSET}>{compactionFallbackLabel(s, data.compaction_models)}</option>
                      {withStored(data.compaction_models, s.compaction_model).map((m) => (
                        <option key={m.id} value={m.id}>{optionLabel(m)}</option>
                      ))}
                    </Select>
                  )}
                </Line>
              </Row>
            ))}
            {data.scopes.length > SCOPES_SHOWN && (
              <Button size="sm" variant="ghost" onClick={() => setShowAll((v) => !v)}>
                {showAll ? "Show fewer" : `Show all ${data.scopes.length}`}
              </Button>
            )}
          </>
        )}
      </Section>

      {saveErr && (
        <Notice tone="error" detail={saveErr} where={`POST ${ENDPOINT}`}>
          Couldn't save the model setting.
        </Notice>
      )}
    </>
  );
}
