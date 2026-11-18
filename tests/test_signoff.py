from decimal import Decimal
import json
import sqlite3

from catalog.transactions import open_archive, read_snapshot
from delivery.export import export, issue
from review.claims import claim, pending, submit
from review.tickets import mark
from revision.coordinates import Delete, Move
from revision.publish import edit_day, seal_day
from tests.support import ArchiveTestCase, BATCH, DAY, claim_lines, freeze


class CommitInterrupted(RuntimeError):
    pass


def interrupt_commit(_connection):
    raise CommitInterrupted('staged writes rejected')


def pending_lines(result):
    return claim_lines(result)


def issued_detail(archive, issue_id):
    with read_snapshot(archive) as connection:
        header = connection.execute(
            '''SELECT ticket_id, target_revision, payload
               FROM issued_handoffs WHERE issue_id = ?''',
            (issue_id,),
        ).fetchone()
        rows = connection.execute(
            '''SELECT target_position, marked_position, source_position, reading
               FROM issued_rows WHERE issue_id = ? ORDER BY target_position''',
            (issue_id,),
        ).fetchall()
        receipts = connection.execute(
            'SELECT cutoff, receipt_id, issue_id FROM signoff_receipts ORDER BY cutoff'
        ).fetchall()
    return header, rows, receipts


class IssueTests(ArchiveTestCase):
    def setUp(self):
        super().setUp()
        self.root = freeze(self.archive, [10, 20, None])
        self.ticket = mark(self.archive, self.root, 1, 3)
        self.moved = edit_day(self.archive, self.root, [Move(1, 0)])
        self.withdrawn = edit_day(self.archive, self.moved, [Delete(0)])

    def test_payload_matches_export_and_is_frozen(self):
        for target in (self.root, self.moved, self.withdrawn):
            with self.subTest(target=target):
                issue_id, payload = issue(self.archive, self.ticket, target)
                document = json.loads(payload)
                original = json.loads(export(self.archive, self.ticket, target))
                self.assertEqual(document, {'issue_id': issue_id, **original})
                header, rows, _ = issued_detail(self.archive, issue_id)
                self.assertEqual(header[:2], (self.ticket, target))
                self.assertEqual(header[2], payload)
                self.assertEqual(
                    [(position, marked) for position, marked, _, _ in rows],
                    [(row['target_position'], row['marked_position'])
                     for row in original['rows']],
                )

    def test_unique_ids_and_export_does_not_issue(self):
        first, _ = issue(self.archive, self.ticket, self.root)
        export(self.archive, self.ticket, self.root)
        second, _ = issue(self.archive, self.ticket, self.moved)
        third, _ = issue(self.archive, self.ticket, self.root)
        self.assertEqual([first, second, third], [1, 2, 3])
        reopened = open_archive(self.path)
        fourth, payload = issue(reopened, self.ticket, self.withdrawn)
        self.assertEqual(fourth, 4)
        self.assertEqual(json.loads(payload)['issue_id'], 4)

    def test_failed_issue_has_no_credential(self):
        interrupted = open_archive(self.path, before_commit=interrupt_commit)
        with self.assertRaises(CommitInterrupted):
            issue(interrupted, self.ticket, self.root)
        reopened = open_archive(self.path)
        with read_snapshot(reopened) as connection:
            count = connection.execute('SELECT COUNT(*) FROM issued_handoffs').fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(issue(reopened, self.ticket, self.root)[0], 1)

    def test_issue_scope_and_lookup(self):
        other = freeze(self.archive, [1], day='2026-11-19', batch='track-b')
        unissued = seal_day(self.archive, DAY, BATCH)
        with self.assertRaisesRegex(ValueError, 'outside the lineage'):
            issue(self.archive, self.ticket, other)
        with self.assertRaisesRegex(ValueError, 'outside the lineage'):
            issue(self.archive, self.ticket, unissued)
        with self.assertRaises(LookupError):
            issue(self.archive, self.ticket, self.root + 100)
        with self.assertRaises(LookupError):
            issue(self.archive, self.ticket + 100, self.root)
        for invalid in (0, -1, True, 1.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                issue(self.archive, self.ticket, invalid)

    def test_empty_issue_payload(self):
        root = freeze(self.archive, [5], day='2026-11-20', batch='track-c')
        ticket = mark(self.archive, root, 0, 1)
        gone = edit_day(self.archive, root, [Delete(0)])
        issue_id, payload = issue(self.archive, ticket, gone)
        document = json.loads(payload)
        self.assertEqual(document['rows'], [])
        self.assertEqual(document['totals'], {'row_count': 0, 'missing_count': 0})
        self.assertEqual(issue_id, 1)

    def test_exact_integer_payload(self):
        value = 10 ** 5000 + 17
        root = freeze(self.archive, [value, None], day='2026-11-21', batch='track-d')
        ticket = mark(self.archive, root, 0, 2)
        _, payload = issue(self.archive, ticket, root)
        document = json.loads(payload, parse_int=Decimal)
        self.assertEqual([row['value'] for row in document['rows']], [value, None])
        self.assertEqual(document['totals'], {'row_count': 2, 'missing_count': 1})


class SubmitTests(ArchiveTestCase):
    def setUp(self):
        super().setUp()
        self.root = freeze(self.archive, [10, 20, None])
        self.ticket = mark(self.archive, self.root, 1, 3)
        self.moved = edit_day(self.archive, self.root, [Move(1, 0)])
        self.withdrawn = edit_day(self.archive, self.moved, [Delete(0)])
        self.base_issue, _ = issue(self.archive, self.ticket, self.root)
        self.moved_issue, _ = issue(self.archive, self.ticket, self.moved)
        self.withdrawn_issue, _ = issue(self.archive, self.ticket, self.withdrawn)

    def test_cutoff_sequence_and_pending_window(self):
        self.assertEqual(pending_lines(pending(self.archive, self.ticket, self.root, 0)),
                         [[1, 1, BATCH, 1, 20], [2, 2, BATCH, 2, None]])
        self.assertEqual(submit(self.archive, 'r1', self.base_issue,
                                [(1, None, 'confirmed')]), 1)
        self.assertEqual(pending_lines(pending(self.archive, self.ticket, self.moved, 1)),
                         [[2, 2, BATCH, 2, None]])
        self.assertEqual(submit(self.archive, 'r2', self.moved_issue,
                                [(0, 'r1', 'pending')]), 2)
        self.assertEqual(pending_lines(pending(self.archive, self.ticket, self.moved, 2)),
                         [[1, 0, BATCH, 1, 20], [2, 2, BATCH, 2, None]])
        # earlier cutoffs keep their original answer
        self.assertEqual(pending_lines(pending(self.archive, self.ticket, self.moved, 1)),
                         [[2, 2, BATCH, 2, None]])
        self.assertEqual(pending_lines(pending(self.archive, self.ticket, self.moved, 0)),
                         [[1, 0, BATCH, 1, 20], [2, 2, BATCH, 2, None]])

    def test_replay_is_idempotent_and_order_free(self):
        entries = [(1, None, 'confirmed')]
        self.assertEqual(submit(self.archive, 'r1', self.base_issue, entries), 1)
        self.assertEqual(submit(self.archive, 'r1', self.base_issue, tuple(entries)), 1)
        self.assertEqual(submit(self.archive, 'r2', self.moved_issue,
                                [(0, 'r1', 'pending')]), 2)
        # replay after the predecessor became current state still returns the original cutoff
        self.assertEqual(submit(self.archive, 'r1', self.base_issue, entries), 1)
        two = [(2, None, 'confirmed'), (0, 'r2', 'pending')]
        self.assertEqual(submit(self.archive, 'r3', self.moved_issue, list(reversed(two))), 3)
        self.assertEqual(submit(self.archive, 'r3', self.moved_issue, two), 3)
        self.assertEqual(pending_lines(pending(self.archive, self.ticket, self.moved, 3)),
                         [[1, 0, BATCH, 1, 20]])

    def test_whole_receipt_rejections(self):
        submit(self.archive, 'r1', self.base_issue, [(1, None, 'confirmed')])
        cases = [
            ('mixed', self.moved_issue, [(2, None, 'confirmed'), (0, None, 'confirmed')]),
            ('stale', self.moved_issue, [(0, None, 'pending')]),
            ('outside', self.withdrawn_issue, [(0, None, 'confirmed')]),
            ('r1', self.moved_issue, [(0, 'r1', 'pending')]),
            ('r1', self.base_issue, [(1, None, 'pending')]),
        ]
        for receipt, issue_id, entries in cases:
            with self.subTest(receipt=receipt, issue=issue_id):
                with self.assertRaises(ValueError):
                    submit(self.archive, receipt, issue_id, entries)
        # nothing was archived: cutoff 2 is still unknown
        with self.assertRaises(LookupError):
            pending(self.archive, self.ticket, self.moved, 2)
        self.assertEqual(submit(self.archive, 'good', self.moved_issue,
                                [(2, None, 'confirmed'), (0, 'r1', 'pending')]), 2)

    def test_entry_validation(self):
        for entries in ([], (), [(1, None)], [(1, None, 'confirmed', 1)],
                        [(1, None, 'accepted')], [(True, None, 'confirmed')],
                        [(-1, None, 'confirmed')], [(1, '', 'confirmed')],
                        [(1, 5, 'confirmed')], [(1, None, 'confirmed'),
                                                (1, None, 'pending')]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                submit(self.archive, 'bad', self.base_issue, entries)
        for receipt in ('', None, b'r1'):
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                submit(self.archive, receipt, self.base_issue, [(1, None, 'confirmed')])
        for issue_id in (0, -1, True, 1.0):
            with self.subTest(issue_id=issue_id), self.assertRaises(ValueError):
                submit(self.archive, 'r1', issue_id, [(1, None, 'confirmed')])
        with self.assertRaises(LookupError):
            submit(self.archive, 'r1', self.withdrawn_issue + 100,
                   [(1, None, 'confirmed')])
        with read_snapshot(self.archive) as connection:
            count = connection.execute('SELECT COUNT(*) FROM signoff_receipts').fetchone()[0]
        self.assertEqual(count, 0)

    def test_interrupted_submit_keeps_cutoff_unknown(self):
        submit(self.archive, 'r1', self.base_issue, [(1, None, 'confirmed')])
        submit(self.archive, 'r2', self.moved_issue, [(0, 'r1', 'pending')])
        good = [(2, None, 'confirmed'), (0, 'r2', 'pending')]
        interrupted = open_archive(self.path, before_commit=interrupt_commit)
        with self.assertRaises(CommitInterrupted):
            submit(interrupted, 'r3', self.moved_issue, good)
        reopened = open_archive(self.path)
        with self.assertRaises(LookupError):
            pending(reopened, self.ticket, self.moved, 3)
        self.assertEqual(pending_lines(pending(reopened, self.ticket, self.moved, 2)),
                         [[1, 0, BATCH, 1, 20], [2, 2, BATCH, 2, None]])
        self.assertEqual(submit(reopened, 'r3', self.moved_issue, good), 3)
        self.assertEqual(pending_lines(pending(open_archive(self.path), self.ticket,
                                               self.moved, 3)),
                         [[1, 0, BATCH, 1, 20]])

    def test_reopen_and_plain_claim_unaffected(self):
        submit(self.archive, 'r1', self.base_issue, [(1, None, 'confirmed')])
        submit(self.archive, 'r2', self.moved_issue, [(0, 'r1', 'pending')])
        reopened = open_archive(self.path)
        self.assertEqual(pending_lines(pending(reopened, self.ticket, self.moved, 1)),
                         [[2, 2, BATCH, 2, None]])
        self.assertEqual(pending_lines(pending(reopened, self.ticket, self.moved, 2)),
                         [[1, 0, BATCH, 1, 20], [2, 2, BATCH, 2, None]])
        self.assertEqual(claim_lines(claim(reopened, self.ticket, self.moved)),
                         [[1, 0, BATCH, 1, 20], [2, 2, BATCH, 2, None]])
        self.assertEqual(submit(reopened, 'r1', self.base_issue,
                                [(1, None, 'confirmed')]), 1)


class PendingScopeTests(ArchiveTestCase):
    def test_independent_marks_and_shared_conclusion(self):
        root = freeze(self.archive, [10, 20, 30])
        first = mark(self.archive, root, 0, 2)
        second = mark(self.archive, root, 1, 3)
        moved = edit_day(self.archive, root, [Move(2, 0)])
        first_issue, _ = issue(self.archive, first, root)
        moved_issue, _ = issue(self.archive, first, moved)
        other_issue, _ = issue(self.archive, second, root)
        submit(self.archive, 'a1', first_issue, [(0, None, 'confirmed')])
        # first mark: row 1 (B) is still open and sits at target 2 after the move
        self.assertEqual(pending_lines(pending(self.archive, first, moved, 1)),
                         [[1, 2, BATCH, 1, 20]])
        # second mark shares the same source row but is independent
        self.assertEqual(pending_lines(pending(self.archive, second, root, 1)),
                         [[1, 1, BATCH, 1, 20], [2, 2, BATCH, 2, 30]])
        # conclusion from the first issue is addressable from the moved issue
        submit(self.archive, 'a2', moved_issue, [(2, None, 'confirmed')])
        self.assertEqual(pending_lines(pending(self.archive, first, moved, 2)), [])
        # second mark's issue can use the same predecessor id only for its own marks
        with self.assertRaises(ValueError):
            submit(self.archive, 'a3', other_issue, [(1, 'a1', 'confirmed')])
        submit(self.archive, 'a3', other_issue, [(1, None, 'confirmed'),
                                                 (2, None, 'pending')])
        self.assertEqual(pending_lines(pending(self.archive, second, root, 3)),
                         [[2, 2, BATCH, 2, 30]])

    def test_pending_scope_errors(self):
        root = freeze(self.archive, [10])
        ticket = mark(self.archive, root, 0, 1)
        child = edit_day(self.archive, root, [Move(0, 1)])
        unrelated = freeze(self.archive, [9], day='2026-11-19', batch='track-b')
        issue_id, _ = issue(self.archive, ticket, root)
        self.assertEqual(submit(self.archive, 'r1', issue_id, [(0, None, 'confirmed')]), 1)
        with self.assertRaisesRegex(ValueError, 'outside the lineage'):
            pending(self.archive, ticket, unrelated, 1)
        with self.assertRaises(LookupError):
            pending(self.archive, ticket, child + 100, 1)
        with self.assertRaises(LookupError):
            pending(self.archive, ticket + 100, child, 1)
        for cutoff in (2, 999):
            with self.subTest(cutoff=cutoff), self.assertRaises(LookupError):
                pending(self.archive, ticket, child, cutoff)
        for cutoff in (-1, True, 1.0, None):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                pending(self.archive, ticket, child, cutoff)
        self.assertEqual(pending_lines(pending(self.archive, ticket, child, 0)),
                         [[0, 0, BATCH, 0, 10]])
        self.assertEqual(pending_lines(pending(self.archive, ticket, child, 1)), [])


class VersionOneArchiveTests(ArchiveTestCase):
    def test_v1_archive_opens_with_original_data(self):
        root = freeze(self.archive, [10, 20, None])
        ticket = mark(self.archive, root, 1, 3)
        # Simulate a V1 archive: strip every table and index added after V1.
        connection = sqlite3.connect(self.path)
        try:
            connection.execute('DROP INDEX IF EXISTS signoff_entries_by_selection')
            for table in ('signoff_entries', 'signoff_receipts', 'issued_rows',
                          'issued_handoffs'):
                connection.execute(f'DROP TABLE IF EXISTS {table}')
            connection.commit()
        finally:
            connection.close()

        reopened = open_archive(self.path)
        self.assertEqual(claim_lines(claim(reopened, ticket, root)),
                         [[1, 1, BATCH, 1, 20], [2, 2, BATCH, 2, None]])
        issue_id, _payload = issue(reopened, ticket, root)
        self.assertEqual(issue_id, 1)
        self.assertEqual(submit(reopened, 'r1', issue_id, [(1, None, 'confirmed')]), 1)
        self.assertEqual(pending_lines(pending(reopened, ticket, root, 1)),
                         [[2, 2, BATCH, 2, None]])
