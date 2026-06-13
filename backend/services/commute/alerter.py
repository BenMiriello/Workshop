"""Alert policy + Discord dispatch.

Policy (operator-stated, never relax without operator confirmation):
    - Alert ONLY when projected arrival exceeds flex_deadline (LATE risk).
    - NEVER alert when running early or on-time.
    - One late-risk alert per (schedule_id, leg_step, day) — per-leg 24h cooldown.
    - Service-alert escalation: emergency_research_triggers in targets.json
      bypass cooldown but still go to the same Discord channel.

State file: .runtime/commute/alert-log.jsonl (append-only).
Cooldown file: .runtime/commute/alert-cooldown.json (legacy schedule-level cooldown).
Per-leg state: .runtime/commute/state/alerts.json (last_alert_per_leg dict).
Masini queue: .runtime/notify-masini.txt (plain-text, one line per alert).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import config, scoring, timefmt


COMMUTE_CHANNEL_ID = os.environ.get(
    "WORKSHOP_COMMUTE_DISCORD_CHANNEL",
    "1507029806909362237",  # operator's dedicated commute channel
)

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

MASINI_NOTIFY = Path(os.environ.get(
    "WORKSHOP_NOTIFY_MASINI",
    "/home/clawd-a/workshop/.runtime/notify-masini.txt",
))

# We may not always have a token loaded in this process; alerts will write
# to the queue regardless and the masini bridge can deliver them (see
# `notify-commute.txt` watcher pattern). The direct-Discord path is a
# convenience for environments where the token IS present (cron-time agent).


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_cooldown() -> dict[str, Any]:
    p = config.runtime_path("alert-cooldown.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _write_cooldown(data: dict[str, Any]) -> None:
    p = config.runtime_path("alert-cooldown.json")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def _append_log(record: dict[str, Any]) -> None:
    p = config.runtime_path("alert-log.jsonl")
    with p.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _on_cooldown(schedule_id: str, day_key: str, hours: int = 24) -> bool:
    """True if we already sent a late-risk alert for this (schedule_id, day) recently."""
    cd = _read_cooldown()
    key = f"{schedule_id}:{day_key}"
    last = cd.get(key)
    if not last:
        return False
    try:
        last_ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (_now() - last_ts).total_seconds() < hours * 3600


def _mark_cooldown(schedule_id: str, day_key: str) -> None:
    cd = _read_cooldown()
    cd[f"{schedule_id}:{day_key}"] = _now().isoformat()
    _write_cooldown(cd)


# --- per-leg cooldown (state/alerts.json) ---

def _read_leg_alerts() -> dict[str, Any]:
    p = config.runtime_path("state/alerts.json")
    if not p.exists():
        return {"last_alert_per_leg": {}}
    try:
        d = json.loads(p.read_text())
        if "last_alert_per_leg" not in d:
            d["last_alert_per_leg"] = {}
        return d
    except json.JSONDecodeError:
        return {"last_alert_per_leg": {}}


def _write_leg_alerts(data: dict[str, Any]) -> None:
    p = config.runtime_path("state/alerts.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def _leg_on_cooldown(schedule_id: str, day_key: str, leg_step: int | None) -> bool:
    """True if we already sent an alert for this (schedule_id, leg_step, day)."""
    if leg_step is None:
        return _on_cooldown(schedule_id, day_key)
    data = _read_leg_alerts()
    key = f"{schedule_id}:{day_key}:leg{leg_step}"
    last = data["last_alert_per_leg"].get(key)
    if not last:
        return False
    try:
        last_ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (_now() - last_ts).total_seconds() < 24 * 3600


def _mark_leg_cooldown(schedule_id: str, day_key: str, leg_step: int | None) -> None:
    if leg_step is not None:
        data = _read_leg_alerts()
        key = f"{schedule_id}:{day_key}:leg{leg_step}"
        data["last_alert_per_leg"][key] = _now().isoformat()
        _write_leg_alerts(data)
    _mark_cooldown(schedule_id, day_key)


# --- masini notify queue ---

def _queue_masini_notify(text: str, channel_id: str) -> None:
    """Append a one-line commute alert to notify-masini.txt.

    masini reads this file on next inbound Discord message and surfaces the
    alert to the operator in the specified channel.
    """
    ts = _now().isoformat()
    summary = text.replace("\n", " | ")[:300]
    line = f"[{ts}] COMMUTE-LATE channel={channel_id} {summary}\n"
    try:
        with MASINI_NOTIFY.open("a") as f:
            f.write(line)
    except OSError:
        pass  # best-effort; Discord path is the primary delivery


def _clean_reason(reason: str) -> str:
    """Strip the `LLM[checkpoint]: ` prefix and any mid-word truncation tail
    from the LLM reasoning so the alarm reads as a clean sentence, not a
    fragment ending in 'beyond the fle'."""
    if not reason:
        return ""
    # Drop a leading "LLM[...]: " tag — it's debug noise, not operator info.
    if "]:" in reason:
        reason = reason.split("]:", 1)[1].strip()
    reason = reason.strip()
    # If it was hard-truncated mid-word (no terminal punctuation), trim back to
    # the last complete sentence/word boundary so we don't show "the fle".
    if reason and reason[-1] not in ".!?":
        # Prefer the last full stop; else trim the dangling partial word.
        if "." in reason:
            reason = reason[: reason.rfind(".") + 1]
        else:
            reason = reason.rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return reason


def _format_alert_text(brief: dict[str, Any], reason: str) -> str:
    """Late-risk alarm — fires ONLY when projected arrival exceeds the flex
    deadline. Kept to four lines max: verdict, the numbers, the cause, the
    one-line reason. No timeline spray (that lives in the brief)."""
    direction = brief["direction"]
    arrow = "→" if direction == "to-work" else "←"
    where_label = "work" if direction == "to-work" else "home"
    late_by = int(brief.get("late_by_min", 0) or 0)
    lines = [
        f"**Commute late-risk** {arrow} {where_label}  ·  ⚠ {late_by}m past target",
        f"target {brief['target_arrive_local']} (flex → {brief['flex_deadline_local']})  ·  "
        f"projected **{brief['projected_arrive_local']}**",
    ]
    rt_delay = brief.get("rt_delay_min", 0)
    if rt_delay and rt_delay > 0:
        cause = f"live delay: {rt_delay:g} min"
        if brief.get("expected_leg"):
            leg = brief["expected_leg"]
            cause += f" on the {leg.get('mode')} leg ({leg.get('from')} → {leg.get('to')})"
        lines.append(cause)
    clean = _clean_reason(reason)
    if clean:
        lines.append(f"_{clean}_")
    return "\n".join(lines)


async def send_discord(text: str, channel_id: str | None = None) -> dict[str, Any]:
    """POST a message via the Discord bot REST API. Returns the parsed
    response or an error dict. Best-effort — failure does NOT raise."""
    ch = channel_id or COMMUTE_CHANNEL_ID
    if not DISCORD_TOKEN:
        # Fall back to disk queue: masini bridge tails this and forwards.
        p = config.runtime_path("notify-commute.txt")
        with p.open("a") as f:
            f.write(json.dumps({"ts": _now().isoformat(), "channel_id": ch, "text": text}) + "\n")
        return {"queued": True, "via": "notify-file"}
    url = f"https://discord.com/api/v10/channels/{ch}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(url, headers=headers, json={"content": text})
            if r.status_code >= 300:
                return {"error": f"http {r.status_code}", "body": r.text[:200]}
            return {"ok": True, "message_id": r.json().get("id")}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)[:200]}


def render_message(msg: dict[str, Any]) -> str:
    """Render a canonical commute message (messages.make_message) to Discord
    text. Same predictable shape the in-app feed shows: headline, detail, and —
    for anything that warrants a 'why' — the reasoning + action."""
    lines = [f"**{msg.get('headline', 'Commute update')}**"]
    detail = (msg.get("detail") or "").strip()
    if detail:
        lines.append(detail)
    sev = msg.get("severity", "info")
    reasoning = _clean_reason(msg.get("reasoning") or "")
    if reasoning and sev in ("caution", "urgent"):
        lines.append(f"_{reasoning}_")
    action = (msg.get("action") or "").strip()
    if action:
        lines.append(f"→ {action}")
    if msg.get("confidence") == "low":
        lines.append("_(low confidence — data is thin/stale)_")
    return "\n".join(lines)


async def dispatch_message(msg: dict[str, Any], channel_id: str | None = None) -> dict[str, Any]:
    """Send a canonical message to Discord (or the masini queue) and log it.
    Used by the reasoner-driven cron path; the message itself is already
    persisted to messages.jsonl by the reasoner."""
    text = render_message(msg)
    delivery = await send_discord(text, channel_id)
    if delivery.get("ok") or delivery.get("queued"):
        _queue_masini_notify(text, channel_id or COMMUTE_CHANNEL_ID)
    _append_log({
        "ts": _now().isoformat(),
        "kind": f"message:{msg.get('kind')}",
        "severity": msg.get("severity"),
        "message_id": msg.get("id"),
        "headline": msg.get("headline"),
        "source": msg.get("source"),
        "delivery": delivery,
    })
    return {"delivery": delivery, "text": text}


async def maybe_alert(brief: dict[str, Any] | None = None, *, force: bool = False, reason: str = "") -> dict[str, Any]:
    """Apply the alert policy. Returns a decision dict including whether we
    sent + why-not.

    `force=True` bypasses cooldown AND the location-freshness gate — used for
    emergency_research_triggers where the operator opts in to "tell me even
    if you can't see me."
    """
    brief = brief or scoring.evaluate()
    if not brief.get("active"):
        return {"sent": False, "reason": "no active schedule"}
    if not brief.get("late_risk") and not force:
        return {"sent": False, "reason": "not late risk"}
    # Location-freshness gate (2026-06-03 fix). Late-risk alerts are
    # actionable only when we have some idea where the operator actually IS.
    # When the last Owntracks ping is older than LOCATION_STALE_AFTER_S
    # (30 min) we have no way to know whether the projected delay matters —
    # the operator may have arrived early, may not be commuting today at
    # all, may have started from somewhere other than home. Suppressing
    # here is strictly better than crying wolf. `force=True` bypasses so
    # emergency_research_triggers (line shutdowns, etc.) still go out.
    loc = brief.get("location") or {}
    if not force and not loc.get("available"):
        return {
            "sent": False,
            "reason": f"location-stale gate: {loc.get('reason', 'no location signal')} — suppressing late-risk alert (use force=True to bypass)",
        }
    sid = brief.get("schedule_id", "?")
    day = timefmt.day_key()
    leg = brief.get("expected_leg")
    leg_step: int | None = int(leg["step"]) if leg and leg.get("step") is not None else None
    if not force and _leg_on_cooldown(sid, day, leg_step):
        cooldown_detail = f"leg{leg_step}" if leg_step is not None else "schedule"
        return {"sent": False, "reason": f"cooldown active for {cooldown_detail} today"}
    text = _format_alert_text(brief, reason)
    delivery = await send_discord(text)
    record = {
        "ts": _now().isoformat(),
        "schedule_id": sid,
        "day": day,
        "leg_step": leg_step,
        "late_by_min": brief.get("late_by_min"),
        "projected_arrive_local": brief.get("projected_arrive_local"),
        "delivery": delivery,
        "force": force,
        "reason": reason,
    }
    _append_log(record)
    if delivery.get("ok") or delivery.get("queued"):
        _mark_leg_cooldown(sid, day, leg_step)
        _queue_masini_notify(text, COMMUTE_CHANNEL_ID)
    return {"sent": True, **record}


def _read_latest_location() -> dict[str, Any] | None:
    """Best-effort read of the latest operator location.

    Reads via the in-process services.location module instead of an HTTP
    self-call — self-calls deadlock when invoked from inside a FastAPI request
    handler on the same event loop. Returns None if unavailable; we never
    block the brief on this.
    """
    try:
        from services import location as location_svc  # local import: avoid load-time cycle
        return location_svc.latest()
    except Exception:  # noqa: BLE001
        return None


def _fmt_minutes_until(target_iso: str | None) -> str | None:
    """Render "in N min" / "N min ago" relative to now-UTC. Returns None on parse
    failure or missing input.
    """
    if not target_iso:
        return None
    try:
        target = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta_s = (target - _now()).total_seconds()
    mins = int(round(delta_s / 60))
    if mins > 0:
        return f"in {mins} min"
    if mins < 0:
        return f"{abs(mins)} min ago"
    return "now"


def _fmt_location_line(loc: dict[str, Any] | None) -> str | None:
    """One-line location summary with staleness marker. None if no loc."""
    if not loc:
        return "your location: unknown (no recent ping)"
    ts_iso = loc.get("ts") or loc.get("client_ts")
    age_label = ""
    if ts_iso:
        try:
            ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            age_s = (_now() - ts).total_seconds()
            if age_s < 60:
                age_label = " (just now)"
            elif age_s < 600:
                age_label = f" ({int(age_s // 60)}m ago)"
            else:
                age_label = f" (stale {int(age_s // 60)}m)"
        except ValueError:
            pass
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is None or lng is None:
        return None
    return f"your location: {lat:.4f}, {lng:.4f}{age_label}"


def _action_hint(brief: dict[str, Any], checkpoint: str) -> str:
    """One-line "what to do" based on checkpoint + risk.

    Honesty rule: do NOT say "on track" when there's no realtime data — we
    only have schedule arithmetic, which is a tautology, not a prediction.
    Operator complaint (2026-05-22): saying "on track" with no live data
    while claiming knowledge we don't have is the worst failure mode.
    """
    risk = bool(brief.get("late_risk"))
    late_min = int(brief.get("late_by_min", 0) or 0)
    rt_available = bool((brief.get("realtime_status") or {}).get("available"))
    leave_by = brief.get("leave_by_local")
    if risk:
        if late_min >= 10:
            return f"action: leave now, you're {late_min}m behind target"
        return f"action: pick up the pace, {late_min}m behind target"
    if checkpoint == "pre-1h":
        msg = f"action: nothing yet"
        if leave_by:
            msg += f" — plan to leave by **{leave_by}**"
        return msg
    if checkpoint == "pre-30m":
        msg = "action: get ready to leave"
        if leave_by:
            msg += f" by **{leave_by}**"
        return msg
    if checkpoint == "during":
        leg = brief.get("current_leg") or brief.get("expected_leg") or {}
        mode = leg.get("mode", "")
        next_leg = brief.get("next_leg") or {}
        if next_leg:
            depart = next_leg.get("depart_local")
            msg = f"action: next leg ({next_leg.get('mode')}) departs **{depart}** — {next_leg.get('from')} → {next_leg.get('to')}"
            if not rt_available:
                msg += "  _(static schedule — confirm at station/app)_"
            return msg
        if mode:
            return f"action: continue {mode} leg — arrives {leg.get('arrive_local','?')}"
        return "action: continue"
    return "action: see timeline above"


def _fmt_rt_marker(rt: dict[str, Any] | None) -> str:
    """One-token tag describing this leg's realtime status."""
    if not rt:
        return "(no data)"
    if rt.get("available"):
        d = rt.get("delay_min", 0)
        if d and d > 0:
            return f"realtime +{d:g}m"
        return "realtime on-time"
    reason = rt.get("reason", "no data")
    if reason == "needs_511_key":
        return "(static — no 511 key)"
    if reason == "no_source_keys":
        return "(static — no live source)"
    if reason == "no_match":
        return "(static — no matching trip in feed)"
    if reason == "stale":
        return "(static — last poll stale)"
    return f"(static — {reason})"


def _fmt_timeline_block(brief: dict[str, Any]) -> list[str]:
    """Render the per-leg timeline as a series of Discord lines.

    Each leg gets: status-icon, step number, mode/route, from→to, depart→arrive
    with realtime marker. Current leg is bolded.
    """
    timeline = brief.get("timeline") or []
    if not timeline:
        return []
    icon = {"before": "·", "current": "▶", "past": "✓"}
    lines: list[str] = []
    for i, leg in enumerate(timeline):
        st = leg.get("status", "before")
        prefix = icon.get(st, "·")
        mode = leg.get("mode", "?")
        route = leg.get("preferred_train") or leg.get("preferred_route") or mode
        from_to = f"{leg.get('from','?')} → {leg.get('to','?')}"
        timing = f"{leg.get('depart_local')} → {leg.get('arrive_local')}"
        dur = leg.get("default_minutes", 0)
        rt_tag = _fmt_rt_marker(leg.get("realtime"))
        line = f"  {prefix} `{leg.get('step')}` {timing}  ({dur}m, {mode}) — {from_to}"
        if route and route != mode:
            line += f"  · _{route}_"
        line += f"  · {rt_tag}"
        if st == "current":
            line = "**" + line.strip() + "**"
            line = "  " + line  # preserve indent after wrap
        lines.append(line)
        # Transfer line: if this leg arrives and the NEXT leg has a different
        # mode (true transfer), show transfer window. The current static model
        # has 0-min transfers; we surface that as "tight" to be honest.
        if i < len(timeline) - 1:
            nxt = timeline[i + 1]
            if leg.get("mode") != nxt.get("mode"):
                # Real walking time isn't modeled yet — static schedule assumes
                # zero. Surface honestly so operator knows this gap is a guess.
                lines.append(
                    f"    ↳ transfer: arrives {leg.get('arrive_local')}, "
                    f"next leg departs {nxt.get('depart_local')} (gap not modeled — assumes immediate)"
                )
    return lines


def _fmt_timeline_compact(brief: dict[str, Any]) -> str | None:
    """One-line route summary: `7:10a drive › 7:20a rail › 8:20a T Third › 8:32a walk → 8:39a`.

    Replaces the old multi-line per-leg block. The operator wanted the timing
    of legs + transfers, NOT a verbose dump with a 'gap not modeled' caveat
    repeated under every transfer. Compact form keeps the legs but loses the
    spray. The leg that's "current" is wrapped in ** for emphasis.
    """
    timeline = brief.get("timeline") or []
    if not timeline:
        return None

    def _clock(s: str) -> str:
        # "7:10 AM" → "7:10a" for compactness
        return s.replace(" AM", "a").replace(" PM", "p")

    parts: list[str] = []
    for leg in timeline:
        label = leg.get("preferred_train") or leg.get("preferred_route") or leg.get("mode", "?")
        # Drop the verbose "Caltrain express ~07:33" prefix to just the mode noun
        # when no route name is set.
        depart = _clock(leg.get("depart_local", "?"))
        chunk = f"{depart} {label}"
        if leg.get("status") == "current":
            chunk = f"**{chunk}**"
        parts.append(chunk)
    arrive = _clock(timeline[-1].get("arrive_local", "?"))
    return "  ·  ".join(parts) + f"  → arrive {arrive}"


def _format_brief_text(brief: dict[str, Any], checkpoint: str) -> str:
    """Cohesive, focused commute brief — what the operator gets at
    pre-1h/pre-30m/during checkpoints regardless of late risk.

    Operator feedback (2026-05-25, message 1508458417591685130):
        > "verbose but confused in detail and format, spraying poorly
        > formatted info without focusing on a cohesive focused clear report"

    Format contract (kept tight on purpose):
        line 1: title + direction
        line 2: the DECISION — leave-by time + on-track/late verdict in ONE line
        line 3: target vs projected arrival
        line 4: compact one-line route (legs + transfers, current leg bolded)
        line 5 (optional): a single short caveat IF realtime is unavailable
        line 6 (optional): top service alert IF any, capped at one line

    No repeated "gap not modeled" lines, no multi-line 511-key banner, no raw
    lat/lng dump. Every line earns its place.
    """
    direction = brief.get("direction", "?")
    arrow = "→" if direction == "to-work" else "←"
    where_label = "work" if direction == "to-work" else "home"
    schedule_id = brief.get("schedule_id", "?")
    risk = bool(brief.get("late_risk"))
    late_by = int(brief.get("late_by_min", 0) or 0)

    label_by_cp = {
        "pre-1h": "Commute · 1h to go",
        "pre-30m": "Commute · 30m to go",
        "during": "Commute · in progress",
    }
    title = label_by_cp.get(checkpoint, "Commute")

    leave_by = brief.get("leave_by_local")
    leave_by_until = _fmt_minutes_until(brief.get("leave_by"))
    target_until = _fmt_minutes_until(brief.get("target_arrive"))

    lines = [f"**{title}** {arrow} {where_label}"]

    # Line 2 — THE DECISION. One line, leads with the actionable verb.
    if leave_by:
        verdict = "leave now" if (leave_by_until and "ago" in leave_by_until) else "leave by"
        decision = f"**{verdict} {leave_by}**"
        if leave_by_until:
            decision += f" ({leave_by_until})"
        if risk and late_by > 0:
            decision += f"  · ⚠ {late_by}m past target"
        else:
            decision += "  · on track"
        lines.append(decision)

    # Line 3 — arrival math, compact.
    arrive_line = f"target {brief['target_arrive_local']}"
    if target_until:
        arrive_line += f" ({target_until})"
    arrive_line += f"  ·  projected **{brief['projected_arrive_local']}**"
    lines.append(arrive_line)

    # Line 4 — compact route.
    route = _fmt_timeline_compact(brief)
    if route:
        lines.append(route)

    # Line 5 — ONE short caveat only when realtime is missing/stale.
    rt_summary = brief.get("realtime_status") or {}
    if not rt_summary.get("available"):
        reason = rt_summary.get("reason", "unknown")
        if reason == "needs_511_key":
            lines.append("_times are static schedule (no live transit feed configured)_")
        elif reason in ("stale", "never_polled"):
            lines.append("_times are static schedule (live feed unavailable right now)_")
        else:
            lines.append(f"_times are static schedule ({reason})_")

    # Line 6 — at most ONE service alert, and only if it likely matters.
    alerts = brief.get("alerts") or []
    if alerts:
        top = alerts[0]
        header = (top.get("header") or "").strip()
        if header:
            extra = f" (+{len(alerts) - 1} more)" if len(alerts) > 1 else ""
            lines.append(f"⚠ alert: {header}{extra}")

    return "\n".join(lines)


def _brief_cooldown_key(schedule_id: str, day_key: str, checkpoint: str) -> str:
    return f"brief:{schedule_id}:{day_key}:{checkpoint}"


def _brief_on_cooldown(schedule_id: str, day_key: str, checkpoint: str) -> bool:
    """One brief per (schedule, day, checkpoint) — prevent cron-retry duplicates."""
    cd = _read_cooldown()
    last = cd.get(_brief_cooldown_key(schedule_id, day_key, checkpoint))
    if not last:
        return False
    try:
        last_ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (_now() - last_ts).total_seconds() < 12 * 3600  # 12h is plenty


def _mark_brief_cooldown(schedule_id: str, day_key: str, checkpoint: str) -> None:
    cd = _read_cooldown()
    cd[_brief_cooldown_key(schedule_id, day_key, checkpoint)] = _now().isoformat()
    _write_cooldown(cd)


async def send_brief(brief: dict[str, Any] | None = None, *, checkpoint: str = "during",
                     force: bool = False) -> dict[str, Any]:
    """Send a status brief (not a late-risk alert) to the commute channel.

    Independent of `maybe_alert` — this fires at pre-commute checkpoints
    REGARDLESS of late risk so the operator gets proactive info (operator
    requested: "you gave me no info"). Cooldowned per (schedule, day,
    checkpoint) so cron retries don't double-send. `force=True` bypasses
    cooldown for operator-initiated runs.
    """
    brief = brief or scoring.evaluate()
    if not brief.get("active"):
        return {"sent": False, "reason": "no active schedule"}
    sid = brief.get("schedule_id", "?")
    day = timefmt.day_key()
    if not force and _brief_on_cooldown(sid, day, checkpoint):
        return {"sent": False, "reason": f"brief cooldown active for {checkpoint} today"}
    text = _format_brief_text(brief, checkpoint)
    delivery = await send_discord(text)
    record = {
        "ts": _now().isoformat(),
        "kind": "brief",
        "schedule_id": sid,
        "day": day,
        "checkpoint": checkpoint,
        "projected_arrive_local": brief.get("projected_arrive_local"),
        "late_risk": brief.get("late_risk"),
        "delivery": delivery,
        "force": force,
    }
    _append_log(record)
    if delivery.get("ok") or delivery.get("queued"):
        _mark_brief_cooldown(sid, day, checkpoint)
        _queue_masini_notify(text, COMMUTE_CHANNEL_ID)
    return {"sent": True, **record}


def recent_alerts(limit: int = 20) -> list[dict[str, Any]]:
    p = config.runtime_path("alert-log.jsonl")
    if not p.exists():
        return []
    lines = p.read_text().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
