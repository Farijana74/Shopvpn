"""Keep the existing admin menu until a replacement has been delivered."""
import logging
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


async def replace_view(call, text, reply_markup=None, parse_mode=None):
    if call.message is None:
        return False
    kwargs = {'reply_markup': reply_markup}
    if parse_mode is not None:
        kwargs['parse_mode'] = parse_mode
    try:
        await call.message.edit_text(text, **kwargs)
        return True
    except TelegramBadRequest as exc:
        if 'message is not modified' in str(exc).lower():
            return True
        logger.warning('Admin menu edit rejected (%s)', type(exc).__name__)
        from aiogram.methods import EditMessageText
        from inline_navigation import without_custom_icons
        fallback = without_custom_icons(EditMessageText(chat_id=1, message_id=1, text=text, reply_markup=reply_markup))
        if fallback is not None:
            kwargs['reply_markup'] = fallback.reply_markup
            try:
                await call.message.edit_text(text, **kwargs)
                return True
            except TelegramBadRequest as retry_exc:
                if 'message is not modified' in str(retry_exc).lower():
                    return True
            except Exception:
                pass
    except Exception as exc:
        logger.warning('Admin menu edit failed (%s)', type(exc).__name__)
    # Deliver first. An API rejection or connection failure must not erase the
    # only working menu, including when the fallback send fails as well.
    try:
        await call.message.answer(text, **kwargs)
    except Exception as exc:
        logger.warning('Admin menu replacement failed; previous menu retained (%s)', type(exc).__name__)
        return False
    try:
        await call.message.delete()
    except Exception:
        pass
    return True
