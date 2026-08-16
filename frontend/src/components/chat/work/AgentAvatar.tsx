// THE SQUARE THAT SAYS WHO. One agent, one initial, in four sizes.
//
// The 4a design draws this in every place an agent is named: beside a run on
// the ticket card, on a needs-you card in the Work panel, on a running row's
// second line, and beside the question in the answer view. It is one component
// because those four are the same mark seen at different scales, and the last
// pass's failure mode was re-deriving each surface separately.
//
// A TONE HERE IS NOT A STATUS PILL. The square carries WHOSE work this is, not
// how it went, so it never takes the five status kinds. `warn` is spent on
// exactly one thing: an agent that has stopped and is waiting on the operator,
// which is the one case the panel exists to surface.
//
// WHY THE TINTED FILL RATHER THAN THE DESIGN'S SOLID ONE, for the two non-accent
// tones. The design fills the square with the status tone and writes the initial
// in white. `--accent` is the only tone in this palette with a paired foreground
// token (`--accent-fg`), and the palette contract guarantees AA for a status
// tone only as ink on its own `-soft` fill, never for white on the solid tone.
// Eight schemes x two modes means guessing here is guessing wrong somewhere, so
// the accent square is solid exactly as drawn and the others are the soft pair.

export type AvatarTone = "accent" | "warn" | "muted";

/** The initial, from whatever the agent is called. Falls back to a dot rather
 *  than an empty square: a nameless agent is still an agent. */
function initialOf(name: string | null | undefined): string {
  const c = (name || "").trim().charAt(0);
  return c ? c.toUpperCase() : "·";
}

export function AgentAvatar({ name, tone = "accent", size }: {
  name: string | null | undefined;
  tone?: AvatarTone;
  /** `sm` is a row's second line, `lg` is the question view. Default is the
   *  ticket card and the needs-you card. */
  size?: "sm" | "lg";
}) {
  const cls = [
    "chat-avatar",
    tone !== "accent" && `chat-avatar--${tone}`,
    size && `chat-avatar--${size}`,
  ].filter(Boolean).join(" ");
  // Decorative: the agent's name is written beside it in every caller, so a
  // screen reader announcing the initial would read the name twice.
  return <span className={cls} aria-hidden>{initialOf(name)}</span>;
}
