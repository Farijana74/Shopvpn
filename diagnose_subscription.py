"""Check the latest test subscription without printing its URL or credentials."""
import asyncio
import os
import socket
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from config import DB_PATH


def latest_test_url():
    path = Path(DB_PATH).resolve()
    if not path.is_file():
        raise RuntimeError('دیتابیس تنظیم‌شده پیدا نشد.')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        for query in (
            "SELECT subscription_url FROM custom_configs WHERE source='test' AND (subscription_url LIKE 'https://%' OR subscription_url LIKE 'http://%') ORDER BY id DESC LIMIT 1",
            "SELECT link FROM test_configs WHERE is_used=1 AND (link LIKE 'https://%' OR link LIKE 'http://%') ORDER BY id DESC LIMIT 1",
        ):
            try:
                row = conn.execute(query).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                return row[0]
    finally:
        conn.close()
    raise RuntimeError('لینک ساب تست در دیتابیس اصلی پیدا نشد.')


async def main():
    try:
        url = latest_test_url()
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('invalid URL')
        host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
    except RuntimeError as exc:
        print('خطا:', str(exc))
        return
    except ValueError:
        print('خطا: آدرس ساب تست معتبر نیست.')
        return
    print('ساب تست از دیتابیس اصلی انتخاب شد؛ آدرس و توکن نمایش داده نمی‌شود.')
    try:
        records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 4)
    except (OSError, asyncio.TimeoutError) as exc:
        print('DNS: ناموفق -', type(exc).__name__)
        return
    families = {record[0] for record in records}
    print('DNS: موفق؛ IPv4:', 'دارد' if socket.AF_INET in families else 'ندارد', '؛ IPv6:', 'دارد' if socket.AF_INET6 in families else 'ندارد')
    for family, label in ((socket.AF_INET, 'IPv4'), (socket.AF_INET6, 'IPv6')):
        candidates = [record for record in records if record[0] == family]
        if not candidates:
            continue
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(candidates[0][4][0], port, family=family), 4)
            writer.close()
            await writer.wait_closed()
            print('اتصال TCP', label, ': موفق')
        except (OSError, asyncio.TimeoutError) as exc:
            print('اتصال TCP', label, ': ناموفق -', type(exc).__name__)
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False), timeout=timeout) as session:
            async with session.get(url, headers={'User-Agent': 'v2rayNG/1.8.29'}) as response:
                print('HTTP: کد', response.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print('HTTP: ناموفق -', type(exc).__name__)
    print('پراکسی محیطی تنظیم شده:', 'بله' if any(os.environ.get(k) for k in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy')) else 'خیر')


if __name__ == '__main__':
    asyncio.run(main())
