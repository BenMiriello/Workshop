// A spoken command's phrases: chips that come off, and a plus that opens a
// field on its own line to add one. Any phrase starts the command.

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Input } from "@workshop/ui";

export function PhraseChips({ phrases, onChange, label }: {
  phrases: string[];
  onChange: (next: string[]) => void;
  label: string;
}) {
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const close = () => { setText(""); setAdding(false); };
  const add = () => {
    const p = text.trim().toLowerCase().replace(/[.,!?]+$/, "");
    if (p && !phrases.includes(p)) onChange([...phrases, p]);
    close();
  };
  return (
    <div className="ws-phrases">
      {phrases.map((p) => (
        <span key={p} className="ws-phrases__chip">
          <span>{p}</span>
          <button type="button" aria-label={`Remove "${p}"`} onClick={() => onChange(phrases.filter((x) => x !== p))}>
            <X aria-hidden size={14} />
          </button>
        </span>
      ))}
      <button type="button" className="ws-phrases__plus" aria-label={`Add a phrase for ${label}`} onClick={() => setAdding(true)}>
        <Plus aria-hidden size={14} />
      </button>
      {adding && (
        <Input
          className="ws-phrases__add"
          autoFocus
          value={text}
          aria-label={`New phrase for ${label}`}
          data-dictation="off"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); add(); }
            else if (e.key === "Escape") close();
          }}
          onBlur={add}
        />
      )}
    </div>
  );
}
