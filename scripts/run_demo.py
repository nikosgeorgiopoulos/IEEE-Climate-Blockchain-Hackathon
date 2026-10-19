#!/usr/bin/env python3
"""One-shot demo prep: seed (if needed) → fresh readings → batch now → verify → print URLs."""

import argparse
import subprocess
import sys

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

from proofofair import batcher, db
from proofofair.config import get_settings
from proofofair.ingest import simulator
from proofofair.verify import verify_reading


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--offline", action="store_true", help="skip Sensor.Community while seeding")
    args = ap.parse_args()
    s = get_settings()
    db.init_db()
    with db.session() as conn:
        have = db.counts(conn)["readings"]
    if have < 100:
        cmd = [sys.executable, str(ROOT / "scripts" / "seed_history.py")]
        subprocess.run(cmd + (["--offline"] if args.offline else []), check=True)
    with db.session() as conn:
        simulator.tick(conn)
    bid = batcher.close_batch()
    with db.session() as conn:
        b = db.get_batch(conn, bid) if bid else conn.execute(
            "SELECT * FROM batches ORDER BY id DESC LIMIT 1").fetchone()
        spike = conn.execute(
            """SELECT r.id, r.device_id, r.pm25 FROM readings r JOIN batches b ON b.id = r.batch_id
               WHERE b.anchor_status = 'confirmed' AND r.device_id = 'sim-07'
               ORDER BY r.pm25 DESC LIMIT 1""").fetchone()
        any_r = conn.execute("SELECT id FROM readings WHERE batch_id = ? LIMIT 1",
                             (b["id"],)).fetchone()
    rep = verify_reading(any_r["id"])
    marks = " ".join("✓" if c.ok else ("…" if c.status == "pending" else "✗") for c in rep.checks)
    u = args.base_url.rstrip("/")
    print(f"\nBatch #{b['id']}: {b['n_leaves']} readings, root {b['merkle_root_hex'][:16]}…, "
          f"{s.anchor_backend} status {b['anchor_status']}"
          + (f", block {b['anchor_block']}" if b["anchor_block"] else ""))
    if b["anchor_url"]:
        link = b["anchor_url"] if b["anchor_url"].startswith("http") else u + b["anchor_url"]
        print(f"  anchor: {link}")
    print(f"Verify reading #{rep.reading_id}: {marks}  -> {rep.status.upper()}")
    print("\nOpen these (start the server with `make run` first):")
    print(f"  Live map      {u}/")
    print(f"  Sensor        {u}/sensor.html?id=sim-07")
    print(f"  Batches       {u}/batches.html")
    if spike:
        print(f"  Tamper Lab    {u}/tamper.html?device=sim-07&reading={spike['id']}"
              f"   (the {spike['pm25']:.1f} µg/m³ spike)")
    print(f"  Verify API    {u}/api/readings/{rep.reading_id}/verify")


if __name__ == "__main__":
    main()
