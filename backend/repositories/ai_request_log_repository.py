"""Repository layer for `AiRequestLog` -- the Admin "AI Monitoring" dashboard's data source (see
routes/admin.py's /admin/ai-monitoring endpoints and models.py's `AiRequestLog` docstring).

Mirrors complaint_repository.py's plain-function style. `record_ai_request()` is called from
`AskSarthiService.ask()` (see that module) on both the success and error paths of every
request -- it is deliberately best-effort: a logging failure here must never break (or mask the
success/failure of) the Ask Sarthi response that has already been produced by the time this is
called, so every exception is caught and logged, never re-raised.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.models import AiAlertState, AiRequestLog, User
from backend.repositories import notification_repository

logger = logging.getLogger(__name__)

# --- AI monitoring alerts -- see check_and_fire_alerts() below ---
_ALERT_WINDOW_SIZE = 20  # how many of the most recent requests to look at
_ERROR_RATE_ALERT_THRESHOLD = 0.2  # 20% of the window failing
_LATENCY_ALERT_THRESHOLD_MS = 10_000.0  # 10s average over the window
_ALERT_COOLDOWN_MINUTES = 30  # don't re-notify for the same alert type more often than this

_ALERT_TITLES = {
    "HIGH_ERROR_RATE": "High AI error rate",
    "HIGH_LATENCY": "High AI latency",
}


def record_ai_request(
    db: Session,
    *,
    request_id: str,
    langsmith_trace_id: str | None,
    phoenix_trace_id: str | None,
    conversation_id: str | None,
    intent: str | None,
    service_category: str | None,
    routed_to: str,
    success: bool,
    error_type: str | None,
    latency_ms: float,
    ai_cost_inr: float | None = None,
    ai_model_name: str | None = None,
    ai_total_tokens: int | None = None,
) -> None:
    """Persist one Ask Sarthi request's outcome. Best-effort -- see this module's docstring.

    `ai_cost_inr`/`ai_model_name`/`ai_total_tokens` default to None so every existing caller/test
    not yet passing them keeps working unchanged -- only non-None when a real Sarvam
    answer-generation LLM call happened this turn (see GraphState's own docstring)."""
    try:
        db.add(
            AiRequestLog(
                request_id=request_id,
                langsmith_trace_id=langsmith_trace_id,
                phoenix_trace_id=phoenix_trace_id,
                conversation_id=conversation_id,
                intent=intent,
                service_category=service_category,
                routed_to=routed_to,
                success=success,
                error_type=error_type,
                latency_ms=latency_ms,
                ai_cost_inr=ai_cost_inr,
                ai_model_name=ai_model_name,
                ai_total_tokens=ai_total_tokens,
            )
        )
        db.commit()
    except Exception:
        logger.warning("Could not record AiRequestLog (request_id=%s)", request_id, exc_info=True)
        db.rollback()


def add_to_ai_request_cost(db: Session, *, request_id: str, additional_cost_inr: float) -> None:
    """LIVE-REPORTED: the Admin AI Monitoring "Recent Requests" table's COST column only ever
    showed the answer-generation LLM's own cost (`record_ai_request()`'s `ai_cost_inr` param,
    set from `GraphState.ai_cost_inr` -- see that field's own docstring), silently omitting real,
    PAID costs from Sarvam's speech-to-text/text-to-speech calls that also happened in the same
    voice turn but outside the graph itself -- a citizen's voice question could genuinely cost
    money on STT+TTS alone even on a turn where no LLM call happened at all (e.g. a clarifying
    question), and the table showed a bare "--" as if nothing was spent.

    Called AFTER `record_ai_request()` has already written the row -- ask_sarthi_service.py's
    `ask_voice()` only knows the real TTS cost once synthesis finishes, which is after `_run()`
    (and its own `record_ai_request()` call) has already returned. Adds to whatever `ai_cost_inr`
    that row already has (treating an existing `None` as "no cost yet", not "definitely
    incurred") rather than overwriting it, so STT's own cost (passed into `_run()` as
    `extra_cost_inr` and folded into the row at insert time) is never lost.

    Best-effort, like `record_ai_request()`: this is on the same "never break a response that's
    already been sent to the citizen" contract, so any failure here is caught and logged, never
    raised."""
    try:
        row = db.query(AiRequestLog).filter(AiRequestLog.request_id == request_id).one_or_none()
        if row is None:
            logger.warning("Could not add to AiRequestLog cost -- no row found (request_id=%s)", request_id)
            return
        row.ai_cost_inr = (row.ai_cost_inr or 0.0) + additional_cost_inr
        db.commit()
    except Exception:
        logger.warning("Could not add to AiRequestLog cost (request_id=%s)", request_id, exc_info=True)
        db.rollback()


def get_ai_monitoring_summary(db: Session, since: datetime | None = None) -> dict:
    """Aggregate counts/rates for the Admin dashboard's top-level AI Monitoring tiles.

    Aggregated in Python rather than a SQL GROUP BY -- simpler to read and matches this
    repository's existing scale (a civic complaints app, not a high-volume analytics workload);
    revisit with a real aggregate query if `ai_request_logs` ever grows large enough for this to
    matter.
    """
    query = db.query(AiRequestLog)
    if since is not None:
        query = query.filter(AiRequestLog.created_at >= since)
    rows = query.all()

    total = len(rows)
    successful = sum(1 for r in rows if r.success)
    failed = total - successful
    average_latency_ms = (sum(r.latency_ms for r in rows) / total) if total else 0.0

    by_route: dict[str, int] = {}
    for r in rows:
        by_route[r.routed_to] = by_route.get(r.routed_to, 0) + 1

    return {
        "total_requests": total,
        "successful_requests": successful,
        "failed_requests": failed,
        "error_rate": (failed / total) if total else 0.0,
        "average_latency_ms": average_latency_ms,
        "rag_requests": by_route.get("RAG", 0),
        # Both a filed complaint and a failed-to-file attempt are "complaint requests" from an
        # AI-monitoring point of view -- COMPLAINT_CREATION_FAILED is also counted in
        # `failed_requests` above via `success`, but belongs here too so this category reflects
        # every complaint-shaped request, not just the successful ones.
        "complaint_requests": by_route.get("COMPLAINT_CREATED", 0) + by_route.get("COMPLAINT_CREATION_FAILED", 0),
        "status_requests": by_route.get("COMPLAINT_STATUS_API", 0),
        "out_of_scope_requests": by_route.get("NONE_OUT_OF_SCOPE", 0),
        # The real threshold check_and_fire_alerts() uses for "High AI latency" -- exposed so the
        # Recent Requests table (whose default page size, REQUESTS_PAGE_SIZE=20 in
        # AdminAiMonitoring.tsx, deliberately matches _ALERT_WINDOW_SIZE below) can highlight
        # exactly the requests that would trip the alert, instead of an admin having to guess or
        # duplicate this number on the frontend. LIVE-REPORTED: clicking a "High AI latency"
        # notification landed on this page with no way to tell which requests were actually slow.
        "latency_alert_threshold_ms": _LATENCY_ALERT_THRESHOLD_MS,
        "clarification_requests": by_route.get("NONE_CLARIFICATION_NEEDED", 0),
    }


def get_daily_ai_stats(db: Session, days: int = 7) -> list[dict]:
    """Per-day request volume + average latency for the Admin dashboard's AI health trend chart --
    distinct from get_ai_monitoring_summary()'s single running total, this is what lets that chart
    show a trend line instead of one number.

    Aggregated in Python like the rest of this module (see its own docstring) -- grouped by
    calendar day (UTC, since `created_at` is stored as such). Returns the most recent `days` days
    that actually had at least one request, not `days` calendar days -- a quiet stretch (a weekend,
    a maintenance window) shouldn't pad the chart with empty rows that read as an outage.
    """
    rows = db.query(AiRequestLog.created_at, AiRequestLog.latency_ms).all()
    by_day: dict[str, list[float]] = {}
    for created_at, latency_ms in rows:
        day = created_at.strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(latency_ms)

    ordered_days = sorted(by_day.keys())[-days:]
    return [
        {
            "date": day,
            "request_count": len(by_day[day]),
            "average_latency_ms": sum(by_day[day]) / len(by_day[day]),
        }
        for day in ordered_days
    ]


def get_recent_ai_requests(db: Session, limit: int = 50) -> list[AiRequestLog]:
    """Most recent requests first, for the Admin dashboard's request table / "View Trace" links.

    Orders by `id` (not just `created_at`) as the primary tiebreaker: two requests logged within
    the same millisecond -- routine under real load, and reliably reproducible in a fast test
    that seeds several rows back-to-back -- would otherwise sort in a DB-dependent, not
    insertion-consistent, order.
    """
    return db.query(AiRequestLog).order_by(AiRequestLog.id.desc()).limit(limit).all()


def get_recent_ai_requests_page(
    db: Session,
    page: int,
    page_size: int,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> tuple[list[AiRequestLog], int]:
    """Same ordering as get_recent_ai_requests() above, but with real offset/limit pagination and
    a total count -- feeds the Admin AI Monitoring page's "Recent Requests" table now that it has
    real page numbers instead of always just showing the newest N with no way to see older ones.
    get_recent_ai_requests() itself is left as the simple "first N" it always was, for callers
    that genuinely just want that (and for the existing test covering it).

    `search`: matches request_id/intent/routed_to (route) -- the three columns actually visible/
    identifying in that table's own rows, same "search the columns a user can see" contract as
    list_workers()'s own `search` (full_name/ward/phone).

    `date_from`/`date_to`: an inclusive calendar-day range over `created_at` -- `date_to` is
    treated as the END of that day (< date_to + 1 day), not midnight at its start, so picking the
    same day for both ends still returns that whole day's requests rather than none."""
    query = db.query(AiRequestLog)
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(
            or_(AiRequestLog.request_id.ilike(like), AiRequestLog.intent.ilike(like), AiRequestLog.routed_to.ilike(like))
        )
    if date_from is not None:
        query = query.filter(AiRequestLog.created_at >= date_from)
    if date_to is not None:
        query = query.filter(AiRequestLog.created_at < date_to + timedelta(days=1))
    query = query.order_by(AiRequestLog.id.desc())
    total = query.count()
    safe_page = max(page, 1)
    safe_size = max(min(page_size, 100), 1)
    rows = query.offset((safe_page - 1) * safe_size).limit(safe_size).all()
    return rows, total


def delete_ai_request_log(db: Session, log_id: int) -> bool:
    """Deletes one AiRequestLog row (Admin dashboard's "Recent Requests" table, same delete-a-row
    pattern already used for workers/complaints elsewhere in this admin area). Returns False if no
    row with that id exists, so the route can 404 instead of silently no-opping. This permanently
    removes that request from the AI Monitoring history/summary counts -- there is no undo, same
    as deleting a worker or complaint."""
    row = db.query(AiRequestLog).filter(AiRequestLog.id == log_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def check_and_fire_alerts(db: Session) -> list[str]:
    """Checks the most recent `_ALERT_WINDOW_SIZE` requests for a sustained error-rate or latency
    problem, and -- if found, and no alert of that type fired within `_ALERT_COOLDOWN_MINUTES` --
    notifies every admin via the existing in-app Notification system (see
    notification_repository.py) and records that it fired (see `AiAlertState`) so a persistent
    problem doesn't spam a fresh notification on every single request while it stays true.

    Deliberately computed from this app's own `AiRequestLog` table, not a LangSmith Automation
    webhook -- matches this file's existing "PostgreSQL is the operational source of truth, even
    for numbers that are ALSO visible in LangSmith" principle, and avoids needing an
    internet-reachable webhook endpoint for something that only needs to notify admins already
    using this app.

    Called from `AskSarthiService.ask()` right after `record_ai_request()`, on both the
    success and failure path. Best-effort like that function -- an alerting bug must never break
    the Ask Sarthi response that already completed by the time this runs.

    Returns the list of alert types that actually fired this call (usually empty) -- mainly for
    tests; callers don't need to do anything with it.
    """
    try:
        recent = db.query(AiRequestLog).order_by(AiRequestLog.id.desc()).limit(_ALERT_WINDOW_SIZE).all()
        if len(recent) < _ALERT_WINDOW_SIZE:
            return []  # not enough recent traffic yet for a meaningful rolling rate

        fired = []

        failed = sum(1 for r in recent if not r.success)
        error_rate = failed / len(recent)
        if error_rate >= _ERROR_RATE_ALERT_THRESHOLD:
            message = f"Error rate over the last {len(recent)} Ask Sarthi requests is {error_rate:.0%}."
            if _try_fire(db, "HIGH_ERROR_RATE", message):
                fired.append("HIGH_ERROR_RATE")

        avg_latency_ms = sum(r.latency_ms for r in recent) / len(recent)
        if avg_latency_ms >= _LATENCY_ALERT_THRESHOLD_MS:
            message = f"Average latency over the last {len(recent)} Ask Sarthi requests is {avg_latency_ms / 1000:.1f}s."
            if _try_fire(db, "HIGH_LATENCY", message):
                fired.append("HIGH_LATENCY")

        return fired
    except Exception:
        logger.warning("AI alert check failed", exc_info=True)
        return []


def _try_fire(db: Session, alert_type: str, message: str) -> bool:
    """Notifies every admin for `alert_type` unless it already fired within the cooldown window.
    Returns True if it actually fired this time."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_ALERT_COOLDOWN_MINUTES)
    state = db.query(AiAlertState).filter(AiAlertState.alert_type == alert_type).first()
    if state is not None:
        # SQLite round-trips a stored datetime as naive (drops the tzinfo `_utcnow()` set at
        # write time) -- Postgres wouldn't. Normalize to UTC-aware before comparing so this
        # works against either backend rather than assuming one.
        last_fired_at = state.last_fired_at
        if last_fired_at.tzinfo is None:
            last_fired_at = last_fired_at.replace(tzinfo=timezone.utc)
        if last_fired_at >= cutoff:
            return False

    admins = db.query(User).filter(User.role == "admin").all()
    for admin in admins:
        notification_repository.create_notification(
            db,
            recipient_id=admin.id,
            type="AI_ALERT",
            title=_ALERT_TITLES[alert_type],
            message=message,
            complaint_id=None,
        )

    now = datetime.now(timezone.utc)
    if state is None:
        db.add(AiAlertState(alert_type=alert_type, last_fired_at=now))
    else:
        state.last_fired_at = now
    db.commit()
    return True
