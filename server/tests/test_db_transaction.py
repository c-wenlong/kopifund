"""server/finance/db.py `transaction`: a write that reads first waits for
another write instead of failing with "database is locked"."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from finance import db


def _get(conn, key):
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _put(conn, key, value):
    conn.execute("INSERT INTO setting(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


class TransactionLockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "kopifund.db"
        with closing(db.connect(self.path)) as conn, db.transaction(conn):
            _put(conn, "count", "0")

    def tearDown(self):
        self.dir.cleanup()

    def test_a_read_then_write_waits_for_another_write(self):
        """One request holds the write lock (a load) while another runs a
        read-then-write transaction (a rule save's relink). The second waits
        for as long as the first holds the lock, then writes on top of the
        first's commit."""
        holding, release, errors = threading.Event(), threading.Event(), []

        def long_write():
            try:
                with closing(db.connect(self.path)) as conn, db.transaction(conn):
                    _put(conn, "count", str(int(_get(conn, "count")) + 1))
                    holding.set()
                    release.wait(10)            # the lock is held until the test lets go
            except Exception as e:              # surfaced by the asserts below
                errors.append(e)
                holding.set()

        def read_then_write():
            try:
                with closing(db.connect(self.path)) as conn, db.transaction(conn):
                    n = int(_get(conn, "count"))    # read first...
                    _put(conn, "count", str(n + 1))  # ...then write
            except Exception as e:
                errors.append(e)

        first = threading.Thread(target=long_write)
        first.start()
        self.assertTrue(holding.wait(5))
        second = threading.Thread(target=read_then_write)
        second.start()
        second.join(0.5)
        # still waiting on the lock the first holds, not failed with "database is locked"
        alive = second.is_alive()
        release.set()
        first.join(10)
        second.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(alive)
        with closing(db.connect(self.path)) as conn:
            # both increments landed: the second read saw the first's commit
            self.assertEqual(_get(conn, "count"), "2")

    def test_a_nested_transaction_is_still_a_savepoint(self):
        with closing(db.connect(self.path)) as conn:
            with db.transaction(conn):
                _put(conn, "outer", "kept")
                with self.assertRaises(RuntimeError):
                    with db.transaction(conn):
                        _put(conn, "inner", "dropped")
                        raise RuntimeError("undo the inner write only")
            self.assertEqual(_get(conn, "outer"), "kept")
            self.assertIsNone(_get(conn, "inner"))

    def test_a_failed_transaction_leaves_the_record_as_it_was(self):
        with closing(db.connect(self.path)) as conn:
            with self.assertRaises(RuntimeError):
                with db.transaction(conn):
                    _put(conn, "count", "99")
                    raise RuntimeError("halfway")
            self.assertFalse(conn.in_transaction)
            self.assertEqual(_get(conn, "count"), "0")


if __name__ == "__main__":
    unittest.main()
