#!/usr/bin/env python3
"""CLI Tamper Lab: edit a reading straight in SQLite and show that verification catches it.

    python scripts/tamper.py                  # pick an anchored spike, tamper, verify
    python scripts/tamper.py --resign         # insider: also forge a valid signature
    python scripts/tamper.py --restore 123    # undo
"""

import argparse
import sqlite3

import _bootstrap  # noqa: F401

from proofofair import db
from proofofair.anchor.base import utcnow_iso
from proofofair.config import get_settings
from proofofair.verify import verify_reading

GREEN, RED, AMBER, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def show(rid: int, title: str) -> None:
    rep = verify_reading(rid)
    print(f"\n{title}")
    for c in rep.checks:
        col, mark = {"pass": (GREEN, "✓"), "fail": (RED, "✗"), "pending": (AMBER, "…")}[c.status]
        print(f"  {col}{mark} {c.step}. {c.name:<28}{RESET} {DIM}{c.detail[:96]}{RESET}")
    col = {"verified": GREEN, "tampered": RED, "pending": AMBER}[rep.status]
    print(f"  => {col}{rep.status.upper()}{RESET}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--reading", type=int, help="reading id (default: biggest anchored spike)")
    ap.add_argument("--factor", type=float, default=0.3)
    ap.add_argument("--resign", action="store_true")
    ap.add_argument("--restore", type=int, metavar="ID")
    args = ap.parse_args()

    if args.restore:
        with db.session() as conn:
            db.restore_reading(conn, args.restore)
        show(args.restore, f"Restored reading #{args.restore}:")
        return

    with db.session() as conn:
        rid = args.reading or conn.execute(
            """SELECT r.id FROM readings r JOIN batches b ON b.id = r.batch_id
               WHERE b.anchor_status = 'confirmed' ORDER BY r.pm25 DESC LIMIT 1""").fetchone()[0]
        r = db.get_reading(conn, rid)
    print(f"Reading #{rid}: {r['device_id']} at {r['ts']}, PM2.5 = {r['pm25']} µg/m³")
    show(rid, "Before:")

    new = round(r["pm25"] * args.factor, 2)
    if args.resign:
        with db.session() as conn:
            db.tamper_reading(conn, rid, new_pm25=new, resign=True, now=utcnow_iso())
        how = "UPDATE + forged signature (insider with the signing key)"
    else:
        # Exactly what a malicious admin would type — no app code involved.
        with db.session() as conn:  # keep a copy so --restore can undo it
            db.tamper_reading(conn, rid, new_pm25=r["pm25"], now=utcnow_iso())
        raw = sqlite3.connect(get_settings().db_path)
        raw.execute("UPDATE readings SET pm25 = ? WHERE id = ?", (new, rid))
        raw.commit()
        raw.close()
        how = f"UPDATE readings SET pm25 = {new} WHERE id = {rid};"
    print(f"\n{RED}Tampering:{RESET} {how}")
    show(rid, "After:")
    print(f"\nUndo with: python scripts/tamper.py --restore {rid}")


if __name__ == "__main__":
    main()
