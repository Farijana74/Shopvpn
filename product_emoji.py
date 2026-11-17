"""Preserve Telegram custom emoji metadata for product names."""
import json
from aiogram.utils.text_decorations import html_decoration


def key(product_id):
    return f'product_name_emoji:{int(product_id)}'


def extract(message, name):
    text = message.text or ''
    entities = [e for e in (getattr(message, 'entities', None) or [])
                if e.type == 'custom_emoji' and e.custom_emoji_id]
    if not entities:
        return {}
    first = entities[0]
    raw = text.encode('utf-16-le')
    start, end = first.offset * 2, (first.offset + first.length) * 2
    label = (raw[:start] + raw[end:]).decode('utf-16-le').strip() or name
    return {'name': name, 'label': label, 'icon': first.custom_emoji_id,
            'html': html_decoration.unparse(text, entities).strip()}


def save_value(db, product_id, value):
    db.set_setting(key(product_id), json.dumps(value, ensure_ascii=False) if value else '')


def save(db, product_id, message, name):
    save_value(db, product_id, extract(message, name))


def get(db, product):
    try:
        value = json.loads(db.get_setting(key(product['id']), '') or '{}')
        if value.get('name') == product['name'] and value.get('icon'):
            return value
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    return {}
