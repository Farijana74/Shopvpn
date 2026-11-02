import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from database import Database


class StartupUnlockTests(unittest.TestCase):
    def test_other_process_can_write_while_text_registry_is_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'startup.db')
            db = Database(path)
            checked = []
            def scan():
                self.assertFalse(db._conn.in_transaction)
                with sqlite3.connect(path, timeout=0.1) as other:
                    other.execute('BEGIN IMMEDIATE')
                    other.execute("UPDATE users SET language_code='fa' WHERE 0")
                checked.append(True)
            db._sync_text_registry = scan
            worker = types.SimpleNamespace(ensure_worker=lambda database: None)
            try:
                with patch.dict('sys.modules', {'bulk_gifts': worker}):
                    db.init_db(owner_id=123)
                    db.init_db(owner_id=123)
                self.assertEqual(checked, [True, True])
            finally:
                db.close()
