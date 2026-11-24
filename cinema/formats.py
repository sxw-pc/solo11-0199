"""Parse field formats and validate the record-level contract."""
import csv
from datetime import datetime, timezone
import io
import json
import re


def code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError(f"invalid identifier {value!r}")
    return value


def number(low, high):
    def parse(value):
        if type(value) is int:
            result = value
        elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,8}", value):
            result = int(value)
        else:
            raise ValueError(f"invalid integer {value!r}")
        if not low <= result <= high:
            raise ValueError(f"integer outside {low}..{high}: {value!r}")
        return result
    return parse


def choice(*allowed):
    def parse(value):
        if value not in allowed:
            raise ValueError(f"expected one of {allowed}: {value!r}")
        return value
    return parse


def instant(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})", value
    ):
        raise ValueError(f"expected an ISO timestamp with an explicit offset: {value!r}")
    if value[-6:-5] in ('+', '-') and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError(f"invalid UTC offset: {value!r}")
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


REV = {"id": code, "rev": number(1, 99999999), "state": choice("active", "void")}
SPAN = {"start": instant, "end": instant}
SCHEMAS = {
    "player": dict(REV, **SPAN, session=code, device=code, film=code, offset=number(0, 28800)),
    "sessions": dict(REV, **SPAN, cycle=code, site=code, feeder=code, device=code,
                     film=code, runtime=number(60, 14400)),
    "attendance": dict(REV, **SPAN, session=code, channel=choice("sign_in", "field_check", "estimate"),
                       observer=code, household=lambda value: "" if value == "" else code(value),
                       count=number(0, 3000)),
    "power": dict(REV, **SPAN, feeder=code, channel=choice("meter", "dispatcher", "electrician"),
                  observer=code),
    "cycles": dict(REV, **SPAN),
    "targets": dict(REV, cycle=code, village=code, goal=number(1, 3000),
                    priority=choice("remote", "standard")),
    "roster": dict(REV, cycle=code, household=code, village=code),
    "scope": dict(REV, **SPAN, site=code, village=code),
    "slots": dict(REV, **SPAN, cycle=code, site=code, crew=code, kit=code,
                  seats=number(1, 500), turnaround=number(0, 14400)),
    "bookings": dict(REV, slot=code, household=code),
}
JSON_FEEDS = {"player", "sessions"}


def check_record(feed, row):
    fields = SCHEMAS[feed]
    if not isinstance(row, dict) or row.keys() != fields.keys():
        raise ValueError(f"columns must be exactly {', '.join(fields)}")
    result = {key: parse(row[key]) for key, parse in fields.items()}
    if "start" in result and result["end"] <= result["start"]:
        raise ValueError("end must follow start")
    if feed == "attendance":
        estimate = result["channel"] == "estimate"
        if estimate and (result["household"] or result["count"] < 1):
            raise ValueError("estimates require a count and an empty household")
        if not estimate and (not result["household"] or result["count"] != 1):
            raise ValueError("named attendance requires a household and count=1")
    if feed in {"targets", "roster"}:
        member = "village" if feed == "targets" else "household"
        # Length-prefixed cycle prevents ambiguity when identifier components contain dots.
        identity = f"{len(result['cycle'])}.{result['cycle']}.{result[member]}"
        # IDs in these two feeds are derived and may exceed the length of a component.
        result["id"] = identity
        if row["id"] != result[member]:
            raise ValueError(f"{feed} id must equal {member}")
    return result


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def input_rows(feed, raw):
    if feed not in SCHEMAS:
        raise ValueError(f"unknown feed {feed!r}")
    text = raw.decode("utf-8-sig")
    if feed in JSON_FEEDS:
        for line, item in enumerate(text.splitlines(), 1):
            if item.strip():
                try:
                    yield line, check_record(feed, json.loads(item, object_pairs_hook=unique_pairs))
                except ValueError as error:
                    raise ValueError(f"{feed}:{line}: {error}") from error
    else:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(SCHEMAS[feed]) or set(headers) != SCHEMAS[feed].keys():
            raise ValueError(f"{feed}: invalid CSV header, expected {', '.join(SCHEMAS[feed])}")
        for row in reader:
            try:
                yield reader.line_num, check_record(feed, row)
            except ValueError as error:
                raise ValueError(f"{feed}:{reader.line_num}: {error}") from error
