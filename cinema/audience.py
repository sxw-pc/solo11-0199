"""Turn observed attendance into household presence with an explicit proof boundary."""
from .intervals import duration, overlap
from .journal import reference
from .playback import span


def assess(session, records, verified, screening_ok):
    threshold = min(1200, (session["runtime"] + 1) // 2)
    accepted, observations = {}, []
    for row in records:
        seconds = duration(hit for window in verified if (hit := overlap(span(row), window)))
        if row["state"] == "void":
            decision = "void"
        elif row["channel"] == "estimate":
            decision = "estimate_only"
        elif not screening_ok:
            decision = "screening_unproven"
        elif seconds < threshold:
            decision = "insufficient_observed_playback"
        else:
            decision = "named_presence"
        observation = {"source": reference(row), "channel": row["channel"], "observer": row["observer"],
                       "household": row["household"], "count": row["count"],
                       "overlap_seconds": seconds, "observed_window": list(span(row)), "decision": decision}
        observations.append(observation)
        if decision == "named_presence":
            accepted.setdefault(row["household"], []).append(reference(row))
    return {"threshold_seconds": threshold, "observations": observations, "named_households": accepted}
