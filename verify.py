from pathlib import Path
import subprocess
import sys
import unittest


def run_regressions(root: Path) -> bool:
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'), top_level_dir=str(root))
    if suite.countTestCases() == 0:
        print('No business regression tests were discovered', file=sys.stderr)
        return False
    return unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


def run_signoff(root: Path) -> bool:
    completed = subprocess.run(
        [sys.executable, '-B', str(root / 'signoff_examples.py'), 'verify'],
        cwd=root,
        capture_output=True,
        encoding='utf-8',
    )
    if completed.returncode != 0:
        print(completed.stdout, file=sys.stdout, end='')
        print(completed.stderr, file=sys.stderr, end='')
        print('Signoff joint verification failed', file=sys.stderr)
        return False
    print(completed.stdout, end='')
    return True


def main() -> int:
    root = Path(__file__).resolve().parent
    regressions_ok = run_regressions(root)
    signoff_ok = run_signoff(root)
    return 0 if regressions_ok and signoff_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
