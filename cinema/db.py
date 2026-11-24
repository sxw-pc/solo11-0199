"""Connection lifecycle and the ledger schema."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


@contextmanager
def connect(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
    finally:
        db.close()


@contextmanager
def transaction(db, *, write=False):
    db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1):
        raise ValueError(f"unsupported database version {version}")
    if version == 0:
        db.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))


def require_initialized(db):
    if db.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise ValueError("initialize the database with init")
