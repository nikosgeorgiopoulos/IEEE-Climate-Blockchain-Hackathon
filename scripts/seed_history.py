#!/usr/bin/env python3
"""Backfill history so the dashboard has something to show.

1. Discover real Sensor.Community sensors around Athens (live + 1-hour snapshot)
   and backfill their archive CSVs for the last N days (best effort, offline-safe).
2. Generate simulated readings on a 5-minute grid for the last N days.
3. Close batches over the history (one per --batch-hours window) and anchor them.

Re-running is safe: duplicates are ignored and only unbatched readings are batched.
"""

import argparse
import logging
from datetime import UTC, datetime, timedelta

import _bootstrap  # noqa: F401

from proofofair import batcher, db
from proofofair.config import get_settings
from proofofair.ingest import sensor_community, simulator


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--step-min", type=int, default=5, help="simulator sampling interval")
    ap.add_argument("--batch-hours", type=float, default=6, help="history batch window")
    ap.add_argument("--offline", action="store_true", help="skip Sensor.Community")
    ap.add_argument("--no-batch", action="store_true", help="leave everything unbatched")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    s = get_settings()
    db.init_db()
    db.gateway_key()
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(days=args.days)

    if s.sensor_community_enabled and not args.offline:
        print("Sensor.Community: discovering sensors around Athens…", flush=True)
        n_live = sensor_community.ingest_live()
        n_hour = sensor_community.ingest_hourly_snapshot()
        n_arch = sensor_community.backfill_archive(days=int(round(args.days)))
        print(f"  live {n_live}, 1-hour snapshot {n_hour}, archive {n_arch} readings "
              f"({len(sensor_community.load_registry())} real sensor locations)")
    else:
        print("Sensor.Community: skipped (offline)")

    with db.session() as conn:
        simulator.register_devices(conn)
        n_sim = simulator.backfill(conn, start, now, timedelta(minutes=args.step_min))
    print(f"Simulator: {n_sim} new readings from {s.sim_devices} devices "
          f"({args.days:g} days, every {args.step_min} min)")

    if not args.no_batch:
        window = timedelta(hours=args.batch_hours)
        t = datetime.fromtimestamp(
            (int(start.timestamp()) // int(window.total_seconds()) + 1) * window.total_seconds(),
            UTC)
        made = []
        while t <= now:
            bid = batcher.close_batch(until=t.strftime("%Y-%m-%dT%H:%M:%SZ"))
            if bid:
                made.append(bid)
            t += window
        # Leave the newest readings for the live scheduler, unless nothing was batched at all.
        if not made:
            bid = batcher.close_batch()
            made += [bid] if bid else []
        with db.session() as conn:
            st = conn.execute("SELECT anchor_status, COUNT(*) FROM batches GROUP BY 1").fetchall()
        print(f"Batches: closed {len(made)} via '{s.anchor_backend}' — "
              + ", ".join(f"{n} {status}" for status, n in st))

    with db.session() as conn:
        c = db.counts(conn)
    print(f"Done: {c['devices']} devices, {c['readings']} readings, {c['batches']} batches, "
          f"{c['unbatched']} waiting for the next batch.")


if __name__ == "__main__":
    main()
