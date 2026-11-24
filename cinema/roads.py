"""Rebuild a feasible road itinerary between two field screening bookings."""
from collections import defaultdict
from heapq import heappop, heappush

from .journal import Snapshot, reference


def earliest(roads, origin, destination, departure, deadline):
    exits = defaultdict(list)
    for road in roads:
        exits[road["origin"]].append(road)
    times = {origin: departure}
    steps = {}
    queue = [(departure, origin)]
    while queue:
        time, site = heappop(queue)
        if time != times[site]:
            continue
        if site == destination:
            break
        for road in exits[site]:
            leave = max(time, road["start"])
            arrive = leave + road["seconds"]
            next_site = road["destination"]
            if arrive > min(deadline, road["end"]) or arrive >= times.get(next_site, deadline + 1):
                continue
            times[next_site] = arrive
            steps[next_site] = (site, {"road": reference(road), "origin": site, "destination": next_site,
                                       "depart": leave, "arrive": arrive})
            heappush(queue, (arrive, next_site))
    if destination not in times:
        return None, []
    path = []
    current = destination
    while current != origin:
        current, step = steps[current]
        path.append(step)
    return times[destination], list(reversed(path))


def report(db, from_slot, to_slot, cutoff=None):
    if from_slot == to_slot:
        raise ValueError("trip requires two distinct screening bookings")
    snap = Snapshot(db, cutoff)
    slots = {row["id"]: row for row in snap.active("slots")}
    for slot in (from_slot, to_slot):
        if slot not in slots:
            raise ValueError(f"unknown active screening booking {slot!r} at receipt {snap.cutoff}")
    first, second = slots[from_slot], slots[to_slot]
    if first["cycle"] != second["cycle"]:
        raise ValueError("trip bookings must be in the same dispatch cycle")
    departure, deadline = first["end"], second["start"]
    arrive, path = None, []
    if departure <= deadline:
        arrive, path = earliest(snap.active("road-windows"), first["site"], second["site"], departure, deadline)
    return {"cutoff": snap.cutoff, "from": reference(first), "to": reference(second),
            "origin": first["site"], "destination": second["site"], "ready_at": departure,
            "deadline": deadline, "arrive": arrive, "path": path,
            "decision": "arrival_proven" if arrive is not None else "no_road_itinerary"}
