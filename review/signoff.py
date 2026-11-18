from sqlite3 import Connection

from catalog.transactions import Archive, read_snapshot, write_transaction
from intake.validation import validate_identifier
from review.receipts import (
    latest_conclusions,
    load_issue,
    load_issued_rows,
    next_number,
    validate_position,
)

_DECISIONS = ('confirmed', 'pending')


def _decode_entries(entries) -> tuple[tuple[int, str | None, str], ...]:
    if not isinstance(entries, (list, tuple)) or not entries:
        raise ValueError('entries must be a nonempty list or tuple of row conclusions')

    decoded = []
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise ValueError(
                f'entry {index} must be a (target_position, predecessor, decision) triple'
            )
        target_position, predecessor, decision = entry
        validate_position(target_position, f'entry {index} target position')
        if target_position in seen:
            raise ValueError(f'duplicate target position {target_position} in one receipt')
        if predecessor is not None:
            validate_identifier(predecessor, f'entry {index} predecessor')
        if type(decision) is not str or decision not in _DECISIONS:
            raise ValueError(f'entry {index} decision must be confirmed or pending')
        seen.add(target_position)
        decoded.append((target_position, predecessor, decision))
    return tuple(decoded)


def _accepted_replay(
    connection: Connection,
    receipt_id: str,
    issue_id: int,
    entries: tuple[tuple[int, str | None, str], ...],
) -> int | None:
    """Return the original cutoff when ``receipt_id`` already names this exact receipt."""
    record = connection.execute(
        'SELECT cutoff, issue_id FROM signoff_receipts WHERE receipt_id = ?',
        (receipt_id,),
    ).fetchone()
    if record is None:
        return None

    cutoff, stored_issue = record
    if stored_issue != issue_id:
        raise ValueError(f'receipt {receipt_id!r} already belongs to another issue')

    stored = connection.execute(
        '''SELECT target_position, predecessor, decision
           FROM signoff_entries WHERE cutoff = ?''',
        (cutoff,),
    )
    stored_entries = {(position, predecessor, decision)
                      for position, predecessor, decision in stored}
    if stored_entries != set(entries):
        raise ValueError(f'receipt {receipt_id!r} already has different entries')
    return cutoff


def _resolve_conclusions(
    connection: Connection,
    ticket_id: int,
    issue_id: int,
    entries: tuple[tuple[int, str | None, str], ...],
) -> list[tuple[int, int, str | None, str]]:
    issued = load_issued_rows(connection, issue_id)
    conclusions = latest_conclusions(connection, ticket_id, 2 ** 63 - 1)

    resolved = []
    for target_position, predecessor, decision in entries:
        marked_position = issued.get(target_position)
        if marked_position is None:
            raise ValueError(
                f'target position {target_position} was not delivered in issue {issue_id}'
            )
        current = conclusions.get(marked_position)
        expected = None if current is None else current[1]
        if predecessor != expected:
            raise ValueError(
                f'entry {target_position} predecessor must be {expected!r} for '
                f'marked row {marked_position}'
            )
        resolved.append((target_position, marked_position, predecessor, decision))
    return resolved


def submit(archive: Archive, receipt_id: str, issue_id: int, entries) -> int:
    """Archive one whole receipt; return its signoff cutoff."""
    validate_identifier(receipt_id, 'receipt id')
    decoded = _decode_entries(entries)

    # A replay only reads the already accepted original, so it never stages writes.
    with read_snapshot(archive) as connection:
        load_issue(connection, issue_id)
        replay = _accepted_replay(connection, receipt_id, issue_id, decoded)
        if replay is not None:
            return replay

    with write_transaction(archive) as connection:
        ticket_id, _target_revision, _payload = load_issue(connection, issue_id)
        replay = _accepted_replay(connection, receipt_id, issue_id, decoded)
        if replay is not None:
            return replay

        resolved = _resolve_conclusions(connection, ticket_id, issue_id, decoded)
        cutoff = next_number(connection, 'signoff_receipts', 'cutoff')
        connection.execute(
            '''INSERT INTO signoff_receipts (cutoff, receipt_id, issue_id)
               VALUES (?, ?, ?)''',
            (cutoff, receipt_id, issue_id),
        )
        connection.executemany(
            '''INSERT INTO signoff_entries
               (cutoff, target_position, ticket_id, marked_position, predecessor, decision)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (
                (cutoff, target_position, ticket_id, marked_position, predecessor, decision)
                for target_position, marked_position, predecessor, decision in resolved
            ),
        )
    return cutoff
