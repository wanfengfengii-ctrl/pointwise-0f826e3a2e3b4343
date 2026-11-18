from catalog.transactions import Archive, read_snapshot
from review.engine import load_claim
from review.receipts import latest_conclusions, require_cutoff
from review.results import ClaimResult


def pending(
    archive: Archive, ticket_id: int, target_revision: int, cutoff: int
) -> ClaimResult:
    """Return surviving selected rows with no conclusion, or a pending one, at ``cutoff``."""
    with read_snapshot(archive) as connection:
        require_cutoff(connection, cutoff)
        result = load_claim(connection, ticket_id, target_revision)
        conclusions = latest_conclusions(connection, result.ticket_id, cutoff)
    rows = tuple(
        row for row in result.rows
        if (conclusion := conclusions.get(row.marked_position)) is None
        or conclusion[0] == 'pending'
    )
    return ClaimResult(result.ticket_id, result.target_revision, rows)
