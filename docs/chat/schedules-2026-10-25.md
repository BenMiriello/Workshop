# Chat schedules (2026-10-25)

A schedule is a standing rule that sends a message into one chat: after each
reply, every so often, or once, from a start time, until an end time or a
number of sends. It is its own record, seen and managed in the chat. Built
because an app's long-running work (an automation that keeps going) posted
into its chat after every turn as if the operator had typed it, with its only
switch on another page.

## The record

`services/chat_schedules.py`, one file per chat beside the queue:
`.runtime/users/<owner>/chat/<thread_id>/schedules.json`.

- `text`, `label`, `repeat` (`after_reply`, `every`, `once`), `every_min`,
  `start_at`, `until_at`, `max_sends`.
- `status` (`active`, `paused`, `done`, `deleted`) and `why`, `sent` (in this
  run), `total_sent` (ever, which numbers its messages), `last_sent_at`,
  `next_at`.
- `set_by`: `{"kind": "you"}`, or `{"kind": "app", "name": <service token
  name>, "href": <a Workshop path to what set it>}`.
- `driver`: `workshop` (the platform sends it) or `app` (the app that set it
  sends each one).

A deleted schedule is kept so the messages it sent still name it. An `every`
schedule with no start sends first one interval after it is saved.

## Sending

`services/chat_schedule_sends.py`. Every send is an ordinary queue entry
(`chat_queue.enqueue`) whose sender is `schedule:<id>`, so the usage window,
resource leases, a Stop's hold and batching all apply. One waiting send is
enough: a schedule never piles messages up behind a busy chat.

- `after_reply`: at the end of each turn (`build_streaming._finalize_turn`).
  The operator's Stop pauses every after-reply schedule on that chat instead.
- `every` and `once`: the resident loop (`chat_drain.continuation_loop`) and
  the startup pass.
- `driver: app`: never sent by the platform. The app calls
  `POST /chat/threads/{id}/schedules/{sid}/send` when it decides to go on,
  and gets 409 with the reason when the operator paused or ended it.

The model's heading for such a message names the schedule and who set it
(`MESSAGE FROM ..., SENT AUTOMATICALLY`), never OPERATOR MESSAGE.

## Routes

`routes/chat_schedules.py`, under `/chat`: list everything set to send
(schedules, wake-ups, the count of messages waiting on a send rule, and the
`count` the composer's dot shows), create, change (rule or status), delete,
send now, cancel a wake-up, and for an ended one `again` (its message once
more; it stays ended) and `rearm` (start over with a rule: active, `sent` 0).
`sends` is its history, newest first, read from the transcript (messages whose
`meta.sender` is `schedule:<id>`) and the queue (the one waiting), so nothing
is stored twice. A message is numbered `<id>-<total_sent>`, never by `sent`:
the log is idempotent on that id, so a restarted run reusing one would be
dropped as already delivered. A service token (an app) makes its own and may
change only the ones it set.

## In the chat

- A dot on the Send later control while anything is set to send.
- Send later menu: Repeat (a schedule of what is typed) and Scheduled.
- Scheduled dialog (`SchedulesDialog.tsx`, list in `ScheduledList.tsx`): one
  tab per state. Upcoming (active schedules and the chat's wake-ups, soonest
  first, plus a row for messages set to send later), Paused, and Ended (ran out
  of time or sends, or ended by the operator). Rows are one fixed height; each
  has a menu of what its state allows (`actsOf` in `schedules.ts`): Send now,
  Pause or Resume, Edit, Duplicate, End, Delete; an ended one offers Run again,
  Send again, Duplicate, Delete and is never edited in place. One an app
  writes is never duplicated or sent by hand. End sets the schedule `done`
  with "ended by you", so a paused one can leave Paused without being deleted.
  Duplicate and Run again open the form filled in, any past time moved on
  (`againFormOf`). A schedule opens to its own view (`ScheduleView.tsx`):
  state, who set it and when, the message, its rule as one sentence
  (`ruleSentence`), next send with Send now, and what it has sent, each send's
  time (`ScheduleSends.tsx`); Duplicate and Delete sit in its lead menu. The form
  (`ScheduleForm.tsx`, values in `scheduleFields.ts`) picks the first send as
  "on its own clock" or "at a time", and an end.
- A message from an app's schedule (or any sender that is not the operator) is
  shown on the incoming side with the sender's name and link; one from the
  operator's own schedule stays his, marked Scheduled
  (`components/chat/schedules.ts`, `shownAs`).

## The first app on it

An app's automation runs keep going through a schedule on their chat,
created by the app's own server: labelled "Automation run #N", linked to the
app's run page, paused or ended from the chat, and the run
page's keep going switch pauses or resumes it.

## Later

When the platform can mark a message as the system's rather than the
operator's, a schedule's sends move onto that: the sender is already set.
