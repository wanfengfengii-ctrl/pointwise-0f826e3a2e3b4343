import json
from pathlib import Path
from tempfile import TemporaryDirectory

from catalog.transactions import open_archive
from delivery.export import export, issue
from intake.batches import register_batch
from review.claims import claim, pending, submit
from review.tickets import mark
from revision.coordinates import Copy, Delete, Move
from revision.publish import edit_day, seal_day


def _claim_details(result):
    return [
        {'marked_position': row.marked_position, 'target_position': row.target_position,
         'source': {'batch_id': row.source.batch_id, 'position': row.source.position},
         'value': row.value}
        for row in result.rows
    ]


def _expected_row(marked, target, source_position, value):
    return {
        'marked_position': marked,
        'target_position': target,
        'source': {'batch_id': 'track-demo-a', 'position': source_position},
        'value': value,
    }


def _v1_case(sample):
    with TemporaryDirectory() as directory:
        archive_path = Path(directory) / 'demo.sqlite3'
        archive = open_archive(archive_path)
        batch_id = register_batch(archive, sample['batch_id'], sample['day_id'], sample['readings'])
        initial = seal_day(archive, sample['day_id'], batch_id)
        ticket = mark(archive, initial, *sample['selection'])
        moved = edit_day(archive, initial, [Copy(1, 4), Move(1, 0)])
        copy_ticket = mark(archive, moved, 4, 5)
        withdrawn = edit_day(archive, moved, [Delete(0)])

        reopened = open_archive(archive_path)
        requests = {
            'initial': (ticket, initial),
            'moved': (ticket, moved),
            'after_withdrawal': (ticket, withdrawn),
            'copy_ticket_after_withdrawal': (copy_ticket, withdrawn),
        }
        actual = {
            label: _claim_details(claim(reopened, *request)) for label, request in requests.items()
        }
        downstream_actual = {
            label: json.loads(export(reopened, *request)) for label, request in requests.items()
        }

    expected = {
        'initial': [_expected_row(1, 1, 1, 22), _expected_row(2, 2, 2, None)],
        'moved': [_expected_row(1, 0, 1, 22), _expected_row(2, 2, 2, None)],
        'after_withdrawal': [_expected_row(2, 1, 2, None)],
        'copy_ticket_after_withdrawal': [_expected_row(4, 3, 1, 22)],
    }
    expected_counts = {
        'initial': (2, 1),
        'moved': (2, 1),
        'after_withdrawal': (1, 1),
        'copy_ticket_after_withdrawal': (1, 0),
    }
    downstream_expected = {}
    for label, (ticket_id, target_revision) in requests.items():
        row_count, missing_count = expected_counts[label]
        downstream_expected[label] = {
            'ticket_id': ticket_id,
            'target_revision': target_revision,
            'rows': expected[label],
            'totals': {'row_count': row_count, 'missing_count': missing_count},
        }
    if actual != expected or downstream_actual != downstream_expected:
        raise RuntimeError('v6.frozen_survival: claim or manifest disagrees with the hand trace')

    return {
        'case_id': 'v6.frozen_survival',
        'input': {
            'batch': sample,
            'first_edit': [['copy', 1, 4], ['move', 1, 0]],
            'copy_selection': [4, 5],
            'second_edit': [['delete', 0]],
        },
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': [
            'revision/operations.py', 'revision/publish.py', 'review/claims.py',
            'review/projection.py', 'delivery/export.py',
        ],
        'oracle': '手工区分两条同源 B，逐步列出原选行的目标位置，再单独核算缺测数。',
    }


def _signoff_case():
    with TemporaryDirectory() as directory:
        archive_path = Path(directory) / 'signoff.sqlite3'
        archive = open_archive(archive_path)
        day = '2026-11-18'
        batch = register_batch(archive, 'track-signoff', day, [10, 20, None])
        base = seal_day(archive, day, batch)
        ticket = mark(archive, base, 1, 3)
        moved = edit_day(archive, base, [Move(1, 0)])
        withdrawn = edit_day(archive, moved, [Delete(0)])

        base_issue, base_payload = issue(archive, ticket, base)
        moved_issue, moved_payload = issue(archive, ticket, moved)
        withdrawn_issue, withdrawn_payload = issue(archive, ticket, withdrawn)

        first = submit(archive, 'r1', base_issue, [(1, None, 'confirmed')])
        second = submit(archive, 'r2', moved_issue, [(0, 'r1', 'pending')])
        replayed = submit(archive, 'r1', base_issue, [(1, None, 'confirmed')])
        third = submit(archive, 'r3', moved_issue,
                       [(2, None, 'confirmed'), (0, 'r2', 'pending')])

        reopened = open_archive(archive_path)
        cutoff_views = {
            'cutoff_0_before_signoff': _claim_details(pending(reopened, ticket, moved, 0)),
            'cutoff_1_confirmed_b': _claim_details(pending(reopened, ticket, moved, 1)),
            'cutoff_2_reopened_b': _claim_details(pending(reopened, ticket, moved, 2)),
            'cutoff_2_withdrawn': _claim_details(pending(reopened, ticket, withdrawn, 2)),
            'cutoff_3_pending_b': _claim_details(pending(reopened, ticket, moved, 3)),
        }
        third_scope = None
        try:
            submit(reopened, 'outside', withdrawn_issue, [(0, None, 'pending')])
        except ValueError as error:
            third_scope = str(error)
        plain = _claim_details(claim(reopened, ticket, moved))

    return {
        'case_id': 'v7.signoff_pending',
        'issues': {
            'base': {'issue_id': base_issue, 'payload': json.loads(base_payload)},
            'moved': {'issue_id': moved_issue, 'payload': json.loads(moved_payload)},
            'withdrawn': {'issue_id': withdrawn_issue, 'payload': json.loads(withdrawn_payload)},
        },
        'cutoffs': {'first': first, 'supplement': second, 'replayed': replayed, 'third': third},
        'pending_after_reopen': cutoff_views,
        'unissued_position_rejected': third_scope,
        'plain_claim_after_reopen': plain,
        'oracle': '手工按原选行身份累计各截止的结论，重开后按旧截止复算。',
    }


def main() -> None:
    path = Path(__file__).resolve().parent / 'samples' / 'observations.json'
    sample = json.loads(path.read_text(encoding='utf-8'))
    v1 = _v1_case(sample)
    print('POINTWISE_SCENARIO ' + json.dumps(v1, ensure_ascii=False, separators=(',', ':')))
    signoff = _signoff_case()
    print('POINTWISE_SCENARIO ' + json.dumps(signoff, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
