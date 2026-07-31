// The composer's type-ahead list, above the input.
//
// ONE component for both pickers. The slash-command list and the @-mention list
// were the same twenty lines of markup twice, differing only in what each row
// said — so a change to keyboard behaviour, hover, or the touch fix below had to
// be made in two places and, predictably, drifted.
//
// The touch fix is the reason this is worth keeping in one place: the action
// runs on CLICK, and `onMouseDown` only calls preventDefault (to keep the
// textarea focused). It used to run ON mousedown, which a touch device
// synthesizes only after the fact and not dependably — so on a phone, tapping a
// command did nothing at all: no error, no message, no fold. `click` fires on
// both.
export type PickerOption = {
  // Stable identity, and what the caller gets back on pick.
  key: string;
  // The leading token, already prefixed (`/compact`, `@reviewer`).
  label: string;
  desc: string;
};

export default function CommandPicker({
  options,
  selected,
  ariaLabel,
  onHover,
  onPick,
}: {
  options: PickerOption[];
  // Clamped here rather than by every caller: the selection survives the list
  // narrowing under it as the operator keeps typing.
  selected: number;
  ariaLabel: string;
  onHover: (index: number) => void;
  onPick: (key: string) => void;
}) {
  if (options.length === 0) return null;
  const active = Math.min(selected, options.length - 1);
  return (
    <ul className="chat-slash-menu" role="listbox" aria-label={ariaLabel}>
      {options.map((o, i) => (
        <li
          key={o.key}
          role="option"
          aria-selected={i === active}
          className={`chat-slash-item${i === active ? " is-active" : ""}`}
          onMouseEnter={() => onHover(i)}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onPick(o.key)}
        >
          <span className="chat-slash-name">{o.label}</span>
          <span className="chat-slash-desc">{o.desc}</span>
        </li>
      ))}
    </ul>
  );
}
