"""Preserve Telegram custom emoji metadata for product names."""
import json
from aiogram.utils.text_decorations import html_decoration


def key(product_id):
    return f'product_name_emoji:{int(product_id)}'


def save(db, product_id, message, name):
    text = message.text or ''
    entities = [e for e in (getattr(message, 'entities', None) or [])
                if e.type == 'custom_emoji' and e.custom_emoji_id]
    if not entities:
        db.set_setting(key(product_id), '')
        return
    first = entities[0]
    raw = text.encode('utf-16-le')
    start, end = first.offset * 2, (first.offset + first.length) * 2
    label = (raw[:start] + raw[end:]).decode('utf-16-le').strip() or name
    value = {'name': name, 'label': label, 'icon': first.custom_emoji_id,
             'html': html_decoration.unparse(text, entities).strip()}
    db.set_setting(key(product_id), json.dumps(value, ensure_ascii=False))


def get(db, product):
    try:
        value = json.loads(db.get_setting(key(product['id']), '') or '{}')
        if value.get('name') == product['name'] and value.get('icon'):
            return value
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    return {}
