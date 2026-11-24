"""Public command-line interface. Each command runs in a single SQLite transaction."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import sys

from . import reconstruction, reservations, settlement
from .db import connect, initialize, require_initialized, transaction
from .demo import demo
from .formats import SCHEMAS, code, instant
from .journal import Snapshot, audit, ingest
from .scope import ServiceArea


def timestamp(value):
    try:
        return instant(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parser():
    cli = argparse.ArgumentParser(prog="python -m cinema", description=__doc__)
    cli.add_argument("--db", default="cinema.sqlite", help="SQLite path, default cinema.sqlite")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize the local ledger")
    commands.add_parser("demo", help="run packaged sample data in a disposable in-memory ledger")
    commands.add_parser("audit", help="verify raw receipts against normalized revisions")
    put = commands.add_parser("import", help="append a UTF-8 offline feed file")
    put.add_argument("feed", choices=SCHEMAS)
    put.add_argument("path", type=Path)
    for command, key in (("reconstruct", "session"), ("coverage", "cycle"), ("settle", "cycle")):
        sub = commands.add_parser(command)
        sub.add_argument(key)
        sub.add_argument("--cutoff", type=int, help="last visible receipt number")
    check = commands.add_parser("eligibility", help="check household eligibility without any playback evidence")
    for key in ("cycle", "site", "household"):
        check.add_argument(key)
    check.add_argument("start", type=timestamp)
    check.add_argument("end", type=timestamp)
    check.add_argument("--cutoff", type=int)
    plan = commands.add_parser("propose", help="select future makeup screenings and stage a proposal")
    plan.add_argument("cycle")
    plan.add_argument("--from", dest="not_before", type=timestamp, required=True)
    plan.add_argument("--until", dest="not_after", type=timestamp, help="latest slot end, default cycle end")
    for command in ("commit-plan", "cancel-plan", "replay-plan", "replay-run", "replay-settlement"):
        commands.add_parser(command).add_argument("id", type=int)
    return cli


def dispatch(args, db):
    command = args.command
    if command == "import":
        return ingest(db, args.feed, args.path.read_bytes())
    if command == "audit":
        return audit(db)
    if command == "reconstruct":
        return reconstruction.record_run(db, code(args.session), args.cutoff)
    if command == "replay-run":
        return reconstruction.replay_run(db, args.id)
    if command == "coverage":
        return settlement.recompute(db, code(args.cycle), args.cutoff)
    if command == "settle":
        return settlement.settle(db, code(args.cycle), args.cutoff)
    if command == "replay-settlement":
        return settlement.replay(db, args.id)
    if command == "eligibility":
        if args.end <= args.start:
            raise ValueError("end must follow start")
        area = ServiceArea(Snapshot(db, args.cutoff), code(args.cycle))
        return area.eligibility(code(args.site), code(args.household), (args.start, args.end))
    if command == "propose":
        return reservations.propose(db, code(args.cycle), args.not_before, args.not_after)
    if command == "commit-plan":
        return reservations.commit(db, args.id)
    if command == "cancel-plan":
        return reservations.cancel(db, args.id)
    if command == "replay-plan":
        return reservations.replay(db, args.id)
    raise AssertionError(f"unsupported command {command}")


def run(args):
    if args.command == "demo":
        return demo()
    if args.command != "init" and not Path(args.db).is_file():
        raise ValueError("database file does not exist; run init")
    with connect(args.db) as db:
        if args.command == "init":
            initialize(db)
            return {"schema_version": 1}
        require_initialized(db)
        write = args.command in {"import", "reconstruct", "settle", "propose", "commit-plan", "cancel-plan"}
        with transaction(db, write=write):
            return dispatch(args, db)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except (ValueError, OSError, csv.Error, sqlite3.Error) as error:
        print(f"cinema: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0
