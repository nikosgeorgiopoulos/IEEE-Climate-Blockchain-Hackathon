"""Background jobs (APScheduler): ingest, batch + anchor, anchor retries, OTS upgrades."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from . import batcher, db
from .config import get_settings
from .ingest import sensor_community, simulator

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None
_first_sc_fetch_done = threading.Event()


def ingest_job() -> None:
    s = get_settings()
    if s.sensor_community_enabled:
        try:
            if not _first_sc_fetch_done.is_set():
                _first_sc_fetch_done.set()
                n = sensor_community.ingest_hourly_snapshot()
                log.info("Sensor.Community 1h snapshot: %s readings", n)
            n = sensor_community.ingest_live()
            log.info("Sensor.Community live: %s readings", n)
        except Exception as e:  # never crash the scheduler on network/parse errors
            log.warning("Sensor.Community ingest failed: %s", e)
    try:
        with db.session() as conn:
            n = simulator.tick(conn)
        log.info("simulator tick: %s readings", n)
    except Exception as e:
        log.exception("simulator tick failed: %s", e)


def batch_job() -> None:
    try:
        batcher.close_batch()
    except Exception as e:
        log.exception("close_batch failed: %s", e)


def retry_job() -> None:
    try:
        batcher.retry_failed()
    except Exception as e:
        log.exception("retry_failed failed: %s", e)


def refresh_job() -> None:
    try:
        batcher.refresh_pending()
    except Exception as e:
        log.exception("refresh_pending failed: %s", e)


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    s = get_settings()
    now = datetime.now(UTC)
    sched = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True,
                                                               "max_instances": 1})
    sched.add_job(ingest_job, "interval", seconds=s.ingest_interval_sec, id="ingest",
                  next_run_time=now + timedelta(seconds=2))
    sched.add_job(batch_job, "interval", seconds=s.batch_interval_sec, id="batch")
    sched.add_job(retry_job, "interval", minutes=30, id="retry_failed")
    # OTS needs hourly upgrades; polygon may leave a tx "submitted" if the receipt was slow.
    sched.add_job(refresh_job, "interval", minutes=60 if s.anchor_backend == "ots" else 10,
                  id="refresh_pending")
    sched.start()
    _scheduler = sched
    log.info("scheduler started (ingest %ss, batch %ss, backend %s)",
             s.ingest_interval_sec, s.batch_interval_sec, s.anchor_backend)
    return sched


def shutdown() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def next_batch_eta() -> datetime | None:
    if _scheduler and _scheduler.running:
        job = _scheduler.get_job("batch")
        if job and job.next_run_time:
            return job.next_run_time.astimezone(UTC)
    return None


def next_batch_in_sec() -> int | None:
    eta = next_batch_eta()
    return None if eta is None else max(0, int((eta - datetime.now(UTC)).total_seconds()))


def next_batch_eta_text() -> str:
    sec = next_batch_in_sec()
    if sec is None:
        return f"every {max(1, get_settings().batch_interval_sec // 60)} min"
    return f"in {sec // 60} min {sec % 60:02d} s" if sec >= 60 else f"in {sec} s"
