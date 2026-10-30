# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""نمایش زنده‌ی پاسخ AI در چت خصوصی با sendMessageDraft (Bot API 9.5 به بعد).

پیش‌نویس موقت است (حدود ۳۰ ثانیه) و با ارسال پیام نهایی از بین می‌رود؛ پیام کامل همیشه باید
جداگانه با send_message ارسال شود. هر خطای ارسال پیش‌نویس، استریم را برای همان پاسخ خاموش می‌کند
و روی ارسال پیام نهایی اثری ندارد.
"""

import asyncio
import logging
import random
import time
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

logger = logging.getLogger(__name__)

_MAX_TEXT = 4000


class DraftStreamer:
    def __init__(self, bot, chat_id: int, min_interval: float = 0.8):
        self._bot = bot
        self._chat_id = chat_id
        self._min_interval = min_interval
        self._draft_id = random.randint(1, 2_000_000_000)
        self._latest = None
        self._last_sent = None
        self._last_sent_at = 0.0
        self._task = None
        self.disabled = False

    async def _send(self, text: str) -> bool:
        try:
            await self._bot.send_message_draft(
                chat_id=self._chat_id, draft_id=self._draft_id, text=text, parse_mode=None,
            )
        except Exception as exc:
            logger.warning("sendMessageDraft برای چت %s ناموفق بود؛ استریم خاموش شد: %s", self._chat_id, exc)
            self.disabled = True
            return False
        self._last_sent = text
        self._last_sent_at = time.monotonic()
        return True

    async def start(self) -> None:
        """متن خالی، جای‌نگهدار «Thinking...» تلگرام را نشان می‌دهد."""
        if not self.disabled:
            await self._send("")

    async def _flush_loop(self) -> None:
        while not self.disabled and self._latest is not None and self._latest != self._last_sent:
            wait = self._min_interval - (time.monotonic() - self._last_sent_at)
            if wait > 0:
                await asyncio.sleep(wait)
            text = self._latest
            if text is None or text == self._last_sent:
                break
            await self._send(text)

    async def update(self, full_text: str) -> None:
        """کل متنِ تا اینجا را می‌گیرد؛ فقط آخرین مقدار با فاصله‌ی حداقلی ارسال می‌شود."""
        if self.disabled:
            return
        text = (full_text or "").strip()
        if not text:
            return
        self._latest = text[:_MAX_TEXT]
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._flush_loop())

    async def close(self) -> None:
        """قبل از ارسال پیام نهایی صدا زده شود تا هیچ پیش‌نویس دیرهنگامی بعد از پیام نهایی ظاهر نشود."""
        self.disabled = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
