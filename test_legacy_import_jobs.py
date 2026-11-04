import asyncio
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import AsyncMock,patch
import test_legacy_migration as migration_tests
import legacy_import_jobs as jobs
from legacy_migration import connect,MigrationError
from admin_panel.security import create_session_token,verify_session_token

class ImportJobTests(unittest.TestCase):
    def setUp(self):
        self.f=migration_tests.LegacyMigrationTests();self.f.setUp()
        self.patches=[patch.object(jobs,'JOBS_DIR',self.f.base/'jobs'),patch.object(jobs,'DB_PATH',str(self.f.new)),patch.object(jobs,'OWNER_ID',1),patch.object(jobs,'LOCAL_SOURCE',self.f.old)]
        for p in self.patches:p.start()
        self.principal='web:1'
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.f.tearDown()
    def preview(self):
        state=jobs.new_job(self.principal,True)
        result=jobs.prepare_job(state['job_id'],self.principal)
        self.assertEqual(result['status'],'ready',result)
        return state['job_id']

    def test_preview_and_cancellation_do_not_change_live_database(self):
        before=self.f.fingerprint(self.f.new)
        job_id=self.preview()
        self.assertEqual(jobs.cancel_job(job_id,self.principal)['status'],'cancelled')
        self.assertEqual(self.f.fingerprint(self.f.new),before)

    def test_confirmation_owner_binding_and_duplicate_queue(self):
        job_id=self.preview();launch=[]
        with self.assertRaises(MigrationError):jobs.queue_job(job_id,'web:2','REPLACE',launch.append)
        with self.assertRaises(MigrationError):jobs.queue_job(job_id,self.principal,'yes',launch.append)
        jobs.queue_job(job_id,self.principal,'REPLACE',launch.append)
        jobs.queue_job(job_id,self.principal,'REPLACE',launch.append)
        self.assertEqual(launch,[job_id])
        with self.assertRaises(MigrationError):jobs.load('../../etc/passwd',self.principal)

    def test_two_different_jobs_cannot_replace_the_database_concurrently(self):
        first=self.preview();second=self.preview();launched=[]
        jobs.queue_job(first,self.principal,'REPLACE',launched.append)
        with self.assertRaises(MigrationError):jobs.queue_job(second,self.principal,'REPLACE',launched.append)
        self.assertEqual(launched,[first])
        self.assertEqual(jobs.load(second,self.principal)['status'],'ready')

    def test_launcher_failure_preserves_ready_preview_and_live_data(self):
        job_id=self.preview();before=self.f.fingerprint(self.f.new)
        def fail(_):raise RuntimeError('systemd unavailable')
        with self.assertRaises(MigrationError):jobs.queue_job(job_id,self.principal,'REPLACE',fail)
        self.assertEqual(jobs.load(job_id,self.principal)['status'],'ready')
        self.assertEqual(self.f.fingerprint(self.f.new),before)

    def test_worker_survives_stopping_services_and_preserves_owner_login(self):
        c=connect(self.f.new)
        c.execute("INSERT INTO web_admins(id,username,password_hash,role) VALUES(1,'current-owner','hash-current','owner')")
        c.commit();c.close()
        c=connect(self.f.old)
        c.execute("INSERT INTO web_admins(id,username,password_hash,role) VALUES(1,'old-admin','hash-old','admin')")
        c.execute("INSERT INTO mobile_app_tokens(admin_id,name,token_hash,token_prefix) VALUES(1,'old-device','old-token','old')")
        c.commit();c.close()
        job_id=self.preview();jobs.queue_job(job_id,self.principal,'REPLACE',lambda _:None)
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0 if argv[1]!='is-active' or argv[-1] in ('shopvpn-independent','shopvpn-independent-adminpanel','v2raybot') else 3)
        result=jobs.run_job(job_id,runner=runner,delay=0,notify=False)
        self.assertEqual(result['status'],'completed',result)
        self.assertIn(['systemctl','stop','shopvpn-independent'],calls)
        self.assertIn(['systemctl','start','shopvpn-independent-adminpanel'],calls)
        self.assertNotIn(['systemctl','start','v2raybot'],calls)
        c=connect(self.f.new,True)
        self.assertIsNone(c.execute('SELECT 1 FROM users WHERE telegram_id=404').fetchone())
        self.assertEqual(c.execute('SELECT username,password_hash FROM web_admins').fetchone()[0],'current-owner')
        self.assertEqual(c.execute('SELECT password_hash FROM web_admins').fetchone()[0],'hash-current')
        self.assertEqual(c.execute('SELECT count(*) FROM mobile_app_tokens').fetchone()[0],0)
        epoch=c.execute("SELECT value FROM settings WHERE key='admin_auth_epoch'").fetchone()[0]
        self.assertTrue(epoch)
        old=verify_session_token('secret',create_session_token('secret',1,'current-owner','owner'))
        self.assertNotEqual(old['ae'],epoch)
        new=verify_session_token('secret',create_session_token('secret',1,'current-owner','owner',auth_epoch=epoch))
        self.assertEqual(new['ae'],epoch)
        c.close()
        self.assertTrue((jobs.folder(job_id)/'execution'/'before.db').is_file())

    def test_failure_after_commit_does_not_restart_the_old_bot(self):
        job_id=self.preview();jobs.queue_job(job_id,self.principal,'REPLACE',lambda _:None)
        real_apply=jobs.apply_plan;calls=[]
        def apply_then_error(*args,**kwargs):
            real_apply(*args,**kwargs)
            raise OSError('post-commit report failed')
        def runner(argv,**kwargs):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0 if argv[1]!='is-active' or argv[-1] in ('shopvpn-independent','v2raybot') else 3)
        with patch.object(jobs,'apply_plan',side_effect=apply_then_error):
            result=jobs.run_job(job_id,runner=runner,delay=0,notify=False)
        self.assertEqual(result['status'],'completed',result)
        self.assertNotIn(['systemctl','start','v2raybot'],calls)
        self.assertIn(['systemctl','start','shopvpn-independent'],calls)

    def test_worker_failure_resumes_old_and_new_services_without_publishing(self):
        job_id=self.preview();jobs.queue_job(job_id,self.principal,'REPLACE',lambda _:None)
        before=self.f.fingerprint(self.f.new);calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0 if argv[1]!='is-active' or argv[-1] in ('shopvpn-independent','v2raybot') else 3)
        with patch.object(jobs,'apply_plan',side_effect=RuntimeError('failure before publish')):
            result=jobs.run_job(job_id,runner=runner,delay=0,notify=False)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.f.fingerprint(self.f.new),before)
        self.assertIn(['systemctl','start','v2raybot'],calls)
        self.assertIn(['systemctl','start','shopvpn-independent'],calls)

    def test_uploaded_full_zip_replaces_data_and_imports_reseller(self):
        import zipfile
        from database import Database
        child=self.f.old.parent/'oldchild.db'
        db=Database(str(child));db.init_db(303,maintenance=True)
        db.set_setting('managed_root_db_path',str(self.f.old));db.close()
        db=Database(str(self.f.old));db.register_reseller_bot('123:fake','oldchild',303,'Owner',str(child));db.close()
        before=self.f.fingerprint(self.f.new)
        job=jobs.new_job(self.principal)['job_id']
        with zipfile.ZipFile(jobs.folder(job)/'source.db','w') as zf:
            zf.write(self.f.old,'main_bot.db');zf.write(child,'reseller_1.db')
            zf.writestr('manifest.txt',f'main_bot.db  <-  {self.f.old}\n')
        jobs.unpack_backup(job,self.principal)
        preview=jobs.prepare_job(job,self.principal)
        self.assertEqual(preview['status'],'ready',preview)
        self.assertEqual(before,self.f.fingerprint(self.f.new))
        jobs.queue_job(job,self.principal,'REPLACE',lambda _:None)
        result=jobs.run_job(job,runner=lambda *a,**k:types.SimpleNamespace(returncode=3),delay=0,notify=False)
        self.assertEqual(result['status'],'completed',result)
        conn=connect(self.f.new,True)
        self.assertIsNone(conn.execute('SELECT 1 FROM users WHERE telegram_id=404').fetchone())
        self.assertIsNotNone(conn.execute('SELECT 1 FROM users WHERE telegram_id=303').fetchone())
        imported=Path(conn.execute('SELECT db_path FROM reseller_bots').fetchone()[0]);conn.close()
        conn=connect(imported,True)
        self.assertEqual(conn.execute("SELECT value FROM settings WHERE key='managed_root_db_path'").fetchone()[0],str(self.f.new));conn.close()
        self.assertTrue((jobs.folder(job)/'execution'/'before.db').is_file())

    def test_zip_rejects_unsafe_and_ambiguous_backups(self):
        import zipfile
        before=self.f.fingerprint(self.f.new)
        for entries in ({'../escape.db':b'data'},{'first.db':b'data','second.db':b'data'}):
            job=jobs.new_job(self.principal)['job_id']
            with zipfile.ZipFile(jobs.folder(job)/'source.db','w') as zf:
                for name,data in entries.items():zf.writestr(name,data)
            with self.assertRaises(MigrationError):jobs.unpack_backup(job,self.principal)
        self.assertEqual(before,self.f.fingerprint(self.f.new))
