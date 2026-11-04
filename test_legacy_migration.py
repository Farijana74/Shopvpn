import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from database import Database
from legacy_migration import prepare,apply_plan,connect,digest,MigrationError

class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.old=self.base/'old'/'bot_database.db';self.new=self.base/'new'/'bot_database.db'
        self.old.parent.mkdir();self.new.parent.mkdir()
        for path,uid,credit,name in [(self.old,303,100,'old'),(self.new,404,50,'new')]:
            db=Database(str(path));db.init_db(1,maintenance=True)
            category=db.add_category('Europe')
            server=db.add_panel_server('Panel','3xui','https://example.invalid','admin','secret')
            product=db.add_product(category,'Plan',100,is_auto_provision=True,provision_server_id=server)
            with db._get_conn() as c:
                c.execute('INSERT INTO users(telegram_id,referral_credit) VALUES(?,?)',(uid,credit))
                config=c.execute('INSERT INTO configs(product_id,link,is_used,assigned_user_id) VALUES(?,?,1,?)',(product,'https://example.invalid/'+name,uid)).lastrowid
            order=db.create_order(uid,product,100)
            with db._get_conn() as c:
                c.execute('UPDATE orders SET config_id=? WHERE id=?',(config,order))
                c.execute('UPDATE configs SET order_id=? WHERE id=?',(order,config))
            db.add_custom_config(uid,server,name,10,30,'https://example.invalid/'+name,order_id=order,source='direct_product')
            db.set_setting('welcome_text',name)
            db.close()
    def tearDown(self):self.tmp.cleanup()
    def fingerprint(self,path):
        c=connect(path,True)
        try:return digest(c)
        finally:c.close()
    def prep(self,folder='plan',**kwargs):
        kwargs.setdefault('mode','merge')
        return prepare(self.old,self.new,self.base/folder,1,**kwargs)

    def test_merge_remaps_cycles_and_preserves_balances_and_settings(self):
        before=self.fingerprint(self.old);target_before=self.fingerprint(self.new)
        report=self.prep();self.assertTrue(report['ready'],report)
        self.assertEqual(self.fingerprint(self.old),before)
        self.assertEqual(self.fingerprint(self.new),target_before)
        apply_plan(self.base/'plan',True)
        c=connect(self.new,True)
        try:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM orders').fetchone()[0],2)
            order=c.execute('SELECT * FROM orders WHERE user_id=303').fetchone()
            self.assertNotEqual(order['id'],1)
            config=c.execute('SELECT * FROM configs WHERE id=?',(order['config_id'],)).fetchone()
            self.assertEqual(config['order_id'],order['id'])
            self.assertEqual(config['assigned_user_id'],303)
            service=c.execute('SELECT * FROM custom_configs WHERE user_id=303').fetchone()
            self.assertEqual(service['order_id'],order['id'])
            self.assertEqual(c.execute('SELECT referral_credit FROM users WHERE telegram_id=303').fetchone()[0],100)
            self.assertEqual(c.execute('SELECT referral_credit FROM users WHERE telegram_id=404').fetchone()[0],50)
            self.assertEqual(c.execute("SELECT value FROM settings WHERE key='welcome_text'").fetchone()[0],'new')
            self.assertEqual(list(c.execute('PRAGMA foreign_key_check')),[])
        finally:c.close()
        self.assertEqual(self.fingerprint(self.old),before)
        self.assertTrue((self.base/'plan'/'before.db').is_file())
        self.assertEqual((self.base/'plan').stat().st_mode&0o777,0o700)
        self.assertEqual((self.base/'plan'/'result.db').stat().st_mode&0o777,0o600)

    def test_reimport_does_not_duplicate_or_restore_spent_money(self):
        self.assertTrue(self.prep()['ready']);apply_plan(self.base/'plan',True)
        c=connect(self.new)
        c.execute('UPDATE users SET referral_credit=80 WHERE telegram_id=303');c.commit();c.close()
        report=self.prep('again');self.assertTrue(report['ready'],report)
        apply_plan(self.base/'again',True)
        c=connect(self.new,True)
        self.assertEqual(c.execute('SELECT count(*) FROM orders').fetchone()[0],2)
        self.assertEqual(c.execute('SELECT referral_credit FROM users WHERE telegram_id=303').fetchone()[0],80)
        c.close()

    def test_financial_conflict_blocks_without_modifying_destination(self):
        c=connect(self.new);c.execute('INSERT INTO users(telegram_id,referral_credit) VALUES(303,77)');c.commit();c.close()
        before=self.fingerprint(self.new)
        report=self.prep();self.assertFalse(report['ready']);self.assertTrue(report['report']['conflicts'])
        self.assertEqual(self.fingerprint(self.new),before)
        with self.assertRaises(MigrationError):apply_plan(self.base/'plan',True)

    def test_changes_after_preview_block_apply(self):
        self.assertTrue(self.prep()['ready'])
        c=connect(self.new);c.execute('UPDATE users SET referral_credit=60 WHERE telegram_id=404');c.commit();c.close()
        changed=self.fingerprint(self.new)
        with self.assertRaises(MigrationError):apply_plan(self.base/'plan',True)
        self.assertEqual(self.fingerprint(self.new),changed)

    def test_corrupt_staged_file_and_running_services_are_rejected(self):
        self.assertTrue(self.prep()['ready'])
        with self.assertRaises(MigrationError):apply_plan(self.base/'plan')
        c=connect(self.base/'plan'/'result.db');c.execute('UPDATE users SET referral_credit=999999');c.commit();c.close()
        before=self.fingerprint(self.new)
        with self.assertRaises(MigrationError):apply_plan(self.base/'plan',True)
        self.assertEqual(self.fingerprint(self.new),before)

    def test_unknown_business_data_stops_merge(self):
        c=connect(self.old);c.execute('CREATE TABLE unknown_feature(id INTEGER PRIMARY KEY,value TEXT)');c.execute("INSERT INTO unknown_feature VALUES(1,'data')");c.commit();c.close()
        report=self.prep();self.assertFalse(report['ready'])
        self.assertEqual(report['report']['conflicts'][0]['table'],'unknown_feature')

    def test_upgrade_cannot_replace_used_store(self):
        report=self.prep(mode='upgrade');self.assertFalse(report['ready'])
        self.assertIn('مقصد اطلاعات دارد',report['error'])

    def test_upgrade_keeps_full_data_on_fresh_destination(self):
        target=self.base/'fresh'/'bot_database.db'
        target.parent.mkdir()
        report=prepare(self.old,target,self.base/'upgrade',1,mode='upgrade')
        self.assertTrue(report['ready'],report)
        apply_plan(self.base/'upgrade',True)
        c=connect(target,True)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM custom_configs').fetchone()[0],1)
        self.assertEqual(c.execute('SELECT referral_credit FROM users WHERE telegram_id=303').fetchone()[0],100)
        self.assertEqual(c.execute("SELECT value FROM settings WHERE key='welcome_text'").fetchone()[0],'old')
        c.close()
        self.assertTrue(apply_plan(self.base/'upgrade',True)['already_applied'])

    def test_upgrade_copies_reseller_database_and_rebases_root_path(self):
        child=self.old.parent/'reseller_dbs'/'oldchild.db';child.parent.mkdir()
        db=Database(str(child));db.init_db(303,maintenance=True);db.set_setting('managed_root_db_path',str(self.old));db.close()
        db=Database(str(self.old));db.register_reseller_bot('123:fake','oldchild',303,'Owner',str(child));db.close()
        target=self.base/'fresh'/'newname.db';target.parent.mkdir()
        report=prepare(self.old,target,self.base/'children',1,mode='upgrade')
        self.assertTrue(report['ready'],report)
        apply_plan(self.base/'children',True)
        new_child=target.parent/'reseller_dbs'/'oldchild.db'
        c=connect(target,True);self.assertEqual(c.execute('SELECT db_path FROM reseller_bots').fetchone()[0],str(new_child));c.close()
        c=connect(new_child,True);self.assertEqual(c.execute("SELECT value FROM settings WHERE key='managed_root_db_path'").fetchone()[0],str(target));c.close()

    def test_changed_source_is_rejected_at_apply(self):
        self.assertTrue(self.prep()['ready']);before=self.fingerprint(self.new)
        c=connect(self.old);c.execute('UPDATE users SET test_used=1');c.commit();c.close()
        with self.assertRaises(MigrationError):apply_plan(self.base/'plan',True)
        self.assertEqual(self.fingerprint(self.new),before)

    def test_existing_approved_topup_and_used_test_are_not_reopened(self):
        for path,status in [(self.old,'pending'),(self.new,'approved')]:
            c=connect(path)
            c.execute('INSERT OR IGNORE INTO users(telegram_id,referral_credit) VALUES(303,100)')
            c.execute("INSERT INTO wallet_topups(id,user_id,amount,status,created_at) VALUES(1,303,1000,?,'2020-01-01 00:00:00')",(status,))
            if path==self.old:c.execute('UPDATE users SET test_used=1 WHERE telegram_id=303')
            c.commit();c.close()
        report=self.prep();self.assertTrue(report['ready'],report);apply_plan(self.base/'plan',True)
        c=connect(self.new,True)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM wallet_topups').fetchone()[0],1)
        self.assertEqual(c.execute('SELECT status FROM wallet_topups').fetchone()[0],'approved')
        self.assertEqual(c.execute('SELECT test_used FROM users WHERE telegram_id=303').fetchone()[0],1)
        c.close()

    def test_keep_current_wallet_policy_is_explicit_and_reported(self):
        c=connect(self.new);c.execute('INSERT INTO users(telegram_id,referral_credit) VALUES(303,77)');c.commit();c.close()
        report=self.prep(wallet_policy='keep-current');self.assertTrue(report['ready'],report)
        self.assertTrue(report['report']['kept_current_financial'])
        apply_plan(self.base/'plan',True)
        c=connect(self.new,True);self.assertEqual(c.execute('SELECT referral_credit FROM users WHERE telegram_id=303').fetchone()[0],77);c.close()

    def test_automatic_mode_chooses_merge_for_used_and_upgrade_for_fresh(self):
        report=self.prep(mode='auto');self.assertTrue(report['ready'],report);self.assertEqual(report['mode'],'merge')
        fresh=self.base/'fresh.db'
        report=prepare(self.old,fresh,self.base/'auto-fresh',1,mode='auto')
        self.assertTrue(report['ready'],report);self.assertEqual(report['mode'],'upgrade')

    def test_imported_backup_is_recognized_after_source_file_moves(self):
        from legacy_migration import snapshot
        self.assertTrue(self.prep()['ready']);apply_plan(self.base/'plan',True)
        moved=self.base/'moved.db';snapshot(self.old,moved)
        report=prepare(moved,self.new,self.base/'moved-plan',1,mode='merge')
        self.assertTrue(report['ready'],report)
        self.assertTrue(report['report']['already_imported_snapshot'])
        apply_plan(self.base/'moved-plan',True)
        c=connect(self.new,True);self.assertEqual(c.execute('SELECT count(*) FROM orders').fetchone()[0],2);c.close()

    def test_full_upgrade_can_be_reimported_without_reverting_new_wallet_state(self):
        target=self.base/'fresh.db'
        report=prepare(self.old,target,self.base/'full-first',1,mode='auto')
        self.assertTrue(report['ready'],report);apply_plan(self.base/'full-first',True)
        c=connect(target);c.execute('UPDATE users SET referral_credit=80 WHERE telegram_id=303');c.commit();c.close()
        report=prepare(self.old,target,self.base/'full-again',1,mode='auto')
        self.assertTrue(report['ready'],report)
        self.assertTrue(report['report']['already_imported_snapshot'])
        apply_plan(self.base/'full-again',True)
        c=connect(target,True);self.assertEqual(c.execute('SELECT referral_credit FROM users WHERE telegram_id=303').fetchone()[0],80);c.close()

    def test_default_installation_with_owner_profile_is_fresh(self):
        target=self.base/'defaults.db'
        db=Database(str(target));db.init_db(1,maintenance=True)
        db.add_or_update_user(1,'owner','Owner')
        db.close()
        report=prepare(self.old,target,self.base/'default-plan',1,mode='upgrade')
        self.assertTrue(report['ready'],report)
        apply_plan(self.base/'default-plan',True)
        c=connect(target,True)
        self.assertEqual(c.execute('SELECT count(*) FROM orders').fetchone()[0],1)
        self.assertEqual(c.execute('SELECT referral_credit FROM users WHERE telegram_id=303').fetchone()[0],100)
        c.close()

    def test_owner_balance_makes_default_installation_nonempty(self):
        target=self.base/'defaults-with-credit.db'
        db=Database(str(target));db.init_db(1,maintenance=True)
        db.add_or_update_user(1,'owner','Owner')
        with db._get_conn() as conn:conn.execute('UPDATE users SET referral_credit=1 WHERE telegram_id=1')
        db.close()
        report=prepare(self.old,target,self.base/'credit-plan',1,mode='upgrade')
        self.assertFalse(report['ready'])

    def test_replacement_clears_stale_fsm_and_failed_publish_restores_it(self):
        fsm=Path(str(self.new)+'.fsm.sqlite3')
        c=connect(fsm);c.execute('CREATE TABLE fsm_storage(id INTEGER PRIMARY KEY,state TEXT)');c.execute("INSERT INTO fsm_storage VALUES(1,'old_order_receipt')");c.commit();c.close()
        before=self.fingerprint(fsm)
        report=prepare(self.old,self.new,self.base/'fsm-plan',1,mode='replace')
        self.assertTrue(report['ready'],report)
        with patch('legacy_migration._copy_contents',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):apply_plan(self.base/'fsm-plan',True)
        self.assertEqual(self.fingerprint(fsm),before)
        apply_plan(self.base/'fsm-plan',True)
        c=connect(fsm,True);self.assertEqual(c.execute('SELECT count(*) FROM fsm_storage').fetchone()[0],0);c.close()
        self.assertEqual(self.fingerprint(self.base/'fsm-plan'/'before-fsm.db'),before)

    def test_default_replacement_removes_existing_data_and_preserves_backup(self):
        before=self.fingerprint(self.new);source_before=self.fingerprint(self.old)
        report=prepare(self.old,self.new,self.base/'replace-plan',1)
        self.assertTrue(report['ready'],report)
        self.assertEqual(report['mode'],'replace')
        self.assertEqual(report['report']['replaced']['users'],1)
        apply_plan(self.base/'replace-plan',True)
        c=connect(self.new,True)
        self.assertIsNone(c.execute('SELECT 1 FROM users WHERE telegram_id=404').fetchone())
        self.assertIsNone(c.execute("SELECT 1 FROM custom_configs WHERE username='new'").fetchone())
        self.assertEqual(c.execute('SELECT referral_credit FROM users WHERE telegram_id=303').fetchone()[0],100)
        self.assertEqual(c.execute('SELECT count(*) FROM orders').fetchone()[0],1)
        self.assertEqual(c.execute("SELECT value FROM settings WHERE key='welcome_text'").fetchone()[0],'old')
        c.close()
        self.assertEqual(self.fingerprint(self.base/'replace-plan'/'before.db'),before)
        self.assertEqual(self.fingerprint(self.old),source_before)

    def test_replacement_keeps_conflicting_reseller_files_for_rollback(self):
        import json
        children=[]
        for main,owner,name in [(self.old,303,'old'),(self.new,404,'new')]:
            path=main.parent/'reseller_dbs'/'samechild.db';path.parent.mkdir()
            db=Database(str(path));db.init_db(owner,maintenance=True);db.set_setting('welcome_text',name);db.close()
            db=Database(str(main));bid=db.register_reseller_bot('123:fake','samechild',owner,'Owner',str(path))
            if main==self.old:
                with db._get_conn() as conn:
                    conn.execute("INSERT INTO managed_purchases(request_key,bot_id,local_order_id,root_order_id,charges,status) VALUES('trial',?,1,1,?,'reserved')",(bid,json.dumps([[str(path),owner,5]])))
            db.close();children.append(path)
        current_before=self.fingerprint(children[1])
        report=prepare(self.old,self.new,self.base/'replace-children',1)
        self.assertTrue(report['ready'],report)
        self.assertTrue(report['current_children_backups'])
        apply_plan(self.base/'replace-children',True)
        self.assertEqual(self.fingerprint(children[1]),current_before)
        c=connect(self.new,True)
        imported=Path(c.execute('SELECT db_path FROM reseller_bots').fetchone()[0])
        self.assertNotEqual(imported,children[1])
        charges=json.loads(c.execute("SELECT charges FROM managed_purchases WHERE request_key='trial'").fetchone()[0])
        self.assertEqual(charges[0][0],str(imported))
        c.close()
        c=connect(imported,True);self.assertEqual(c.execute("SELECT value FROM settings WHERE key='welcome_text'").fetchone()[0],'old');c.close()

    def test_same_source_destination_inode_is_rejected(self):
        import os
        hard=self.base/'hardlink.db';os.link(self.old,hard)
        with self.assertRaises(MigrationError):prepare(self.old,hard,self.base/'hard-plan',1)

    def test_maintenance_initialization_does_not_start_workers_or_scan(self):
        with patch('bulk_gifts.ensure_worker') as worker,patch.object(Database,'_sync_text_registry') as scan:
            db=Database(str(self.base/'maintenance.db'))
            try:db.init_db(1,maintenance=True)
            finally:db.close()
        worker.assert_not_called();scan.assert_not_called()

    def test_failed_publish_rolls_back_every_destination_table(self):
        self.assertTrue(self.prep()['ready']);before=self.fingerprint(self.new)
        from legacy_migration import _copy_contents
        def fail_after_copy(source,dst):
            _copy_contents(source,dst)
            raise RuntimeError('simulated failure')
        with patch('legacy_migration._copy_contents',side_effect=fail_after_copy):
            with self.assertRaises(RuntimeError):apply_plan(self.base/'plan',True)
        self.assertEqual(self.fingerprint(self.new),before)
