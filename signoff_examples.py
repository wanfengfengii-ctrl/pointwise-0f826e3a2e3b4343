import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from catalog.transactions import open_archive
from delivery.export import export
from intake.batches import register_batch
from review.claims import claim
from review.tickets import mark
from revision.coordinates import Delete, Move
from revision.publish import edit_day, seal_day


class CommitInterrupted(RuntimeError):
    pass


def interrupt_commit(_connection):
    raise CommitInterrupted("signoff interrupted before commit")


def same(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: got {actual!r}, expected {expected!r}")


def must_raise(error_type, operation, *args):
    try:
        operation(*args)
    except error_type:
        return
    raise AssertionError(f"{operation.__name__} should raise {error_type.__name__}")


def coordinates(result):
    return [(row.marked_position, row.target_position, row.value) for row in result.rows]


def fixture(path):
    archive = open_archive(path)
    day = "2026-11-18"
    batch = register_batch(archive, "signoff-sample", day, [10, 20, None])
    base = seal_day(archive, day, batch)
    ticket = mark(archive, base, 1, 3)
    other_ticket = mark(archive, base, 1, 3)
    moved = edit_day(archive, base, [Move(1, 0)])
    withdrawn = edit_day(archive, moved, [Delete(0)])
    expected = {
        base: [(1, 1, 20), (2, 2, None)],
        moved: [(1, 0, 20), (2, 2, None)],
        withdrawn: [(2, 1, None)],
    }
    for revision, rows in expected.items():
        same(coordinates(claim(archive, ticket, revision)), rows, "V1 coordinates")
    return archive, ticket, other_ticket, (base, moved, withdrawn), expected


def exercise(path):
    from delivery.export import issue
    from review.claims import pending, submit

    archive, ticket, other_ticket, (base, moved, withdrawn), expected = fixture(path)
    documents = [json.loads(export(archive, ticket, revision))
                 for revision in (base, moved, withdrawn)]
    issues = []
    for document in documents:
        issue_id, payload = issue(archive, ticket, document["target_revision"])
        if type(issue_id) is not int or issue_id <= 0 or issue_id in issues:
            raise AssertionError("each issue must have its own positive integer ID")
        same(json.loads(payload.decode("utf-8")), {"issue_id": issue_id, **document},
             "frozen issue payload")
        issues.append(issue_id)
    first, second, third = issues

    confirm_b = [(1, None, "confirmed")]
    reopen_b = [(0, "r1", "pending")]
    same(submit(archive, "r1", first, confirm_b), 1, "first cutoff")
    same(submit(archive, "r2", second, reopen_b), 2, "supplement cutoff")
    same(submit(archive, "r1", first, confirm_b), 1, "old receipt replay")
    must_raise(ValueError, submit, archive, "r1", first, [(1, None, "pending")])
    must_raise(ValueError, submit, archive, "mixed", second,
               [(2, None, "confirmed"), (0, None, "confirmed")])
    must_raise(ValueError, submit, archive, "outside", third, [(0, None, "confirmed")])
    unused_issue = next(number for number in range(1, 5) if number not in issues)
    must_raise(LookupError, submit, archive, "unknown", unused_issue, confirm_b)

    good = [(2, None, "confirmed"), (0, "r2", "pending")]
    interrupted = open_archive(path, before_commit=interrupt_commit)
    must_raise(CommitInterrupted, submit, interrupted, "r3", second, good)
    archive = open_archive(path)
    must_raise(LookupError, pending, archive, ticket, moved, 3)

    views = {}
    requests = [
        ("before_signoff", ticket, base, 0, expected[base]),
        ("after_confirmation", ticket, moved, 1, [expected[moved][1]]),
        ("after_supplement", ticket, moved, 2, expected[moved]),
        ("after_withdrawal", ticket, withdrawn, 2, expected[withdrawn]),
        ("independent_ticket", other_ticket, moved, 1, expected[moved]),
    ]
    for label, number, revision, cutoff, wanted in requests:
        result = pending(archive, number, revision, cutoff)
        same((result.ticket_id, result.target_revision), (number, revision), label)
        same(coordinates(result), wanted, label)
        views[label] = {"cutoff": cutoff, "rows": coordinates(result)}

    same(submit(archive, "r3", second, good), 3, "retry after rollback")
    same(submit(archive, "r3", second, list(reversed(good))), 3, "unordered replay")
    result = pending(archive, ticket, moved, 3)
    same(coordinates(result), [expected[moved][0]], "successful retry rows")
    views["after_retry"] = {"cutoff": 3, "rows": coordinates(result)}
    same(coordinates(claim(archive, ticket, moved)), expected[moved], "unchanged V1 claim")
    same(json.loads(export(archive, ticket, moved)), documents[1], "unchanged V1 export")
    return {"issues": [{"issue_id": key, **value} for key, value in zip(issues, documents)],
            "replayed_cutoff": 1, "pending": views}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("fixture", "verify", "demo"))
    args = parser.parse_args()
    with TemporaryDirectory() as directory:
        path = Path(directory) / "signoff.sqlite3"
        if args.mode == "fixture":
            _, _, _, _, expected = fixture(path)
            report = {"v1_coordinates": expected}
        else:
            report = exercise(path)
            if args.mode == "verify":
                report = {"pending": report["pending"]}
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
