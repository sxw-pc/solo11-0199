"""Persist forecast proposals and atomically acquire or release reservations."""
import json

from .journal import Snapshot, audit, canonical, fingerprint, watermark
from .planning import build
from .scope import ServiceArea


def active_holds(db):
    return [dict(json.loads(row["payload"]), proposal=row["proposal"]) for row in db.execute(
        "SELECT * FROM reservation WHERE active=1 ORDER BY proposal, slot")]


def get(db, proposal):
    row = db.execute("SELECT * FROM proposal WHERE id=?", (proposal,)).fetchone()
    if row is None:
        raise ValueError(f"unknown proposal {proposal}")
    return row


def response(row, report):
    return {"id": row["id"], "status": row["status"], "digest": row["digest"], "report": report}


def propose(db, cycle, not_before, not_after=None):
    snap = Snapshot(db)
    if not_after is None:
        not_after = ServiceArea(snap, cycle).policy["end"]
    if not_after <= not_before:
        raise ValueError("dispatch end must follow dispatch start")
    holds = active_holds(db)
    row = db.execute("""
        SELECT * FROM proposal WHERE cycle=? AND cutoff=? AND not_before=? AND not_after=? AND holds=? AND status<>'cancelled'
    """, (cycle, snap.cutoff, not_before, not_after, canonical(holds))).fetchone()
    if row:
        return response(row, verify(db, row))
    report = build(snap, cycle, not_before, not_after, holds)
    new_id = db.execute("""
        INSERT INTO proposal(cycle, cutoff, not_before, not_after, holds, digest, payload, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed')
    """, (cycle, snap.cutoff, not_before, not_after, canonical(holds), fingerprint(report), canonical(report))).lastrowid
    db.execute("INSERT INTO proposal_event VALUES (?, 'proposed')", (new_id,))
    return response(get(db, new_id), report)


def selected_slots(report):
    selected = set(report["selected"])
    return [slot for slot in report["slots"] if slot["id"] in selected]


def verify(db, row):
    holds = json.loads(row["holds"])
    for hold in holds:
        source = db.execute("SELECT payload FROM reservation WHERE proposal=? AND slot=?",
                            (hold["proposal"], hold["id"])).fetchone()
        if source is None or json.loads(source["payload"]) != {k: v for k, v in hold.items() if k != "proposal"}:
            raise ValueError(f"proposal {row['id']} frozen hold mismatch")
    report = build(Snapshot(db, row["cutoff"]), row["cycle"], row["not_before"], row["not_after"], holds)
    if fingerprint(report) != row["digest"] or canonical(report) != row["payload"]:
        raise ValueError(f"proposal {row['id']} replay mismatch")
    events = {item[0] for item in db.execute("SELECT status FROM proposal_event WHERE proposal=?", (row["id"],))}
    valid = {"proposed": [{"proposed"}], "committed": [{"proposed", "committed"}],
             "cancelled": [{"proposed", "cancelled"}, {"proposed", "committed", "cancelled"}]}
    if events not in valid[row["status"]]:
        raise ValueError(f"proposal {row['id']} transition history mismatch")
    expected = [(slot["id"], canonical(slot), int(row["status"] == "committed"))
                for slot in selected_slots(report)] if "committed" in events else []
    reservations = db.execute("SELECT slot, payload, active FROM reservation WHERE proposal=? ORDER BY slot",
                              (row["id"],)).fetchall()
    if expected != [tuple(item) for item in reservations]:
        raise ValueError(f"proposal {row['id']} reservations mismatch")
    return report


def move(db, row_id, status):
    db.execute("UPDATE proposal SET status=? WHERE id=?", (status, row_id))
    db.execute("INSERT INTO proposal_event VALUES (?, ?)", (row_id, status))


def commit(db, row_id):
    row = get(db, row_id)
    if row["status"] == "cancelled":
        raise ValueError("cancelled proposal cannot be committed")
    if row["status"] == "proposed" and (row["cutoff"] != watermark(db)
                                       or row["holds"] != canonical(active_holds(db))):
        raise ValueError("stale proposal: receipts or active reservations changed; propose again")
    report = verify(db, row)
    if row["status"] == "proposed":
        if not report["selected"]:
            raise ValueError("empty proposal cannot reserve resources")
        db.executemany("INSERT INTO reservation VALUES (?, ?, ?, 1)",
                       [(row_id, slot["id"], canonical(slot)) for slot in selected_slots(report)])
        move(db, row_id, "committed")
    return response(get(db, row_id), report)


def cancel(db, row_id):
    row = get(db, row_id)
    report = verify(db, row)
    if row["status"] != "cancelled":
        db.execute("UPDATE reservation SET active=0 WHERE proposal=?", (row_id,))
        move(db, row_id, "cancelled")
    return response(get(db, row_id), report)


def replay(db, row_id):
    row = get(db, row_id)
    audit(db, row["cutoff"])
    return {**response(row, verify(db, row)), "verified": True}
