// "Build with chat" = spawn a real, freestanding builder ("Blank project N")
// and drop the operator into its chat. Unlike the old text-only planning chat,
// this builder has the full Claude Code toolset scoped to a scaffolded working
// tree — it can research the web, write files, and stand up a starter
// environment from the first turn. It appears in the Agents list like any other
// builder, and is renamed once the project takes shape.
export async function createBlankBuilder(): Promise<{ slug: string; name: string }> {
  const r = await fetch("/api/agents/build/blank", { method: "POST" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
