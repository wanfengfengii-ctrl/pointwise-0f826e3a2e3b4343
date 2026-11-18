from catalog.transactions import Archive, read_snapshot
from review.engine import load_claim
from review.pending import pending
from review.results import ClaimResult
from review.signoff import submit

__all__ = ['claim', 'load_claim', 'pending', 'submit']


def claim(archive: Archive, ticket_id: int, target_revision: int) -> ClaimResult:
    with read_snapshot(archive) as connection:
        return load_claim(connection, ticket_id, target_revision)
