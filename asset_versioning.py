#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""Content-hash asset versions and no-store API headers shared by the admin panel and mini app."""

import hashlib
import os

from starlette.datastructures import MutableHeaders

_STATIC_EXTENSIONS = (".html", ".js", ".css")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
_digest_cache = {}


def file_digest(path: str) -> str:
    """Short content hash of a file, recomputed only when its mtime or size changes."""
    try:
        st = os.stat(path)
    except OSError:
        return "0"
    key = (st.st_mtime_ns, st.st_size)
    hit = _digest_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    with open(path, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()[:10]
    _digest_cache[path] = (key, digest)
    return digest


def static_version(static_dir: str) -> str:
    """Single version string that changes whenever any html/js/css file in the directory changes."""
    try:
        names = sorted(n for n in os.listdir(static_dir) if n.endswith(_STATIC_EXTENSIONS))
    except OSError:
        return "0"
    joined = "".join(file_digest(os.path.join(static_dir, n)) for n in names)
    return hashlib.sha1(joined.encode()).hexdigest()[:10]


class ApiNoStoreMiddleware:
    """Adds Cache-Control: no-store to every /api/ response that does not set its own."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_header)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
