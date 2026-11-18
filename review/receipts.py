from sqlite3 import Connection

from catalog.lookup import validate_record_id


def validate_position(position: int, label: str) -> None:
    if type(position) is not int or not 0 <= position < 2 ** 63:
        raise ValueError(f'{label} must be a non-negative SQLite integer position')


def validate_cutoff_argument(cutoff: int) -> None:
    if type(cutoff) is not int or not 0 <= cutoff < 2 ** 63:
        raise ValueError('cutoff must be a non-negative SQLite integer')


def require_cutoff(connection: Connection, cutoff: int) -> int:
    validate_cutoff_argument(cutoff)
    if cutoff == 0:
        return 0
    record = connection.execute(
        'SELECT cutoff FROM signoff_receipts WHERE cutoff = ?', (cutoff,)
    ).fetchone()
    if record is None:
        raise LookupError(f'unknown signoff cutoff {cutoff}')
    return cutoff


def next_number(connection: Connection, table: str, column: str) -> int:
    (current,) = connection.execute(
        f'SELECT COALESCE(MAX({column}), 0) FROM {table}'
    ).fetchone()
    return current + 1


def latest_conclusions(
    connection: Connection, ticket_id: int, cutoff: int
) -> dict[int, tuple[str, str]]:
    """Map each marked position to (latest decision, receipt id) as of ``cutoff``."""
    records = connection.execute(
        '''SELECT entries.marked_position, entries.cutoff, entries.decision,
                  receipts.receipt_id
           FROM signoff_entries AS entries
           JOIN signoff_receipts AS receipts ON receipts.cutoff = entries.cutoff
           JOIN (
               SELECT marked_position, MAX(cutoff) AS cutoff
               FROM signoff_entries
               WHERE ticket_id = ? AND cutoff <= ?
               GROUP BY marked_position
           ) AS newest
             ON newest.marked_position = entries.marked_position
            AND newest.cutoff = entries.cutoff
           WHERE entries.ticket_id = ?''',
        (ticket_id, cutoff, ticket_id),
    )
    return {
        marked_position: (decision, receipt_id)
        for marked_position, _cutoff, decision, receipt_id in records
    }


def load_issue(connection: Connection, issue_id: int) -> tuple[int, int, bytes]:
    validate_record_id(issue_id, 'issue id')
    record = connection.execute(
        '''SELECT ticket_id, target_revision, payload
           FROM issued_handoffs WHERE issue_id = ?''',
        (issue_id,),
    ).fetchone()
    if record is None:
        raise LookupError(f'unknown issued handoff {issue_id}')
    return record


def load_issued_rows(connection: Connection, issue_id: int) -> dict[int, int]:
    """Map an issue's target position to its marked position."""
    records = connection.execute(
        '''SELECT target_position, marked_position
           FROM issued_rows WHERE issue_id = ? ORDER BY target_position''',
        (issue_id,),
    )
    return dict(records)
