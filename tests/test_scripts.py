"""Smoke tests: the CLI scripts and the scheduler run end-to-end, offline."""

import os
import subprocess
import sys
from pathlib import Path

from proofofair import db, scheduler
from proofofair.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


def run(script, *args):
    env = {**os.environ, "SENSOR_COMMUNITY_ENABLED": "false"}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


def test_gen_keys_seed_demo_tamper():
    out = run("gen_keys.py", "--device", "esp32-01")
    assert "sim-12" in out and "DEVICE_PRIVATE_KEY[32]" in out
    out = run("seed_history.py", "--days", "0.5", "--batch-hours", "2")
    assert "Simulator:" in out and "confirmed" in out
    with db.session() as conn:
        c = db.counts(conn)
    assert c["readings"] >= 12 * 140 and c["batches"] >= 5
    assert run("seed_history.py", "--days", "0.5").count("0 new readings") == 1  # idempotent

    out = run("run_demo.py", "--offline")
    assert "Tamper Lab" in out and "VERIFIED" in out

    out = run("tamper.py")
    assert "Before:" in out and "TAMPERED" in out
    rid = out.split("--restore ")[1].split()[0]
    assert "VERIFIED" in run("tamper.py", "--restore", rid)
    out = run("tamper.py", "--resign")
    assert "TAMPERED" in out


def test_scheduler_starts_and_reports_eta(monkeypatch):
    monkeypatch.setattr(scheduler, "ingest_job", lambda: None)
    db.init_db()
    assert scheduler.next_batch_eta() is None
    assert "every" in scheduler.next_batch_eta_text()
    scheduler.start()
    try:
        sec = scheduler.next_batch_in_sec()
        assert sec is not None and 0 < sec <= get_settings().batch_interval_sec
        assert scheduler.next_batch_eta_text().startswith("in ")
    finally:
        scheduler.shutdown()
    assert scheduler.next_batch_eta() is None


def test_jobs_never_raise(monkeypatch):
    db.init_db()

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler.batcher, "close_batch", boom)
    monkeypatch.setattr(scheduler.batcher, "retry_failed", boom)
    monkeypatch.setattr(scheduler.batcher, "refresh_pending", boom)
    monkeypatch.setattr(scheduler.simulator, "tick", boom)
    scheduler.batch_job()
    scheduler.retry_job()
    scheduler.refresh_job()
    scheduler.ingest_job()
