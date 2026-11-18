from decimal import Decimal
import json

from delivery.manifest import HandoffManifest
from review.results import ClaimRow


def _encode_row(row: ClaimRow) -> str:
    source = json.dumps(
        {'batch_id': row.source.batch_id, 'position': row.source.position},
        ensure_ascii=False,
        separators=(',', ':'),
    )
    # Decimal constructs the exact decimal token even beyond int's digit limit.
    value = 'null' if row.value is None else str(Decimal(row.value))
    return (
        f'{{"marked_position":{row.marked_position},"target_position":{row.target_position},'
        f'"source":{source},"value":{value}}}'
    )


def _encode_manifest(manifest: HandoffManifest, issue_id: int | None) -> bytes:
    rows = ','.join(_encode_row(row) for row in manifest.rows)
    totals = json.dumps(
        {'row_count': manifest.totals.row_count, 'missing_count': manifest.totals.missing_count},
        separators=(',', ':'),
    )
    prefix = '' if issue_id is None else f'"issue_id":{issue_id},'
    text = (
        f'{{{prefix}"ticket_id":{manifest.ticket_id},'
        f'"target_revision":{manifest.target_revision},'
        f'"rows":[{rows}],"totals":{totals}}}\n'
    )
    return text.encode('utf-8')


def encode_handoff(manifest: HandoffManifest) -> bytes:
    return _encode_manifest(manifest, None)


def encode_issue(issue_id: int, manifest: HandoffManifest) -> bytes:
    return _encode_manifest(manifest, issue_id)
