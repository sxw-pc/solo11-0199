"""Reconcile player positions with conflicting logs and confirmed loss of power."""
from .intervals import duration, merged, overlap, partition


def span(row):
    return row["start"], row["end"]


def outages(session, reports):
    tagged = []
    for row in reports:
        if row["state"] == "active" and (window := overlap(span(session), span(row))):
            tagged.append((window, (row["channel"], row["observer"])))
    confirmed, advisory = [], []
    for window, sources in partition(tagged):
        dispatchers = {observer for channel, observer in sources if channel == "dispatcher"}
        electricians = {observer for channel, observer in sources if channel == "electrician"}
        if any(channel == "meter" for channel, _ in sources) or (
            dispatchers and electricians and len(dispatchers | electricians) >= 2
        ):
            confirmed.append(window)
        else:
            advisory.append(window)
    return merged(confirmed), merged(advisory)


def reconcile(session, logs, power):
    confirmed, advisory = outages(session, power)
    tagged = [(window, ("power", 0)) for window in confirmed]
    excluded = []
    for row in logs:
        if row["state"] == "void" or row["device"] != session["device"]:
            excluded.append({"id": row["id"], "reason": "void" if row["state"] == "void" else "wrong_device"})
            continue
        claimed = overlap(span(session), span(row))
        if not claimed:
            excluded.append({"id": row["id"], "reason": "outside_session"})
            continue
        bias = row["offset"] - row["start"]
        if row["film"] != session["film"]:
            tagged.append((claimed, ("other_film", 0)))
            excluded.append({"id": row["id"], "reason": "wrong_film"})
            continue
        media_window = overlap(claimed, (-bias, session["runtime"] - bias))
        if media_window != claimed:
            excluded.append({"id": row["id"], "reason": "outside_film_runtime"})
        if media_window:
            tagged.append((media_window, ("film", bias)))
    wall, media, conflicts = [], [], []
    for (start, end), tags in partition(tagged):
        offsets = [offset for kind, offset in tags if kind == "film"]
        if len(offsets) > 1 or (offsets and ("other_film", 0) in tags):
            conflicts.append((start, end))
        elif offsets and ("power", 0) not in tags:
            wall.append((start, end))
            media.append((start + offsets[0], end + offsets[0]))
    wall, media = merged(wall), merged(media)
    return {
        "wall": wall, "media": media, "wall_seconds": duration(wall), "media_seconds": duration(media),
        "confirmed_outage": confirmed, "advisory_outage": advisory, "player_conflict": merged(conflicts),
        "excluded_logs": excluded,
    }
