import csv
import io
import json
import unittest

from cinema.db import connect, initialize, transaction
from cinema.demo import EXAMPLES, FEEDS
from cinema.journal import ingest


class LedgerCase(unittest.TestCase):
    def setUp(self):
        manager = connect(":memory:")
        self.db = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        initialize(self.db)

    def put(self, feed, raw):
        with transaction(self.db, write=True):
            return ingest(self.db, feed, raw if isinstance(raw, bytes) else raw.encode())

    def fixture(self, filename, feed=None):
        path = EXAMPLES / filename
        return self.put(feed or path.stem, path.read_bytes())

    def sample(self, files=FEEDS):
        for filename in files:
            self.fixture(filename)

    def changed_csv(self, feed, identity, **changes):
        with (EXAMPLES / f"{feed}.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            row = next(row for row in reader if row["id"] == identity)
            row.update(changes)
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, reader.fieldnames)
            writer.writeheader()
            writer.writerow(row)
        return self.put(feed, output.getvalue())

    def changed_json(self, feed, identity, **changes):
        text = (EXAMPLES / f"{feed}.jsonl").read_text(encoding="utf-8-sig")
        row = next(item for item in map(json.loads, text.splitlines()) if item["id"] == identity)
        return self.put(feed, json.dumps(row | changes))

    def write(self, function, *args, **kwargs):
        with transaction(self.db, write=True):
            return function(self.db, *args, **kwargs)
