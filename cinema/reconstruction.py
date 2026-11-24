"""Independent session verdicts and cut-bound evidence runs."""
import json
from collections import defaultdict

from . import audience, playback
from .intervals import overlap
from .journal import Snapshot, audit, canonical, fingerprint, reference


class Reconstruction:
    def __init__(self, snapshot):
        self.cutoff = snapshot.cutoff
        self.sessions = {row["id"]: row for row in snapshot.feed("sessions")}
        self.sources = {}
        for name, key in (("player", "session"), ("attendance", "session"), ("power", "feeder")):
            groups = defaultdict(list)
            for row in snapshot.feed(name):
                groups[row[key]].append(row)
            self.sources[name] = groups
        self.by_device = defaultdict(list)
        for row in self.sessions.values():
            if row["state"] == "active":
                self.by_device[row["device"]].append(row)

    def for_session(self, session_id):
        row = self.sessions.get(session_id)
        if row is None:
            raise ValueError(f"unknown session {session_id!r} at receipt {self.cutoff}")
        logs = self.sources["player"][session_id]
        power = [report for report in self.sources["power"][row["feeder"]]
                 if overlap(playback.span(report), playback.span(row))]
        collisions = [reference(other) for other in self.by_device[row["device"]]
                      if other["id"] != session_id and overlap(playback.span(row), playback.span(other))]
        result = playback.reconcile(row, logs, power)
        required = (4 * row["runtime"] + 4) // 5
        if row["state"] == "void":
            status = "void"
        elif collisions:
            status = "device_double_booked"
        elif result["media_seconds"] >= required:
            status = "proven"
        else:
            status = "insufficient_unique_media"
        report = {
            "session": session_id, "cutoff": self.cutoff, "site": row["site"], "cycle": row["cycle"],
            "window": list(playback.span(row)), "decision": status, "required_media_seconds": required,
            "source": reference(row), "player_sources": [reference(log) for log in logs],
            "power_sources": [reference(report) for report in power], "device_collisions": collisions,
            "playback": result,
            "audience": audience.assess(row, self.sources["attendance"][session_id], result["wall"], status == "proven"),
        }
        return json.loads(canonical(report))  # normalize tuples to the persisted JSON representation


def findings(report):
    return [(row["source"]["id"], row["household"], row["overlap_seconds"], row["decision"])
            for row in report["audience"]["observations"]]


def verify_run(db, row, report):
    stored = db.execute("""
        SELECT observation, household, overlap_seconds, decision FROM presence_finding
        WHERE run=? ORDER BY observation
    """, (row["id"],)).fetchall()
    if (fingerprint(report) != row["digest"] or canonical(report) != row["payload"]
            or [tuple(item) for item in stored] != findings(report)):
        raise ValueError("evidence run replay mismatch")


def record_run(db, session, cutoff=None):
    report = Reconstruction(Snapshot(db, cutoff)).for_session(session)
    digest = fingerprint(report)
    row = db.execute("SELECT * FROM evidence_run WHERE session=? AND cutoff=?", (session, report["cutoff"])).fetchone()
    if row:
        verify_run(db, row, report)
        return {"id": row["id"], "digest": digest, "report": report}
    run = db.execute("INSERT INTO evidence_run(session, cutoff, digest, payload) VALUES(?, ?, ?, ?)",
                     (session, report["cutoff"], digest, canonical(report))).lastrowid
    db.executemany("INSERT INTO presence_finding VALUES (?, ?, ?, ?, ?)",
                   [(run, *item) for item in findings(report)])
    return {"id": run, "digest": digest, "report": report}


def replay_run(db, run_id):
    row = db.execute("SELECT * FROM evidence_run WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown evidence run {run_id}")
    audit(db, row["cutoff"])
    report = Reconstruction(Snapshot(db, row["cutoff"])).for_session(row["session"])
    verify_run(db, row, report)
    return {"id": run_id, "digest": row["digest"], "report": report, "verified": True}
