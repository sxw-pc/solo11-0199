"""Immutable receipt history, version selection and provenance verification."""
from hashlib import sha256
import json

from .formats import input_rows


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value):
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def watermark(db, cutoff=None):
    latest = db.execute("SELECT coalesce(max(id), 0) FROM receipt").fetchone()[0]
    if cutoff is None:
        return latest
    if not 0 <= cutoff <= latest:
        raise ValueError(f"receipt cutoff outside 0..{latest}")
    return cutoff


def ingest(db, feed, raw):
    sha = sha256(raw).hexdigest()
    found = db.execute("SELECT id FROM receipt WHERE feed=? AND sha=?", (feed, sha)).fetchone()
    if found:
        return {"receipt": found[0], "duplicate": True, "sha256": sha}
    rows = list(input_rows(feed, raw))
    if not rows:
        raise ValueError(f"{feed}: no records")
    receipt = db.execute("INSERT INTO receipt(feed, sha, raw) VALUES (?, ?, ?)", (feed, sha, raw)).lastrowid
    added = 0
    seen = set()
    for line, item in rows:
        key = (feed, item["id"], item["rev"])
        if key in seen:
            raise ValueError(f"{feed}:{line}: duplicate identity and revision within receipt")
        seen.add(key)
        body = canonical(item)
        existing = db.execute("SELECT body FROM revision WHERE feed=? AND identity=? AND version=?", key).fetchone()
        if existing and existing[0] != body:
            raise ValueError(f"{feed}:{line}: conflicting revision {key[1]}@{key[2]}")
        if existing is None:
            db.execute("INSERT INTO revision VALUES (?, ?, ?, ?, ?)", (*key, body, receipt))
            added += 1
        db.execute("INSERT INTO receipt_row VALUES (?, ?, ?, ?, ?)", (receipt, line, *key))
    return {"receipt": receipt, "duplicate": False, "sha256": sha, "rows": len(rows), "new_revisions": added}


class Snapshot:
    def __init__(self, db, cutoff=None):
        self.db = db
        self.cutoff = watermark(db, cutoff)
        self.cache = {}

    def feed(self, name):
        if name not in self.cache:
            rows = self.db.execute("""
                WITH ranked AS (
                    SELECT *, row_number() OVER (PARTITION BY identity ORDER BY version DESC) AS rank
                    FROM revision WHERE feed=? AND first_receipt<=?
                )
                SELECT ranked.body, receipt.id, receipt.sha, receipt_row.line
                FROM ranked JOIN receipt ON receipt.id=ranked.first_receipt
                JOIN receipt_row ON (receipt_row.receipt, receipt_row.feed, receipt_row.identity, receipt_row.version)
                                  =(receipt.id, ranked.feed, ranked.identity, ranked.version)
                WHERE rank=1 ORDER BY ranked.identity
            """, (name, self.cutoff))
            self.cache[name] = [dict(json.loads(row["body"]), ref={"feed": name, "receipt": row["id"],
                                                                  "sha256": row["sha"], "line": row["line"]})
                                for row in rows]
        return self.cache[name]

    def active(self, name):
        return [row for row in self.feed(name) if row["state"] == "active"]


def reference(record):
    return dict(record["ref"], id=record["id"], rev=record["rev"])


def audit(db, cutoff=None):
    cutoff = watermark(db, cutoff)
    count = 0
    for row in db.execute("SELECT * FROM receipt WHERE id<=? ORDER BY id", (cutoff,)):
        if sha256(row["raw"]).hexdigest() != row["sha"]:
            raise ValueError(f"raw receipt {row['id']} digest mismatch")
        expected = [(line, item["id"], item["rev"], canonical(item))
                    for line, item in input_rows(row["feed"], row["raw"])]
        actual = db.execute("""
            SELECT line, identity, version, body FROM receipt_row JOIN revision USING(feed, identity, version)
            WHERE receipt=? AND feed=? ORDER BY line
        """, (row["id"], row["feed"])).fetchall()
        if expected != [tuple(item) for item in actual]:
            raise ValueError(f"raw receipt {row['id']} does not match its normalized rows")
        count += len(expected)
    missing = db.execute("""
        SELECT revision.feed, revision.identity, revision.version FROM revision
        LEFT JOIN receipt_row USING(feed, identity, version)
        WHERE revision.first_receipt<=? OR receipt_row.receipt<=?
        GROUP BY revision.feed, revision.identity, revision.version
        HAVING min(receipt_row.receipt) IS NOT revision.first_receipt
    """, (cutoff, cutoff)).fetchone()
    if missing:
        raise ValueError(f"first receipt mismatch: {tuple(missing)}")
    wrong_feed = db.execute("""
        SELECT 1 FROM receipt_row JOIN receipt ON receipt.id=receipt_row.receipt
        WHERE receipt.id<=? AND receipt_row.feed<>receipt.feed LIMIT 1
    """, (cutoff,)).fetchone()
    if wrong_feed:
        raise ValueError("receipt row feed mismatch")
    return {"receipts": cutoff, "rows": count, "verified": True}
