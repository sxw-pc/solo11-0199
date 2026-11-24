"""A reproducible walkthrough using the shipped offline field files."""
from pathlib import Path

from . import reconstruction, reservations, settlement
from .db import connect, initialize, transaction
from .formats import instant
from .journal import audit, ingest


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
FEEDS = ("cycles.csv", "targets.csv", "roster.csv", "scope.csv", "sessions.jsonl", "player.jsonl",
         "attendance.csv", "power.csv", "slots.csv", "bookings.csv")
CYCLE = "nov26"
DISPATCH = "2026-11-19T00:00:00+08:00"


def load_sample(db):
    for filename in FEEDS:
        path = EXAMPLES / filename
        ingest(db, path.stem, path.read_bytes())


def demo():
    with connect(":memory:") as db:
        initialize(db)
        with transaction(db, write=True):
            load_sample(db)
            first = reconstruction.record_run(db, "s-ridge")
            before = settlement.settle(db, CYCLE)
            dispatch = instant(DISPATCH)
            initial_plan = reservations.propose(db, CYCLE, dispatch)
            ingest(db, "power", (EXAMPLES / "power-late.csv").read_bytes())
            second = reconstruction.record_run(db, "s-ridge")
            after = settlement.settle(db, CYCLE)
            current_plan = reservations.propose(db, CYCLE, dispatch)
            committed = reservations.commit(db, current_plan["id"])
            verified = (reconstruction.replay_run(db, first["id"])["verified"]
                        and settlement.replay(db, after["id"])["verified"]
                        and reservations.replay(db, committed["id"])["verified"])
            return {
                "before": {"screening": first["report"]["decision"],
                           "credited": sorted(before["coverage"]["awards"]),
                           "ridge_gap": before["coverage"]["villages"]["ridge"]["gap"],
                           "makeup": initial_plan["report"]["selected"]},
                "after_late_power": {"screening": second["report"]["decision"],
                                     "postings": after["postings"],
                                     "ridge_gap": after["coverage"]["villages"]["ridge"]["gap"],
                                     "makeup": committed["report"]["selected"], "status": committed["status"],
                                     "forecast_ridge_gap": committed["report"]["forecast"]["ridge"]["gap"]},
                "raw_audit": audit(db), "replayed_all_three_ledgers": verified,
            }
