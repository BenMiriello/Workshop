<!-- author: derived:claude-sonnet-5 -->
CORRECTED 2026-08-14. This note used to read "Chat composer input retention
fixed via clearNow; watch for recurrence on mobile with IME events". That was
wrong in a way that cost real time, so the original wording is kept above only
so it is recognisable.

`clearNow` no longer exists anywhere in `frontend/src`. It was introduced by
`a7faffa` (2026-07-31) and deleted by `76f9c66` (2026-08-12). Because this note
said the bug was already fixed, and named the fix, every later agent read
"solved" and went looking somewhere else.

WHAT IS ACTUALLY TRUE. The operator reported "text stays in the box after I
send" eight times between 2026-07-31 and 2026-08-14. Five separate fixes were
shipped (`a7faffa`, `ec4db15`, `76f9c66`, `76529f2`, `37efe27`). Every one of
them was verified by READING the source, the source read correct every time,
and the symptom kept coming back. There was never a test.

WHAT TO DO IF IT IS REPORTED AGAIN. Run `bash scripts/smoke_chat.sh`. It drives
the built bundle in a real browser and asserts the invariant rather than a code
path: after a send the box is empty and STAYS empty, past the editing window,
across a refocus, across a reload, in a second tab, and on the first message of
a new chat. Those are the five ways it has actually come back. It intercepts and
fails the send on purpose, so the box must empty because send was pressed rather
than because the server agreed, and it writes no message into a real
conversation. It exits 3 when it cannot run and never reports that as a pass.

If the symptom is real and that script is green, the script is wrong and that is
the thing to fix first.

Do not add a sixth fix on the strength of reading the source. That is the move
that has failed five times.

Migrated 2026-08-14 from chat-distilled; originating chat not recorded.
