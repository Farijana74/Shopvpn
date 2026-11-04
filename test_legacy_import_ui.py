import asyncio
import types
import unittest
from unittest.mock import AsyncMock,patch
import httpx
import config
import legacy_import_jobs as jobs
import test_legacy_import_jobs as job_tests

class ImportUITests(unittest.TestCase):
    # Reuse only fixture setup; imported parent scenarios remain independently useful.
    def setUp(self):
        job_tests.ImportJobTests.setUp(self)
        from database import Database
        original=Database.init_db
        with patch.object(config,'DB_PATH',str(self.f.new)),patch.object(config,'OWNER_ID',1),patch.object(Database,'init_db',lambda self,owner_id,**kwargs:original(self,owner_id,maintenance=True)):
            import admin_panel.server as server
        self.server=server
        server.main_db=Database(str(self.f.new))
        server.MAIN_TENANT=server.Tenant(slug='',bot_id=None,db=server.main_db,db_path=str(self.f.new),bot_token=config.BOT_TOKEN,backup_dir=str(self.f.new.parent/'backups'))
        server._current_tenant.set(server.MAIN_TENANT)
        self.admin={'id':1,'username':'owner','role':'owner','permissions':[],'tenant':''}
        server.app.dependency_overrides[server.get_current_admin]=lambda:self.admin
    def tearDown(self):
        self.server.app.dependency_overrides.clear()
        self.server.main_db.close()
        job_tests.ImportJobTests.tearDown(self)

    def test_web_preview_confirm_cancel_and_authorization(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.server.app),base_url='http://test') as client:
                before=self.f.fingerprint(self.f.new)
                self.admin['role']='admin'
                response=await client.post('/api/system/legacy-import/local-preview')
                self.assertEqual(response.status_code,403)
                self.admin.update(role='owner',tenant='child')
                self.assertEqual((await client.post('/api/system/legacy-import/local-preview')).status_code,403)
                self.admin['tenant']=''
                response=await client.post('/api/system/legacy-import/preview',files={'file':('old.db',self.f.old.read_bytes(),'application/octet-stream')})
                self.assertEqual(response.status_code,200,response.text)
                job=response.json()['job_id']
                self.assertEqual(self.f.fingerprint(self.f.new),before)
                self.admin['id']=2
                self.assertEqual((await client.get('/api/system/legacy-import/'+job)).status_code,404)
                self.admin['id']=1
                self.assertEqual((await client.post('/api/system/legacy-import/'+job+'/confirm',data={'confirm_phrase':'yes'})).status_code,400)
                launched=[]
                with patch.object(jobs,'launch',side_effect=launched.append):
                    for _ in range(2):
                        response=await client.post('/api/system/legacy-import/'+job+'/confirm',data={'confirm_phrase':'REPLACE'})
                        self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(launched,[job])
                self.assertEqual(self.f.fingerprint(self.f.new),before)
                second=await client.post('/api/system/legacy-import/local-preview')
                self.assertEqual(second.status_code,200,second.text)
                import io,zipfile
                archive=io.BytesIO()
                with zipfile.ZipFile(archive,'w') as zf:zf.write(self.f.old,'main_bot.db')
                zipped=await client.post('/api/system/legacy-import/preview',files={'file':('backup.zip',archive.getvalue(),'application/zip')})
                self.assertEqual(zipped.status_code,200,zipped.text)
                self.assertEqual(zipped.json()['status'],'ready')
                second_id=second.json()['job_id']
                self.assertEqual((await client.post('/api/system/legacy-import/'+second_id+'/cancel')).json()['status'],'cancelled')
        asyncio.run(run())

    def test_replaced_database_epoch_rejects_old_cookie(self):
        from starlette.requests import Request
        from fastapi import HTTPException
        from admin_panel.security import create_session_token
        token=create_session_token(self.server.ADMIN_PANEL_SECRET,1,'owner','owner')
        self.server.main_db.set_setting('admin_auth_epoch','new-epoch')
        request=Request({'type':'http','method':'GET','path':'/','headers':[(b'cookie',(self.server.COOKIE_NAME+'='+token).encode())]})
        async def run():
            with self.assertRaises(HTTPException) as caught:await self.server.get_current_admin(request)
            self.assertEqual(caught.exception.status_code,401)
            self.assertIn("تغییر دیتابیس",str(caught.exception.detail))
        asyncio.run(run())

    def test_bot_owner_guard_and_preview_menu(self):
        from database import Database
        from handlers_admin import create_admin_router
        db=Database(str(self.f.new))
        router=create_admin_router(db,True)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='legacy_callback')
        call=types.SimpleNamespace(data='legacyui:start',from_user=types.SimpleNamespace(id=404),message=types.SimpleNamespace(answer=AsyncMock()),answer=AsyncMock())
        state=AsyncMock()
        async def run():
            await callback(call,state)
            call.message.answer.assert_not_awaited()
            self.assertTrue(call.answer.call_args.kwargs['show_alert'])
            call.from_user.id=1
            await callback(call,state)
            state.set_state.assert_awaited_once()
            self.assertIn('فایل بکاپ',call.message.answer.call_args.args[0])
            buttons=call.message.answer.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertTrue(any(b.callback_data=='legacyui:upload' for row in buttons for b in row))
            call.data='legacyui:upload'
            await callback(call,state)
            self.assertIn('.zip',call.message.answer.call_args.args[0])
        try:asyncio.run(run())
        finally:db.close()
