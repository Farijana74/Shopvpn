import sqlite3
import unittest
from unittest.mock import patch

from db._base import DatabaseBase


class DatabaseStartupRetryTests(unittest.TestCase):
    def test_init_db_retries_transient_locked_database(self):
        database = DatabaseBase.__new__(DatabaseBase)
        calls = []

        def initialize_once(owner_id):
            calls.append(owner_id)
            if len(calls) < 3:
                raise sqlite3.OperationalError("database is locked")
            return "initialized"

        database._init_db_once = initialize_once
        with patch("db._base.time.sleep") as sleep:
            result = database.init_db(owner_id=123)

        self.assertEqual(result, "initialized")
        self.assertEqual(calls, [123, 123, 123])
        self.assertEqual(sleep.call_count, 2)

    def test_init_db_does_not_retry_non_lock_operational_errors(self):
        database = DatabaseBase.__new__(DatabaseBase)
        calls = []

        def initialize_once(owner_id):
            calls.append(owner_id)
            raise sqlite3.OperationalError("disk I/O error")

        database._init_db_once = initialize_once
        with patch("db._base.time.sleep") as sleep:
            with self.assertRaisesRegex(sqlite3.OperationalError, "disk I/O"):
                database.init_db(owner_id=123)

        self.assertEqual(calls, [123])
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
