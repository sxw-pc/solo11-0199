"""Admit booked household groups and check shared field resources."""
from collections import defaultdict

from .intervals import overlap
from .journal import reference
from .playback import span
from .scope import contained


def inspect_slots(snapshot, area, not_before, not_after):
    by_slot = defaultdict(list)
    slots = snapshot.feed("slots")
    slot_ids = {slot["id"] for slot in slots}
    for row in snapshot.feed("bookings"):
        by_slot[row["slot"]].append(row)
    results = []
    for row in slots:
        if row["cycle"] != area.policy["id"]:
            continue
        window = span(row)
        households, requests = {}, []
        for booking in by_slot[row["id"]]:
            household = booking["household"]
            checked = area.eligibility(row["site"], household, window)
            if booking["state"] == "void":
                checked["decision"] = "booking_void"
            if checked["decision"] == "eligible":
                households[household] = checked["village"]
            requests.append(dict(checked, source=reference(booking)))
        if row["state"] == "void":
            decision = "slot_void"
        elif row["start"] < not_before:
            decision = "before_dispatch_floor"
        elif not contained(window, span(area.policy)):
            decision = "outside_cycle"
        elif row["end"] > not_after:
            decision = "after_dispatch_limit"
        elif not households:
            decision = "no_eligible_bookings"
        elif len(households) > row["seats"]:
            decision = "over_capacity"
        else:
            decision = "candidate"
        results.append({"id": row["id"], "cycle": row["cycle"], "site": row["site"], "crew": row["crew"],
                        "kit": row["kit"], "start": row["start"], "end": row["end"],
                        "turnaround": row["turnaround"], "seats": row["seats"], "source": reference(row),
                        "households": households, "requests": requests, "decision": decision})
    orphans = [reference(row) for slot, rows in sorted(by_slot.items()) if slot not in slot_ids
               for row in rows if row["state"] == "active"]
    return results, orphans


def conflicts(one, other):
    reasons = []
    if overlap(span(one), span(other)):
        if one["site"] == other["site"]:
            reasons.append("site")
        if one["households"].keys() & other["households"].keys():
            reasons.append("household")
    padded = (one["start"], one["end"] + one["turnaround"])
    if overlap(padded, (other["start"], other["end"] + other["turnaround"])):
        reasons.extend(field for field in ("crew", "kit") if one[field] == other[field])
    if one["id"] == other["id"]:
        reasons.append("slot")
    return reasons
