# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""هدیه‌ی گروهی حجم/زمان با صف پایدار و قابل ادامه بعد از ری‌استارت."""
import asyncio
import json
import logging
import threading
import time
from datetime import datetime

from panel_providers import get_provider, PanelError
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

logger = logging.getLogger(__name__)
_workers = {}
_workers_lock = threading.Lock()


def ensure_worker(db):
    key = db.db_path
    with _workers_lock:
        if key in _workers and _workers[key].is_alive():
            return
        t = threading.Thread(target=_worker, args=(db,), daemon=True, name=f"bulk-gift-{abs(hash(key))}")
        _workers[key] = t
        t.start()


def _worker(db):
    while True:
        try:
            job = db.claim_next_bulk_gift_item()
            if not job:
                time.sleep(3)
                continue
            _process_item(db, job)
        except Exception:
            logger.exception("bulk gift worker failed")
            time.sleep(3)


def _process_item(db, item):
    job = db.get_bulk_gift_job(item["job_id"])
    if not job:
        db.fail_bulk_gift_item(item["id"], "job not found")
        return
    params = json.loads(job["params_json"] or "{}")
    server = db.get_panel_server(item["panel_server_id"])
    if not server or not server["is_active"]:
        db.fail_bulk_gift_item(item["id"], "پنل غیرفعال یا حذف شده است")
        return
    # سرویس On-hold که هنوز اولین اتصالش رخ نداده، با افزایش زمان معمولی از حالت‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    # انتظار خارج می‌شود؛ بنابراین هدیه‌ی زمانی را عمداً رد می‌کنیم.‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    if item["start_on_first_use"] and not item["expires_at"] and int(params.get("days") or 0) > 0:
        db.fail_bulk_gift_item(item["id"], "سرویس On-hold هنوز اولین اتصال را نداشته است؛ هدیه‌ی زمانی اعمال نشد")
        return
    try:
        provider = get_provider(server)
        result = asyncio.run(provider.update_user(
            item["username"],
            add_volume_gb=float(params.get("volume_gb") or 0),
            add_days=int(params.get("days") or 0),
            reset_usage=False,
            preserve_remaining=True,
        ))
        db.apply_bulk_gift_success(
            item["id"], item["custom_config_id"],
            float(params.get("volume_gb") or 0), int(params.get("days") or 0),
            (getattr(result, "raw", None) or {}).get("expires_at") if result else None,
        )
    except Exception as exc:
        db.fail_bulk_gift_item(item["id"], str(exc)[:500])


def start_job(db, *, admin_id, panel_server_id=None, user_ids=None, volume_gb=0, days=0, note=""):
    ensure_worker(db)
    return db.create_bulk_gift_job(
        admin_id=admin_id, panel_server_id=panel_server_id, user_ids=user_ids or [],
        volume_gb=volume_gb, days=days, note=note,
    )
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
