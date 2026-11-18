from collections.abc import Iterable, Mapping

from catalog.lookup import FrozenRowRecord
from review.results import ClaimRow


def project_survivors(
    selected_positions: Mapping[tuple[int, int], int],
    target_rows: Iterable[FrozenRowRecord],
) -> tuple[ClaimRow, ...]:
    survivors = []
    for target in target_rows:
        marked_position = selected_positions.get(target.identity)
        if marked_position is not None:
            survivors.append(
                ClaimRow(marked_position, target.position, target.source, target.value)
            )
    return tuple(survivors)
