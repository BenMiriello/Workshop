# Navigation shell — primary source material

Everything in this folder is **input to** [`../navigation-shell-2026-08-20.md`](../navigation-shell-2026-08-20.md),
not a design of record. Read the design doc first; come here when you need the exact words.

The work was done by an external design agent over three sessions (2026-08-18 to 2026-08-20) at the
operator's direction. It is design exploration against a mocked dataset. **Nothing here was built,
and several parts of it describe a system that does not exist.** The design doc's section 8 is the
list of those.

## Files

| file | what it is |
|---|---|
| `HANDOFF.md` | The design agent's own handoff, covering sessions 1 and 2. The most complete single statement of the decided shell. |
| `../../masini/TRANSCRIPT-session-1.md` | Session 1 verbatim. The original brief, the six named navigation patterns, and the turn where the three-column dock was decided. **Untracked** — see the note below. |
| `../../masini/TRANSCRIPT-session-2.md` | Session 2 verbatim. Motion, per-app memory, the mobile bar-one iterations, the branch-picker reversals. **Untracked.** |
| `../../masini/TRANSCRIPT-session-3.md` | Session 3, reconstructed from the operator's paste. The root layer, and the four-scopes answer for Activity. **Untracked.** |

> The three transcripts are raw unedited voice dictation by one operator, in his
> own voice and full of asides about his own setup. They are design INPUT, not
> platform documentation, so on 2026-08-27 they moved to `docs/masini/`, which
> `.gitignore` excludes. They are still on the instance that recorded them and
> are still the wording of record for the two shell docs; a fresh clone simply
> does not carry someone's dictation. `HANDOFF.md` below is the tracked
> derivative and is the fullest single statement of what was decided.
| `workshop-shell-mockup.dc.html` | The final mockup: an HTML template plus a logic class, inline styles only. This is the artifact the implementation is measured against. |

## Reading the mockup

`workshop-shell-mockup.dc.html` needs the design tool's `support.js` runtime to render, which is
generated and is not checked in. Two ways to see it:

1. The self-contained offline build (`Workshop Shell (standalone).html`) opens in any browser with no
   runtime. It is in the operator's chat attachment store, content-addressed:
   `.runtime/users/6/files/blobs/86/868de7a2564f1002a71f4a6693c4cbe4943276cce234e1671298b42104f2c140`
   (the zip `Workshop shell toolbar refinements.zip`, 2026-08-20).
2. Read the source directly. The `<x-dc>` block is ordinary HTML with `{{ }}` bindings, and the
   `class Component extends DCLogic` at the bottom holds every state rule. It reads fine as text and
   that is how the layout numbers in the design doc were taken.

## Artifacts that were never exported

Session 3's `Root Layer Proposals.dc.html`, its turn-5 chats-list proposal, and its turn-6b folder
options do not exist in any export. Their conclusions are carried into the design doc; their
wireframes are gone. Do not reconstruct them from memory. Ask the operator to re-export.
