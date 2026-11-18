from catalog.transactions import Archive, write_transaction
from delivery.format import encode_issue
from delivery.manifest import build_manifest
from intake.format import encode_reading
from review.engine import load_claim
from review.receipts import next_number


def issue(archive: Archive, ticket_id: int, target_revision: int) -> tuple[int, bytes]:
    """Freeze one handoff and return its archive-unique id with the offline payload."""
    with write_transaction(archive) as connection:
        result = load_claim(connection, ticket_id, target_revision)
        manifest = build_manifest(result)
        issue_id = next_number(connection, 'issued_handoffs', 'issue_id')
        payload = encode_issue(issue_id, manifest)
        connection.execute(
            '''INSERT INTO issued_handoffs (issue_id, ticket_id, target_revision, payload)
               VALUES (?, ?, ?, ?)''',
            (issue_id, result.ticket_id, result.target_revision, payload),
        )
        connection.executemany(
            '''INSERT INTO issued_rows
               (issue_id, target_position, marked_position, batch_id, source_position, reading)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (
                (issue_id, row.target_position, row.marked_position,
                 row.source.batch_id, row.source.position, encode_reading(row.value))
                for row in manifest.rows
            ),
        )
    return issue_id, payload
