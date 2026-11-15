"""Inspect only non-secret protocol details of the latest test panel."""
import asyncio
import json
import sqlite3
from pathlib import Path
from config import DB_PATH
from database import Database


def safe_label(value, allowed):
    value = str(value or '').lower()
    return value if value in allowed else 'other'


def decode(value):
    if isinstance(value, dict): return value
    try: return json.loads(value or '{}')
    except (ValueError, TypeError): return {}


async def main():
    path=Path(DB_PATH).resolve()
    if not path.is_file():
        print('دیتابیس اصلی پیدا نشد.')
        return
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
        row=conn.execute("SELECT panel_server_id,username FROM custom_configs WHERE source='test' AND panel_server_id IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        print('تست پنلی در دیتابیس اصلی پیدا نشد.')
        return
    db=Database(str(path))
    try:
        server=db.get_panel_server(row[0])
        if not server:
            print('پنل تست پیدا نشد.')
            return
        panel_type=safe_label(server['panel_type'], {'3xui','marzban','marzneshin','pasarguard','hiddify','alireza','rebecca','sui','wgdashboard','mikrotik','ibsng'})
        print('نوع پنل:',panel_type)
        if panel_type not in ('3xui','3x-ui','xui'):
            return
        from panel_providers import get_provider
        provider=get_provider(server)
        async with provider._session() as session:
            async with session.get(f'{provider._base_url()}/panel/api/inbounds/list') as response:
                if response.status != 200:
                    print('API پنل: کد HTTP',response.status)
                    return
                data=await response.json()
        inbounds=data.get('obj') or []
        matched=0
        for inbound in inbounds:
            settings=decode(inbound.get('settings'))
            if not any(c.get('email')==row[1] for c in settings.get('clients',[]) if isinstance(c,dict)):
                continue
            stream=decode(inbound.get('streamSettings'))
            print('اینباند:',safe_label(inbound.get('protocol'), {'vless','vmess','trojan','shadowsocks'}),
                  '| شبکه:',safe_label(stream.get('network') or 'tcp', {'tcp','raw','ws','grpc','xhttp','http','httpupgrade','kcp','quic'}),
                  '| امنیت:',safe_label(stream.get('security') or 'none', {'none','tls','reality'}))
            matched+=1
        print('تعداد اینباندهای این تست:',matched)
    except Exception as exc:
        # No exception text: panel errors may contain addresses, tokens or client data.
        print('بررسی پنل ناموفق بود:',type(exc).__name__)
    finally:
        db.close()

if __name__=='__main__':
    asyncio.run(main())
