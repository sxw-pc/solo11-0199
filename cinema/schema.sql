BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS receipt (
    id INTEGER PRIMARY KEY,
    feed TEXT NOT NULL,
    sha TEXT NOT NULL,
    raw BLOB NOT NULL,
    UNIQUE (feed, sha)
);
CREATE TABLE IF NOT EXISTS revision (
    feed TEXT NOT NULL,
    identity TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    body TEXT NOT NULL,
    first_receipt INTEGER NOT NULL REFERENCES receipt(id),
    PRIMARY KEY (feed, identity, version)
);
CREATE INDEX IF NOT EXISTS revision_receipt ON revision(first_receipt);
CREATE TABLE IF NOT EXISTS receipt_row (
    receipt INTEGER NOT NULL REFERENCES receipt(id),
    line INTEGER NOT NULL CHECK (line > 0),
    feed TEXT NOT NULL,
    identity TEXT NOT NULL,
    version INTEGER NOT NULL,
    PRIMARY KEY (receipt, line),
    FOREIGN KEY (feed, identity, version) REFERENCES revision(feed, identity, version)
);
CREATE TABLE IF NOT EXISTS evidence_run (
    id INTEGER PRIMARY KEY,
    session TEXT NOT NULL,
    cutoff INTEGER NOT NULL CHECK (cutoff >= 0),
    digest TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (session, cutoff)
);
CREATE TABLE IF NOT EXISTS presence_finding (
    run INTEGER NOT NULL REFERENCES evidence_run(id),
    observation TEXT NOT NULL,
    household TEXT NOT NULL,
    overlap_seconds INTEGER NOT NULL CHECK (overlap_seconds >= 0),
    decision TEXT NOT NULL CHECK (decision IN (
        'void', 'estimate_only', 'screening_unproven', 'insufficient_observed_playback', 'named_presence'
    )),
    PRIMARY KEY (run, observation)
);
CREATE TABLE IF NOT EXISTS settlement (
    id INTEGER PRIMARY KEY,
    cycle TEXT NOT NULL,
    cutoff INTEGER NOT NULL CHECK (cutoff >= 0),
    previous INTEGER REFERENCES settlement(id),
    digest TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (cycle, cutoff)
);
CREATE TABLE IF NOT EXISTS entitlement_posting (
    settlement INTEGER NOT NULL REFERENCES settlement(id),
    village TEXT NOT NULL,
    household TEXT NOT NULL,
    delta INTEGER NOT NULL CHECK (delta IN (-1, 1)),
    PRIMARY KEY (settlement, village, household)
);
CREATE TABLE IF NOT EXISTS proposal (
    id INTEGER PRIMARY KEY,
    cycle TEXT NOT NULL,
    cutoff INTEGER NOT NULL CHECK (cutoff >= 0),
    not_before INTEGER NOT NULL,
    not_after INTEGER NOT NULL CHECK (not_after > not_before),
    holds TEXT NOT NULL,
    digest TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'committed', 'cancelled'))
);
CREATE UNIQUE INDEX IF NOT EXISTS live_proposal_basis ON proposal(cycle, cutoff, not_before, not_after, holds)
    WHERE status <> 'cancelled';
CREATE TABLE IF NOT EXISTS proposal_event (
    proposal INTEGER NOT NULL REFERENCES proposal(id),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'committed', 'cancelled')),
    PRIMARY KEY (proposal, status)
);
CREATE TABLE IF NOT EXISTS reservation (
    proposal INTEGER NOT NULL REFERENCES proposal(id),
    slot TEXT NOT NULL,
    payload TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    PRIMARY KEY (proposal, slot)
);
PRAGMA user_version = 1;
COMMIT;
