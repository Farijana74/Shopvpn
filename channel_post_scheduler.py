# -*- coding: utf-8 -*-
"""حلقه‌ی ارسال خودکار پست‌های زمان‌بندی‌شده‌ی کانال."""

import asyncio
import html
import logging
from datetime import datetime

import admin_channel_posts
import channel_suggestions
import report_router

logger = logging.getLogger(__name__)
POLL_SECONDS = 20


async def _notify(bot, admin_id, text: str):
    if not admin_id:
        return
    try:
        await bot.send_message(admin_id, text)
    except Exception:
        logger.warning("اعلان پست زمان‌بندی‌شده به ادمین %s ارسال نشد", admin_id)


async def channel_post_loop(bot, db):
    try:
        await asyncio.to_thread(db.recover_stuck_channel_posts)
    except Exception:
        logger.exception("recover_stuck_channel_posts failed")
    while True:
        try:
            now = datetime.utcnow().isoformat(timespec="seconds")
            for post in await asyncio.to_thread(db.get_due_channel_posts, now):
                if not await asyncio.to_thread(db.claim_channel_post, post["id"]):
                    continue
                ok, error = await admin_channel_posts.publish_record(bot, db, post["id"])
                if ok:
                    await asyncio.to_thread(
                        db.log_admin_action, post["admin_id"] or 0, "ai_channel_post",
                        f"پست زمان‌بندی‌شده #{post['id']} ارسال شد", "channel_post", post["id"],
                    )
                    await _notify(bot, post["admin_id"], f"✅ پست زمان‌بندی‌شده #{post['id']} در کانال ارسال شد.")
                    await report_router.notify(bot, db, "campaign", report_router.format_event(
                        "پست زمان‌بندی‌شده در کانال ارسال شد", [("پست", report_router.code(f"#{post['id']}"))], icon="📢"))
                else:
                    await _notify(bot, post["admin_id"], f"❌ ارسال پست زمان‌بندی‌شده #{post['id']} ناموفق بود: {html.escape(error)}")
                    await report_router.notify(bot, db, "campaign", report_router.format_event(
                        "ارسال پست زمان‌بندی‌شده ناموفق بود", [
                            ("پست", report_router.code(f"#{post['id']}")), ("خطا", str(error)[:300]),
                        ], icon="❌"))
            await channel_suggestions.tick(bot, db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("channel post loop failed")
        await asyncio.sleep(POLL_SECONDS)
