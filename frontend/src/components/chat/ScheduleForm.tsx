import { Field, Input, Notice, Segmented, Select, Stack, Textarea } from "@workshop/ui";
import type { ScheduleForm as Form } from "./scheduleFields";
import { everyText } from "./schedules";

// The fields of a schedule: what it sends, when, and within what. The dialog
// around it owns Save and Cancel; this only edits the values.

const REPEATS = [
  { value: "after_reply" as const, label: "After each reply" },
  { value: "every" as const, label: "Every" },
  { value: "once" as const, label: "Once" },
];

export default function ScheduleForm({ value, onChange, error, fromApp }: {
  value: Form;
  onChange: (next: Form) => void;
  error?: string;
  /** An app's schedule: the app writes its message and rule; only its limits are edited here. */
  fromApp?: boolean;
}) {
  const set = <K extends keyof Form>(k: K, v: Form[K]) => onChange({ ...value, [k]: v });
  const minutes = Math.round(value.every * (value.unit === "h" ? 60 : 1));
  const soon = value.repeat === "every" && minutes >= 1 ? `In ${everyText(minutes)}` : "After the next reply";
  return (
    <Stack>
      {!fromApp && (
        <>
          <Field label="Message" htmlFor="sch-text">
            <Textarea id="sch-text" rows={3} grow value={value.text}
              onChange={(e) => set("text", e.target.value)} />
          </Field>
          <Field label="Name" htmlFor="sch-label" optional>
            <Input id="sch-label" value={value.label} placeholder="Shown in the chat"
              onChange={(e) => set("label", e.target.value)} />
          </Field>
        </>
      )}
      {!fromApp && (
        <div className="chat-sched-pair">
          <Field label="When" className="chat-sched-when">
            <Segmented ariaLabel="When" options={REPEATS} value={value.repeat}
              onChange={(v) => set("repeat", v)} />
          </Field>
          {value.repeat === "every" && (
            <Field label="How often" htmlFor="sch-every">
              <div className="chat-sched-every">
                <Input id="sch-every" type="number" min={1} value={value.every}
                  onChange={(e) => set("every", Number(e.target.value))} />
                <Select aria-label="Unit" value={value.unit}
                  onChange={(e) => set("unit", e.target.value as Form["unit"])}>
                  <option value="min">minutes</option>
                  <option value="h">hours</option>
                </Select>
              </div>
            </Field>
          )}
        </div>
      )}
      {value.repeat === "once" ? (
        <Field label="At" htmlFor="sch-once">
          <Input id="sch-once" type="datetime-local" value={value.onceAt}
            onChange={(e) => set("onceAt", e.target.value)} />
        </Field>
      ) : (
        <>
          <div className="chat-sched-pair">
            <Field label="First send" htmlFor="sch-start">
              <Select id="sch-start" value={value.start}
                onChange={(e) => set("start", e.target.value as Form["start"])}>
                <option value="now">{soon}</option>
                <option value="at">At a time</option>
              </Select>
            </Field>
            {value.start === "at" && (
              <Field label="Starts at" htmlFor="sch-start-at">
                <Input id="sch-start-at" type="datetime-local" value={value.startAt}
                  onChange={(e) => set("startAt", e.target.value)} />
              </Field>
            )}
          </div>
          <div className="chat-sched-pair">
            <Field label="Ends" htmlFor="sch-end">
              <Select id="sch-end" value={value.end}
                onChange={(e) => set("end", e.target.value as Form["end"])}>
                <option value="never">When you stop it</option>
                <option value="at">At a time</option>
                <option value="count">After a number of sends</option>
              </Select>
            </Field>
            {value.end === "at" && (
              <Field label="Ends at" htmlFor="sch-until">
                <Input id="sch-until" type="datetime-local" value={value.untilAt}
                  onChange={(e) => set("untilAt", e.target.value)} />
              </Field>
            )}
            {value.end === "count" && (
              <Field label="Number of sends" htmlFor="sch-max">
                <Input id="sch-max" type="number" min={1} value={value.maxSends}
                  onChange={(e) => set("maxSends", Number(e.target.value))} />
              </Field>
            )}
          </div>
        </>
      )}
      {error && <Notice tone="error">{error}</Notice>}
    </Stack>
  );
}
