from collections.abc import Iterable
from dataclasses import dataclass

from catalog.lookup import FrozenRowRecord
from intake.source_rows import SourceRef


@dataclass(frozen=True, slots=True)
class Occurrence:
    identity: tuple[int, int]
    source: SourceRef


class OccurrenceAllocator:
    def __init__(self, revision_id: int) -> None:
        self._revision_id = revision_id
        self._next_sequence = 0

    def create(self, source: SourceRef) -> Occurrence:
        occurrence = Occurrence((self._revision_id, self._next_sequence), source)
        self._next_sequence += 1
        return occurrence


def restore_occurrences(rows: Iterable[FrozenRowRecord]) -> list[Occurrence]:
    return [Occurrence(row.identity, row.source) for row in rows]
