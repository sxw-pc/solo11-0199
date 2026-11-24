"""Choose a feasible portfolio by marginal public-service coverage."""
from .capacity import conflicts, inspect_slots
from .coverage import assess
from .journal import fingerprint
from .playback import span
from .scope import ServiceArea


MAX_CANDIDATES = 12


def projection(area, covered, portfolio):
    combined = dict(covered)
    for slot in portfolio:
        combined.update(slot["households"])
    return area.village_totals(combined)


def optimize(area, covered, candidates):
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError(f"more than {MAX_CANDIDATES} admissible slots; narrow --from/--until")
    baseline = area.village_totals(covered)
    # Precompute conflicts once. Search all feasible subsets through ascending slot IDs.
    incompatible = {item["id"]: {other["id"] for other in candidates if conflicts(item, other)}
                    for item in candidates}
    best_score, best_ids, best = (0, 0, 0), (), []

    def search(index, chosen, blocked, credited):
        nonlocal best_score, best_ids, best
        totals = area.village_totals(credited)
        remote_closed = sum(old["priority"] == "remote" and old["gap"] > 0 and totals[village]["gap"] == 0
                            for village, old in baseline.items())
        value = sum((2 if old["priority"] == "remote" else 1) * (old["gap"] - totals[village]["gap"])
                    for village, old in baseline.items())
        score = (remote_closed, value, -len(chosen))
        ids = tuple(item["id"] for item in chosen)
        if score > best_score or (score == best_score and ids < best_ids):
            best_score, best_ids, best = score, ids, chosen
        for position in range(index, len(candidates)):
            item = candidates[position]
            if item["id"] not in blocked:
                search(position + 1, [*chosen, item], blocked | incompatible[item["id"]],
                       credited | item["households"])

    search(0, [], set(), covered)
    return best, list(best_score)


def build(snapshot, cycle, not_before, not_after, holds):
    area = ServiceArea(snapshot, cycle)
    coverage = assess(snapshot, area)
    actual = {household: award["village"] for household, award in coverage["awards"].items()}
    projected = dict(actual)
    held_assessments = []
    for hold in holds:
        if hold["cycle"] != cycle or hold["start"] < not_before:
            continue
        # Physical locks remain frozen. Only current eligible households reduce forecast gaps.
        for household in hold["households"]:
            check = area.eligibility(hold["site"], household, span(hold))
            held_assessments.append(dict(check, proposal=hold["proposal"], slot=hold["id"]))
            if check["decision"] == "eligible":
                projected[household] = check["village"]
    inspected, orphan_bookings = inspect_slots(snapshot, area, not_before, not_after)
    candidates = []
    for slot in inspected:
        if slot["decision"] != "candidate":
            continue
        blocked_by = [{"proposal": hold["proposal"], "slot": hold["id"], "resources": reasons}
                      for hold in holds if (reasons := conflicts(slot, hold))]
        if blocked_by:
            slot.update(decision="held_conflict", blocked_by=blocked_by)
        else:
            candidates.append(slot)
    selected, score = optimize(area, projected, candidates)
    selected_ids = [slot["id"] for slot in selected]
    omitted = {}
    for candidate in candidates:
        if candidate["id"] in selected_ids:
            continue
        reasons = {slot["id"]: reasons for slot in selected if (reasons := conflicts(candidate, slot))}
        omitted[candidate["id"]] = {"decision": "portfolio_conflict" if reasons else "no_additional_gap_reduction",
                                    "conflicts": reasons}
    return {"cycle": cycle, "cutoff": snapshot.cutoff, "not_before": not_before, "not_after": not_after,
            "coverage_digest": fingerprint(coverage), "actual_villages": coverage["villages"],
            "held_baseline": area.village_totals(projected), "forecast": projection(area, projected, selected),
            "held_slots": holds, "held_assessments": held_assessments, "slots": inspected,
            "orphan_bookings": orphan_bookings, "selected": selected_ids, "omitted": omitted, "score": score}
