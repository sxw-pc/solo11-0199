"""Cycle membership, roster identity and time-bounded village service agreements."""
from collections import defaultdict

from .intervals import merged
from .journal import reference
from .playback import span


def contained(inner, outer):
    return outer[0] <= inner[0] and inner[1] <= outer[1]


class ServiceArea:
    def __init__(self, snapshot, cycle):
        policy = next((row for row in snapshot.feed("cycles") if row["id"] == cycle), None)
        if policy is None or policy["state"] != "active":
            raise ValueError(f"unknown or void service cycle {cycle!r}")
        self.policy = policy
        self.targets = self.index_cycle(snapshot, "targets", "village", cycle)
        self.roster = self.index_cycle(snapshot, "roster", "household", cycle)
        self.agreements = defaultdict(list)
        for row in snapshot.feed("scope"):
            self.agreements[row["site"], row["village"]].append(row)

    @staticmethod
    def index_cycle(snapshot, feed, key, cycle):
        return {row[key]: row for row in snapshot.feed(feed) if row["cycle"] == cycle}

    def covers(self, site, village, window):
        rows = self.agreements[site, village]
        valid = any(contained(window, allowed) for allowed in merged(
            span(row) for row in rows if row["state"] == "active"))
        return valid, [reference(row) for row in rows]

    def eligibility(self, site, household, window):
        result = {"household": household, "site": site, "window": list(window), "cycle_source": reference(self.policy)}
        if not contained(window, span(self.policy)):
            return dict(result, decision="outside_cycle")
        member = self.roster.get(household)
        if member is None:
            return dict(result, decision="unknown_household")
        result.update(roster_source=reference(member), village=member["village"])
        if member["state"] != "active":
            return dict(result, decision="inactive_household")
        target = self.targets.get(member["village"])
        if target is None:
            return dict(result, decision="village_without_target")
        result["target_source"] = reference(target)
        if target["state"] != "active":
            return dict(result, decision="village_without_target")
        allowed, refs = self.covers(site, member["village"], window)
        return dict(result, decision="eligible" if allowed else "outside_service_area", scope_sources=refs)

    def village_totals(self, credited):
        counts = defaultdict(int)
        for village in credited.values():
            counts[village] += 1
        output = {}
        for village, target in sorted(self.targets.items()):
            if target["state"] == "active":
                goal = target["goal"]
                output[village] = {"goal": goal, "priority": target["priority"], "credited": counts[village],
                                   "gap": max(0, goal - counts[village]), "met": counts[village] >= goal,
                                   "source": reference(target)}
        return output
