// Every action taken from dictation's Actions, by voice or by click, newest
// last. Each one applies at once and can be undone, and redone, on its own row;
// the heading's Undo and Redo take the newest. A spoken "cancel" undoes the
// action said just before it (takeBacks.ts).
//
// Whether a row's Undo or Redo can run is asked of the action itself (`check`)
// when the menu opens and again just before it runs, since what it acts on (a
// chat, a draft, a model) can change in between. Actions that build on each
// other share a `chain` (sends to one chat): undoing one takes the later ones
// back with it, as going back in a chat removes everything after, and they are
// redone oldest first.

import { answered, type Said } from "./takeBacks";

export type Outcome = { ok: boolean; text: string; href?: string | null };
/** Why Undo or Redo cannot run now; absent or null when it can. */
export type Blocked = { undo?: string | null; redo?: string | null };

export type ActionSpec = {
  label: string;
  href?: string | null;
  chain?: string | null;
  undo: () => Promise<Outcome>;
  redo: () => Promise<Outcome>;
  check?: (done: boolean) => Promise<Blocked>;
};

export type ActionEntry = {
  id: number;
  label: string;
  href: string | null;
  chain: string | null;
  /** Stream sample where the spoken command ended; null from a click. */
  at: number | null;
  /** When it applied, and how long after the command was heard. */
  made: number;
  lag: number;
  done: boolean;
  busy: boolean;
  blocked: { undo: string | null; redo: string | null };
  /** When it was last undone, for the heading's Redo. */
  undoneAt: number;
};

const KEEP = 50;
const EARLIER_FIRST = "Redo the earlier one first";

export class ActionHistory {
  private list: ActionEntry[] = [];
  private specs = new Map<number, ActionSpec>();
  private listeners = new Set<() => void>();
  private next = 0;

  get entries(): readonly ActionEntry[] { return this.list; }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  };

  record(spec: ActionSpec, said: Said | null, now = Date.now()): ActionEntry {
    const e: ActionEntry = {
      id: ++this.next, label: spec.label, href: spec.href ?? null, chain: spec.chain ?? null,
      at: said?.to ?? null, made: now, lag: said ? Math.max(0, now - said.heard) : 0, done: true, busy: false, blocked: { undo: null, redo: null }, undoneAt: 0,
    };
    this.specs.set(e.id, spec);
    this.list = [...this.list, e];
    if (this.list.length > KEEP) {
      this.list.slice(0, this.list.length - KEEP).forEach((x) => this.specs.delete(x.id));
      this.list = this.list.slice(-KEEP);
    }
    this.emit();
    return e;
  }

  /** The action now goes by another name or is seen somewhere else (a new
   *  chat's first send made its conversation). */
  update(id: number, patch: Partial<Pick<ActionEntry, "label" | "href" | "chain">>): void {
    this.patch(id, patch);
  }

  /** The newest action that can be undone now. */
  newestUndo(): ActionEntry | null {
    return [...this.list].reverse().find((e) => e.done && !e.busy && !e.blocked.undo) ?? null;
  }

  /** The action undone most recently that can be redone now. */
  newestRedo(): ActionEntry | null {
    const can = this.list.filter((e) => !e.done && !e.busy && !e.blocked.redo);
    return can.reduce<ActionEntry | null>((a, e) => (!a || e.undoneAt > a.undoneAt ? e : a), null);
  }

  /** The action a spoken "cancel" heard at stream sample `from` undoes. */
  answer(from: number, heardAt = Date.now()): ActionEntry | null {
    return answered(this.list.filter((e) => e.done && !e.busy), { from, heardAt });
  }

  undo(id: number): Promise<Outcome> {
    return this.run(id, true);
  }

  redo(id: number): Promise<Outcome> {
    return this.run(id, false);
  }

  /** Ask every action whether it can be undone or redone now. */
  async refresh(): Promise<void> {
    await Promise.all(this.list.map((e) => this.recheck(e.id)));
  }

  private async run(id: number, undo: boolean): Promise<Outcome> {
    const e = this.find(id);
    const spec = this.specs.get(id);
    if (!e || !spec || e.busy || e.done !== undo) return { ok: false, text: "That already happened." };
    const why = (await this.recheck(id))[undo ? "undo" : "redo"];
    if (why) return { ok: false, text: why };
    this.patch(id, { busy: true });
    let out: Outcome;
    try {
      out = await (undo ? spec.undo() : spec.redo());
    } catch (err) {
      out = { ok: false, text: err instanceof Error ? err.message : String(err) };
    }
    this.patch(id, { busy: false });
    if (out.ok) this.settle(id, !undo);
    void this.refresh();
    return out;
  }

  // Undone: it and every later action on its chain. Redone: it alone.
  private settle(id: number, done: boolean): void {
    const e = this.find(id)!;
    const now = Date.now();
    this.list = this.list.map((x) => {
      const later = !done && x.made > e.made && !!e.chain && x.chain === e.chain && x.done;
      if (x.id !== id && !later) return x;
      return { ...x, done, undoneAt: done ? x.undoneAt : now };
    });
    this.emit();
  }

  private async recheck(id: number): Promise<{ undo: string | null; redo: string | null }> {
    const e = this.find(id);
    const spec = this.specs.get(id);
    if (!e || !spec) return { undo: null, redo: null };
    const asked: Blocked = await spec.check?.(e.done).catch(() => ({})) ?? {};
    const cur = this.find(id) ?? e;
    const earlier = !!cur.chain && this.list.some((x) => x.chain === cur.chain && x.made < cur.made && !x.done);
    const blocked = { undo: asked.undo ?? null, redo: earlier ? EARLIER_FIRST : asked.redo ?? null };
    if (blocked.undo !== cur.blocked.undo || blocked.redo !== cur.blocked.redo) this.patch(id, { blocked });
    return blocked;
  }

  private find(id: number): ActionEntry | undefined {
    return this.list.find((e) => e.id === id);
  }

  private patch(id: number, p: Partial<ActionEntry>): void {
    if (!this.find(id)) return;
    this.list = this.list.map((e) => (e.id === id ? { ...e, ...p } : e));
    this.emit();
  }

  private emit(): void {
    this.listeners.forEach((l) => l());
  }
}

export const actionHistory = new ActionHistory();
