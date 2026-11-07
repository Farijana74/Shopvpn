"""Recognize cached Telegram reply-menu labels after emoji changes."""
import asyncio
import re
import hashlib
from collections import OrderedDict
import unicodedata
from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove


def label_key(text):
    text=re.sub(r'[0-9#*]\ufe0f?\u20e3','',text or '')
    text=''.join(c for c in text if unicodedata.category(c)[0] in ('L','N') and c!='ℹ')
    text=unicodedata.normalize('NFKC',text).translate(str.maketrans({'ي':'ی','ك':'ک'}))
    return ''.join(c for c in text.casefold() if unicodedata.category(c)[0] in ('L','N'))


def menu_snapshot(db,user_id,is_main_bot):
    import keyboards
    from db.constants import MENU_BUTTON_META,DEFAULT_SETTINGS
    from i18n import tr
    reply=keyboards.menu_for_user(db,user_id,is_main_bot)
    inline=keyboards.inline_menu_for_user(db,user_id,is_main_bot)
    if isinstance(reply,ReplyKeyboardMarkup):
        labels={b.text for row in reply.keyboard for b in row}
    else:
        labels={b.text for row in inline.inline_keyboard for b in row} if inline else set()
    aliases={}
    for text in labels:
        if label_key(text):aliases.setdefault(label_key(text),set()).add(text)
    known=set(aliases)
    for key,meta in MENU_BUTTON_META.items():
        default=meta.get('default_text') or DEFAULT_SETTINGS.get(key,'')
        current=db.get_setting(key,default)
        for old in (default,tr(default,'en')) if default else ():
            fingerprint=label_key(old)
            known.add(fingerprint)
            # Permissions/visibility come from the rendered menu, not text matching.
            for target in (current,tr(current)):
                if target in labels:aliases.setdefault(fingerprint,set()).add(target)
    return reply,inline,labels,aliases,known


class ReplyMenuCompatibilityMiddleware(BaseMiddleware):
    def __init__(self,db,is_main_bot=True):
        self.db=db;self.is_main_bot=is_main_bot
        self._revisions=OrderedDict()

    async def __call__(self,handler,event,data):
        text=getattr(event,'text',None)
        user=getattr(event,'from_user',None)
        if not text or not user or text.startswith('/') or getattr(event.chat,'type','private')!='private':
            return await handler(event,data)
        state=data.get('state')
        active=await state.get_state() if state else None
        # An admin may be entering a new label; that is form content, not navigation.
        if active and (active.startswith(('Admin','AgencyFlow:','ControlsFlow:')) or active=='VolumeFlow:settings'):
            return await handler(event,data)
        reply,inline,labels,aliases,known=await asyncio.to_thread(menu_snapshot,self.db,user.id,self.is_main_bot)
        key=label_key(text);targets=aliases.get(key,set())
        stale=text not in labels and bool(targets or key in known)
        recognized=text in labels or stale
        revision=hashlib.sha256((reply.model_dump_json(exclude_none=True)+(inline.model_dump_json(exclude_none=True) if inline is not None else '')).encode()).hexdigest()
        async def refresh():
            from menu_restore import menu_text
            text, options = menu_text(self.db)
            if inline is not None:
                if isinstance(reply, (ReplyKeyboardRemove, ReplyKeyboardMarkup)):
                    await event.answer('منو به‌روزرسانی شد.',reply_markup=reply)
                await event.answer(text,reply_markup=inline,**options)
            else:
                await event.answer(text,reply_markup=reply,**options)
            self._revisions[user.id]=revision
            self._revisions.move_to_end(user.id)
            if len(self._revisions)>4096:self._revisions.popitem(last=False)
        if recognized:
            if active and state:
                await state.clear()
                data['raw_state']=None
            if stale or self._revisions.get(user.id)!=revision:
                await refresh()
            if stale:
                if state and not active:await state.clear()
                data['raw_state']=None
                if len(targets)!=1:return None
                event=event.model_copy(update={'text':next(iter(targets)),'entities':None})
            return await handler(event,data)
        result=await handler(event,data)
        if result is UNHANDLED and not active:
            await refresh()
            return None
        return result
