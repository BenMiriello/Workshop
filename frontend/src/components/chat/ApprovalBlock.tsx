import { useState } from "react";
import { Check, ShieldAlert, X } from "lucide-react";
import { Button, Markdown, Textarea } from "../../ui";
import type { ChatSegment } from "../../lib/chat";

// The turn has stopped and is asking. Rendered INLINE in the log, under the
// message that caused it — not in the strip above the transcript, which is for
// things pointing OUT of the chat. A permission request is the most in-chat
// thing there is, and it is the only control that can restart the run.
//
// Not a ui-card. Those are model-authored fences parsed out of message text
// against a fixed kind registry; this is a runtime event carrying a request_id
// that must be answered exactly once, that blocks a live process, and that goes
// stale when that process dies. Routing it through message-text parsing would
// make de-duplication and staleness accidental properties of string handling.

type Approval = Extract<ChatSegment, { t: "approval" }>;

/** The mode the CLI itself proposed, if any. This is what "stop asking" means:
 *  the vendor's own suggestion, not a policy of ours. */
function suggestedMode(seg: Approval): string | null {
  for (const s of seg.suggestions || []) {
    if (s && s.type === "setMode" && s.mode) return s.mode;
  }
  return null;
}

/** Plan approvals read as a plan, not as a tool call with an argument blob. */
function planText(seg: Approval): string | null {
  if (seg.tool !== "ExitPlanMode") return null;
  const plan = seg.input?.plan;
  return typeof plan === "string" && plan.trim() ? plan : null;
}

export default function ApprovalBlock({
  seg, onAnswer,
}: {
  seg: Approval;
  onAnswer: (a: { requestId: string; allow: boolean; message?: string; applySuggestion?: boolean }) => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [denying, setDenying] = useState(false);
  const [reason, setReason] = useState("");
  const plan = planText(seg);
  const suggestion = suggestedMode(seg);

  if (seg.state !== "pending") {
    // A decision, kept in place so the transcript still reads as what happened.
    // Quiet: it is history now, and history should not shout at the operator.
    const said =
      seg.state === "allowed" ? "Allowed"
        : seg.state === "denied" ? "Denied"
          : "Expired when the turn ended";
    return (
      <div className="chat-approval is-resolved">
        {seg.state === "allowed"
          ? <Check size={14} aria-hidden className="chat-approval__icon" />
          : <X size={14} aria-hidden className="chat-approval__icon" />}
        <span className="chat-approval__resolved">
          {said} · {plan ? "plan" : seg.label}
          {seg.detail && !plan ? ` ${seg.detail}` : ""}
        </span>
      </div>
    );
  }

  const run = async (allow: boolean, applySuggestion = false) => {
    // Which BUTTON is busy, not which effect it has: approving a plan applies a
    // suggestion too, but it is the primary button that has to show the spinner.
    const key = !allow ? "deny" : (applySuggestion && !plan ? "always" : "allow");
    setBusy(key);
    try {
      await onAnswer({
        requestId: seg.id,
        allow,
        message: allow ? undefined : (reason.trim() || undefined),
        applySuggestion,
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="chat-approval">
      <div className="chat-approval__head">
        <ShieldAlert size={14} aria-hidden className="chat-approval__icon" />
        <span className="chat-approval__title">
          {plan ? "Ready to start on this plan" : `Asking to run ${seg.label}`}
        </span>
        {seg.detail && !plan && <span className="chat-approval__detail">{seg.detail}</span>}
      </div>

      {plan && <div className="chat-approval__plan"><Markdown>{plan}</Markdown></div>}

      {denying && (
        <Textarea
          className="chat-approval__reason"
          value={reason}
          rows={2}
          autoFocus
          // The model READS this. "Not that file, use the other one" steers the
          // next attempt; a bare refusal leaves it guessing what went wrong.
          placeholder="Why not? (optional — the model reads this)"
          onChange={(e) => setReason(e.target.value)}
        />
      )}

      <div className="chat-approval__actions">
        <Button
          size="sm"
          variant="primary"
          loading={busy === "allow"}
          disabled={busy != null}
          // Approving a plan also LEAVES plan mode. Without that the chat has
          // just been told to go ahead and still cannot edit anything, which
          // reads as the approval not having worked.
          onClick={() => void run(true, Boolean(plan))}
        >
          {plan ? "Approve plan" : "Allow"}
        </Button>
        {suggestion && !plan && (
          <Button
            size="sm"
            loading={busy === "always"}
            disabled={busy != null}
            onClick={() => void run(true, true)}
          >
            Allow and stop asking
          </Button>
        )}
        {denying ? (
          <Button
            size="sm"
            variant="danger"
            loading={busy === "deny"}
            disabled={busy != null}
            onClick={() => void run(false)}
          >
            Send refusal
          </Button>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy != null}
            onClick={() => setDenying(true)}
          >
            {plan ? "Keep planning" : "Deny"}
          </Button>
        )}
      </div>
    </div>
  );
}
