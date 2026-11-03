"""Settings keys and validation for native Telegram custom button icons."""
import re


def validate_icon(value):
    value = str(value or '').strip()
    if value and not re.fullmatch(r'[0-9]{1,32}', value):
        raise ValueError('شناسه ایموجی پرمیوم باید فقط عدد باشد؛ برای حذف، خالی بگذارید.')
    return value


def icon_key(group, key):
    if group == 'main_menu':
        prefix = key
    elif group == 'admin_categories':
        prefix = f'catlbl_{key}'
    elif group.startswith('admin_items__') or group in ('buyflow', 'buyflow_confirm', 'account_hub'):
        prefix = key
    elif group == 'payment_methods':
        prefix = f'paymeth_customgw_{key.split(":", 1)[1]}' if key.startswith('customgw:') else f'paymeth_{key}'
    else:
        prefix = f'ubtn_{key}'
    return prefix + '_icon_custom_emoji_id'


def button_icon(db, style_key):
    key = style_key.removesuffix('_style') + '_icon_custom_emoji_id'
    value = str(db.get_setting(key, '') or '').strip()
    return value if re.fullmatch(r'[0-9]{1,32}', value) else None


def message_button_label(message, fallback_label=None):
    """Extract an emoji entity, sticker or explicit ``name | emoji_id`` input."""
    text = getattr(message, 'text', None) or getattr(message, 'caption', None) or ''
    entities = getattr(message, 'entities', None) or getattr(message, 'caption_entities', None) or []
    for entity in entities:
        if entity.type == 'custom_emoji' and entity.custom_emoji_id:
            raw = text.encode('utf-16-le')
            start, end = entity.offset * 2, (entity.offset + entity.length) * 2
            label = (raw[:start] + raw[end:]).decode('utf-16-le').strip()
            return label or fallback_label or text.strip(), validate_icon(entity.custom_emoji_id)
    sticker = getattr(message, 'sticker', None)
    if sticker and getattr(sticker, 'custom_emoji_id', None):
        return fallback_label or 'دسته‌بندی', validate_icon(sticker.custom_emoji_id)
    explicit = re.fullmatch(r'(.+?)\s*\|\s*([0-9]{8,32})', text.strip())
    if explicit:
        return explicit.group(1).strip(), validate_icon(explicit.group(2))
    if fallback_label and re.fullmatch(r'[0-9]{8,32}', text.strip()):
        return fallback_label, validate_icon(text.strip())
    return text.strip(), ''


async def send_icon_preview(message, label, custom_icon):
    """Show whether Telegram accepted the icon instead of silently claiming success."""
    if not custom_icon:
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
    from aiogram.exceptions import TelegramBadRequest
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data='noop', icon_custom_emoji_id=custom_icon)
    ]])
    try:
        sent = await message.answer('پیش‌نمایش آیکون ذخیره‌شده:', reply_markup=markup)
    except TelegramBadRequest:
        await message.answer('شناسه ذخیره شد، ولی تلگرام نمایش این آیکون را نپذیرفت. معتبر بودن شناسه و پرمیوم بودن مالک واقعی ربات در BotFather را بررسی کنید.')
        return
    if isinstance(sent, Message) and (not sent.reply_markup or not any(
        button.icon_custom_emoji_id for row in sent.reply_markup.inline_keyboard for button in row
    )):
        await message.answer('شناسه ذخیره شد، اما تلگرام آیکون را نپذیرفت و پیش‌نمایش بدون آیکون ارسال شد. پرمیوم باید برای مالک واقعی ربات در BotFather فعال باشد؛ ممکن است شناسه نیز نامعتبر باشد.')
