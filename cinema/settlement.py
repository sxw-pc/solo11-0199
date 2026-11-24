"""Append-only household entitlement postings and deterministic replay."""
import json

from .coverage import assess
from .journal import Snapshot, audit, canonical, fingerprint, reference
from .scope import ServiceArea


def keys(report):
    return {(award["village"], household) for household, award in report["awards"].items()}


def difference(before, after):
    return [(village, household, -1) for village, household in sorted(before - after)] + [
        (village, household, 1) for village, household in sorted(after - before)]


def recompute(db, cycle, cutoff):
    snap = Snapshot(db, cutoff)
    # A formal cycle can be retired. Voiding it must not strand existing postings.
    record = next((row for row in snap.feed("cycles") if row["id"] == cycle), None)
    if record is not None and record["state"] == "void":
        return {"cycle": cycle, "cutoff": snap.cutoff, "cycle_source": reference(record),
                "sessions": [], "awards": {}, "villages": {}, "decision": "cycle_void"}
    return assess(snap, ServiceArea(snap, cycle))


def payload(db, cycle, cutoff, previous):
    current = recompute(db, cycle, cutoff)
    old = recompute(db, cycle, previous["cutoff"]) if previous else {"awards": {}}
    postings = [dict(village=v, household=h, delta=d) for v, h, d in difference(keys(old), keys(current))]
    return dict(coverage=current, postings=postings, previous=previous["id"] if previous else None)


def previous_row(db, cycle, cutoff):
    return db.execute("SELECT * FROM settlement WHERE cycle=? AND cutoff<? ORDER BY cutoff DESC LIMIT 1",
                      (cycle, cutoff)).fetchone()


def recorded_postings(db, row_id):
    return [dict(row) for row in db.execute("""
        SELECT village, household, delta FROM entitlement_posting WHERE settlement=? ORDER BY delta, village, household
    """, (row_id,))]


def verify_row(db, row):
    last = previous_row(db, row["cycle"], row["cutoff"])
    result = payload(db, row["cycle"], row["cutoff"], last)
    if (row["digest"] != fingerprint(result) or row["payload"] != canonical(result)
            or row["previous"] != result["previous"] or recorded_postings(db, row["id"]) != result["postings"]):
        raise ValueError(f"settlement {row['id']} replay mismatch")
    return {"id": row["id"], "digest": row["digest"], **result}


def settle(db, cycle, cutoff=None):
    snap = Snapshot(db, cutoff)
    row = db.execute("SELECT * FROM settlement WHERE cycle=? AND cutoff=?", (cycle, snap.cutoff)).fetchone()
    if row:
        return verify_row(db, row)
    latest = db.execute("SELECT * FROM settlement WHERE cycle=? ORDER BY cutoff DESC LIMIT 1", (cycle,)).fetchone()
    if latest and latest["cutoff"] > snap.cutoff:
        raise ValueError("cannot insert an older cutoff into an already posted ledger")
    if latest:
        verify_row(db, latest)
    result = payload(db, cycle, snap.cutoff, latest)
    digest = fingerprint(result)
    run = db.execute("INSERT INTO settlement(cycle, cutoff, previous, digest, payload) VALUES (?, ?, ?, ?, ?)",
                     (cycle, snap.cutoff, result["previous"], digest, canonical(result))).lastrowid
    db.executemany("INSERT INTO entitlement_posting VALUES (?, ?, ?, ?)",
                   [(run, item["village"], item["household"], item["delta"]) for item in result["postings"]])
    return {"id": run, "digest": digest, **result}


def replay(db, row_id):
    row = db.execute("SELECT * FROM settlement WHERE id=?", (row_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown settlement {row_id}")
    audit(db, row["cutoff"])
    return {**verify_row(db, row), "verified": True}
