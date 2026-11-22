import os
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from backup import _restore_sqlite_in_place, restore_full_backup
from database import Database


def make_db(path, script):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(script)


class FullRestoreTests(unittest.TestCase):
    def test_restore_while_another_connection_holds_wal_open(self):
        with tempfile.TemporaryDirectory() as root:
            old = os.path.join(root, 'old.db')
            source = os.path.join(root, 'source.db')
            pre = os.path.join(root, 'before.db')
            make_db(old, "CREATE TABLE t(v); INSERT INTO t VALUES ('old');")
            make_db(source, "CREATE TABLE t(v); INSERT INTO t VALUES ('new');")
            with sqlite3.connect(old) as live:
                live.execute('PRAGMA journal_mode=WAL')
                live.execute("INSERT INTO t VALUES ('wal-only')")
                live.commit()
                _restore_sqlite_in_place(source, old, pre)
                self.assertEqual(live.execute('SELECT v FROM t').fetchall(), [('new',)])
                live.execute("INSERT INTO t VALUES ('after')")
                live.commit()
            with sqlite3.connect(pre) as before:
                self.assertEqual(before.execute('SELECT v FROM t').fetchall(), [('old',), ('wal-only',)])
            with sqlite3.connect(old) as result:
                self.assertEqual(result.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(result.execute('SELECT v FROM t').fetchall(), [('new',), ('after',)])

    def test_full_restore_moves_reseller_paths_and_keeps_live_connections(self):
        with tempfile.TemporaryDirectory() as root:
            dest_main = os.path.join(root, 'current', 'main.db')
            source_main = os.path.join(root, 'input', 'main.db')
            source_reseller = os.path.join(root, 'input', 'reseller.db')
            dest_resellers = os.path.join(root, 'current', 'reseller_dbs')
            dest_reseller = os.path.join(dest_resellers, 'agent.db')
            schema = "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT); CREATE TABLE reseller_bots(id INTEGER PRIMARY KEY, bot_username TEXT, db_path TEXT);"
            make_db(dest_main, schema + "INSERT INTO settings VALUES ('marker','before');")
            make_db(source_main, schema + "INSERT INTO settings VALUES ('marker','restored'); INSERT INTO reseller_bots VALUES (7,'agent','/old/server/reseller_dbs/agent.db');")
            make_db(source_reseller, "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT); INSERT INTO settings VALUES ('managed_root_db_path','/old/server/main.db'); CREATE TABLE users(id); INSERT INTO users VALUES (123);")
            make_db(dest_reseller, "CREATE TABLE users(id); INSERT INTO users VALUES (999);")
            archive = os.path.join(root, 'backup.zip')
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.write(source_main, 'main_bot.db')
                zf.write(source_reseller, 'reseller_7.db')
            db = Database(dest_main)
            live_main = sqlite3.connect(dest_main)
            live_reseller = sqlite3.connect(dest_reseller)
            try:
                live_main.execute('PRAGMA journal_mode=WAL')
                live_reseller.execute('PRAGMA journal_mode=WAL')
                with patch('config.RESELLER_DBS_DIR', dest_resellers):
                    result = restore_full_backup(db, dest_main, archive)
                self.assertEqual(len(result['resellers_restored']), 1)
                self.assertEqual(result['resellers_skipped'], [])
                self.assertEqual(live_main.execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0], 'restored')
                self.assertEqual(live_main.execute('SELECT db_path FROM reseller_bots').fetchone()[0], dest_reseller)
                self.assertEqual(live_reseller.execute('SELECT id FROM users').fetchall(), [(123,)])
                self.assertEqual(live_reseller.execute("SELECT value FROM settings WHERE key='managed_root_db_path'").fetchone()[0], dest_main)
                self.assertEqual(live_reseller.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            finally:
                db.close()
                live_main.close()
                live_reseller.close()

    def test_archive_path_traversal_does_not_change_database(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, 'main.db')
            make_db(target, "CREATE TABLE t(v); INSERT INTO t VALUES ('original');")
            archive = os.path.join(root, 'bad.zip')
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('../escape', 'bad')
            db = Database(target)
            try:
                with self.assertRaises(ValueError):
                    restore_full_backup(db, target, archive)
                with sqlite3.connect(target) as conn:
                    self.assertEqual(conn.execute('SELECT v FROM t').fetchone()[0], 'original')
                self.assertFalse(os.path.exists(os.path.join(root, 'escape')))
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
