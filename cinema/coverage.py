"""Attribute each eligible household once per cycle while preserving alternate proofs."""
from .journal import fingerprint, reference
from .reconstruction import Reconstruction


def assess(snapshot, area):
    cycle = area.policy["id"]
    reconstruction = Reconstruction(snapshot)
    sessions, awards = [], {}
    for row in sorted(reconstruction.sessions.values(), key=lambda item: (item["start"], item["id"])):
        if row["cycle"] != cycle:
            continue
        evidence = reconstruction.for_session(row["id"])
        attendance = []
        for household, refs in sorted(evidence["audience"]["named_households"].items()):
            attempt = area.eligibility(row["site"], household, evidence["window"])
            attempt["attendance_sources"] = refs
            if attempt["decision"] == "eligible" and household in awards:
                attempt.update(decision="repeat_household", credited_session=awards[household]["session"])
            elif attempt["decision"] == "eligible":
                awards[household] = {"session": row["id"], "village": attempt["village"], "proof": attempt}
            attendance.append(attempt)
        sessions.append({"session": row["id"], "decision": evidence["decision"], "proof_digest": fingerprint(evidence),
                         "source": reference(row), "attendance": attendance, "evidence": evidence})
    villages = area.village_totals({household: award["village"] for household, award in awards.items()})
    return {"cycle": cycle, "cutoff": snapshot.cutoff, "cycle_source": reference(area.policy),
            "sessions": sessions, "awards": awards, "villages": villages}
