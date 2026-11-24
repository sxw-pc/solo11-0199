"""Half-open interval algebra shared by field evidence and capacity windows."""
from collections import Counter, defaultdict


def overlap(a, b):
    start, end = max(a[0], b[0]), min(a[1], b[1])
    return (start, end) if start < end else None


def merged(spans):
    output = []
    for start, end in sorted(spans):
        if output and start <= output[-1][1]:
            output[-1] = (output[-1][0], max(end, output[-1][1]))
        else:
            output.append((start, end))
    return output


def partition(tagged):
    """Yield disjoint windows with the set of tags active in each window."""
    changes = defaultdict(Counter)
    for (start, end), tag in tagged:
        changes[start][tag] += 1
        changes[end][tag] -= 1
    active = Counter()
    previous = None
    for point, deltas in sorted(changes.items()):
        if active:
            yield (previous, point), frozenset(active)
        active.update(deltas)
        for tag in deltas:
            if not active[tag]:
                del active[tag]
        previous = point


def duration(spans):
    return sum(end - start for start, end in spans)
