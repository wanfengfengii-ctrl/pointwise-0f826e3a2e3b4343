from sqlite3 import Connection


_TABLES = (
    '''CREATE TABLE IF NOT EXISTS source_batches (
        batch_id TEXT NOT NULL PRIMARY KEY,
        day_id TEXT NOT NULL,
        UNIQUE (batch_id, day_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS source_rows (
        batch_id TEXT NOT NULL REFERENCES source_batches (batch_id),
        source_position INTEGER NOT NULL CHECK (source_position >= 0),
        reading TEXT,
        PRIMARY KEY (batch_id, source_position)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS revisions (
        revision_id INTEGER PRIMARY KEY,
        day_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        parent_revision INTEGER,
        UNIQUE (revision_id, batch_id),
        CHECK (parent_revision IS NULL OR parent_revision < revision_id),
        FOREIGN KEY (batch_id, day_id) REFERENCES source_batches (batch_id, day_id),
        FOREIGN KEY (parent_revision, batch_id) REFERENCES revisions (revision_id, batch_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS revision_rows (
        revision_id INTEGER NOT NULL,
        position INTEGER NOT NULL CHECK (position >= 0),
        birth_revision INTEGER NOT NULL CHECK (birth_revision <= revision_id),
        birth_sequence INTEGER NOT NULL CHECK (birth_sequence >= 0),
        batch_id TEXT NOT NULL,
        source_position INTEGER NOT NULL,
        PRIMARY KEY (revision_id, position),
        UNIQUE (revision_id, birth_revision, birth_sequence),
        FOREIGN KEY (revision_id, batch_id) REFERENCES revisions (revision_id, batch_id),
        FOREIGN KEY (birth_revision, batch_id) REFERENCES revisions (revision_id, batch_id),
        FOREIGN KEY (batch_id, source_position) REFERENCES source_rows (batch_id, source_position)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS review_tickets (
        ticket_id INTEGER PRIMARY KEY,
        revision_id INTEGER NOT NULL REFERENCES revisions (revision_id),
        start_position INTEGER NOT NULL CHECK (start_position >= 0),
        stop_position INTEGER NOT NULL CHECK (stop_position > start_position)
    )''',
    '''CREATE TABLE IF NOT EXISTS issued_handoffs (
        issue_id INTEGER PRIMARY KEY,
        ticket_id INTEGER NOT NULL REFERENCES review_tickets (ticket_id),
        target_revision INTEGER NOT NULL REFERENCES revisions (revision_id),
        payload BLOB NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS issued_rows (
        issue_id INTEGER NOT NULL REFERENCES issued_handoffs (issue_id),
        target_position INTEGER NOT NULL CHECK (target_position >= 0),
        marked_position INTEGER NOT NULL CHECK (marked_position >= 0),
        batch_id TEXT NOT NULL,
        source_position INTEGER NOT NULL,
        reading TEXT,
        PRIMARY KEY (issue_id, target_position),
        FOREIGN KEY (batch_id, source_position)
            REFERENCES source_rows (batch_id, source_position)
    )''',
    '''CREATE TABLE IF NOT EXISTS signoff_receipts (
        cutoff INTEGER PRIMARY KEY,
        receipt_id TEXT NOT NULL UNIQUE,
        issue_id INTEGER NOT NULL REFERENCES issued_handoffs (issue_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS signoff_entries (
        cutoff INTEGER NOT NULL REFERENCES signoff_receipts (cutoff),
        target_position INTEGER NOT NULL CHECK (target_position >= 0),
        ticket_id INTEGER NOT NULL REFERENCES review_tickets (ticket_id),
        marked_position INTEGER NOT NULL CHECK (marked_position >= 0),
        predecessor TEXT,
        decision TEXT NOT NULL CHECK (decision IN ('confirmed', 'pending')),
        PRIMARY KEY (cutoff, target_position)
    )''',
    '''CREATE INDEX IF NOT EXISTS signoff_entries_by_selection
        ON signoff_entries (ticket_id, marked_position, cutoff)''',
)


def initialize_schema(connection: Connection) -> None:
    for statement in _TABLES:
        connection.execute(statement)
