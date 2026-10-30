"""Regression test for sqlite3.Row order objects in direct SSH provisioning."""
import sqlite3
import unittest

from direct_panel_provision import _order_config_name


class OrderConfigNameTests(unittest.TestCase):
    def test_reads_sqlite_row(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE orders (config_name TEXT)")
        conn.execute("INSERT INTO orders (config_name) VALUES (?)", ("My-SSH",))
        row = conn.execute("SELECT config_name FROM orders").fetchone()
        self.assertEqual(_order_config_name(row), "My-SSH")
        conn.close()

    def test_reads_mapping_and_missing_order(self):
        self.assertEqual(_order_config_name({"config_name": "user_1"}), "user_1")
        self.assertEqual(_order_config_name(None), "")


if __name__ == "__main__":
    unittest.main()
