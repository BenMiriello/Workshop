// Settings, Speech, Spoken commands: the mode they are heard in and the trigger
// word (SpeechModeRows), what can be said to dictation and when each command is
// heard, and the operator's own commands. An action is heard only when said on
// its own, with a pause before and after; "new line" and "new paragraph" work
// anywhere. Kept on the server, so every device hears the same commands. Each
// change is saved as it is made, like the shortcuts above it.

import { useState } from "react";
import { MessageSquareText, Plus, RotateCcw, Trash2 } from "lucide-react";
import {
  Button, ConfirmDialog, Notice, Row as Line, SettingsRow as Row, SettingsSection as Section, Skeleton, Stack, Textarea,
} from "@workshop/ui";
import {
  resetSpeechCommands, saveSpeechCommands, useSpeechCommands, type CustomSpeechCommand, type SpeechCommands as Commands,
} from "../../lib/speech/commandsApi";
import { PhraseChips } from "./PhraseChips";
import { SpeechModeRows, WhenMenu } from "./SpeechModeRows";

function CustomRow({ c, onChange, onRemove }: {
  c: CustomSpeechCommand;
  onChange: (next: CustomSpeechCommand) => void;
  onRemove: () => void;
}) {
  const [text, setText] = useState(c.text);
  return (
    <Stack gap="sm" className="ws-own-command">
      <div className="ws-command-row">
        <PhraseChips phrases={c.phrases} label="your command" onChange={(phrases) => onChange({ ...c, phrases })} />
        <WhenMenu when={c.when ?? "alone"} label="your command" onChange={(when) => onChange({ ...c, when })} />
      </div>
      <Textarea
        grow
        rows={1}
        value={text}
        placeholder="Text it types"
        aria-label="Text it types"
        data-dictation="off"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => { if (text.trim() && text !== c.text) onChange({ ...c, text }); }}
      />
      <Button size="sm" variant="ghost" onClick={onRemove}><Trash2 aria-hidden /> Remove</Button>
    </Stack>
  );
}

// A command being added: saved once it has a phrase and a text.
function NewCommand({ onAdd, onCancel }: { onAdd: (c: CustomSpeechCommand) => void; onCancel: () => void }) {
  const [phrases, setPhrases] = useState<string[]>([]);
  const [text, setText] = useState("");
  return (
    <Stack gap="sm" className="ws-own-command">
      <PhraseChips phrases={phrases} label="the new command" onChange={setPhrases} />
      <Textarea
        grow
        rows={1}
        value={text}
        placeholder="Text it types"
        aria-label="Text it types"
        data-dictation="off"
        onChange={(e) => setText(e.target.value)}
      />
      <Line wrap>
        <Button size="sm" variant="primary" disabled={!phrases.length || !text.trim()}
          onClick={() => onAdd({ id: "", phrases, text, when: "alone" })}>Add</Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </Line>
    </Stack>
  );
}

export function SpeechCommands() {
  const { data, isPending, isError, refetch } = useSpeechCommands();
  const [failed, setFailed] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);

  const save = (next: Commands) => {
    setFailed(false);
    void saveSpeechCommands(next).catch(() => setFailed(true));
  };

  let body;
  if (isPending) body = <Stack gap="sm"><Skeleton /><Skeleton /><Skeleton /></Stack>;
  else if (isError || !data) {
    body = <Notice tone="error" where="GET /api/speech/commands" action={<Button size="sm" onClick={() => void refetch()}>Retry</Button>}>Couldn't load spoken commands.</Notice>;
  } else {
    const c = data;
    body = (
      <>
        {failed && <Notice tone="error" where="PUT /api/speech/commands">Couldn't save that change.</Notice>}
        <SpeechModeRows
          c={c}
          onFail={() => setFailed(true)}
          onAllTrigger={() => save({
            ...c,
            builtins: c.builtins.map((x) => (x.when === "alone" ? { ...x, when: "trigger" } : x)),
            custom: c.custom.map((x) => (x.when === "alone" ? { ...x, when: "trigger" } : x)),
          })}
        />
        {c.builtins.map((b, i) => (
          <Row key={b.id} label={b.label.toLowerCase()} hint={b.takes_arg ? "followed by a name" : undefined}>
            <div className="ws-command-row">
              <PhraseChips
                phrases={b.phrases}
                label={b.label}
                onChange={(phrases) => save({ ...c, builtins: c.builtins.map((x, j) => (j === i ? { ...x, phrases } : x)) })}
              />
              <WhenMenu
                when={b.when}
                label={b.label}
                onChange={(when) => save({ ...c, builtins: c.builtins.map((x, j) => (j === i ? { ...x, when } : x)) })}
              />
            </div>
          </Row>
        ))}
        <Row label="your commands" hint="Say a phrase on its own to type its text.">
          <Stack className="ws-own-commands">
            {c.custom.map((own, i) => (
              <CustomRow
                key={own.id || i}
                c={own}
                onChange={(next) => save({ ...c, custom: c.custom.map((x, j) => (j === i ? next : x)) })}
                onRemove={() => setRemoving(i)}
              />
            ))}
            {adding ? (
              <NewCommand
                onAdd={(own) => { save({ ...c, custom: [...c.custom, own] }); setAdding(false); }}
                onCancel={() => setAdding(false)}
              />
            ) : (
              <Button size="sm" onClick={() => setAdding(true)}><Plus aria-hidden /> Add a command</Button>
            )}
          </Stack>
        </Row>
        <Row label="defaults">
          <Button size="sm" variant="ghost" onClick={() => setResetting(true)}><RotateCcw aria-hidden /> Reset to defaults</Button>
        </Row>
        <ConfirmDialog
          open={resetting}
          onCancel={() => setResetting(false)}
          onConfirm={() => { setResetting(false); void resetSpeechCommands().catch(() => setFailed(true)); }}
          title="Reset spoken commands?"
          body="Every phrase goes back to its default and your own commands are deleted. This can't be undone."
          confirmLabel="Reset"
          danger
        />
        <ConfirmDialog
          open={removing !== null}
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            if (removing !== null) save({ ...c, custom: c.custom.filter((_, j) => j !== removing) });
            setRemoving(null);
          }}
          title="Remove this command?"
          body="Its phrases will be typed as words again. This can't be undone."
          confirmLabel="Remove"
          danger
        />
      </>
    );
  }
  return <section id="commands"><Section icon={MessageSquareText} title="Spoken commands">{body}</Section></section>;
}
