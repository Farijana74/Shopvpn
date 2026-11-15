"""Shared delivery formatting and clipboard controls for bot and web approvals."""
import base64
import html
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from config import MINIAPP_URL
from i18n import tr


def text_length(text):
    return len(text.encode('utf-16-le')) // 2


def link_sections(configs=(), subscriptions=(), happs=(), L=None, code_links=False):
    L = L or tr
    sections = []
    if configs:
        content = html.escape('\n\n'.join(configs), quote=False)
        sections.append(f"<b>📋 {L('کانفیگ‌های تکی')}</b>\n<pre><code>{content}</code></pre>")
    if subscriptions:
        content = '\n\n'.join(f'<code>{html.escape(url, quote=False)}</code>' for url in subscriptions)
        if code_links:
            content = '<pre><code>' + html.escape('\n\n'.join(subscriptions), quote=False) + '</code></pre>'
        sections.append(f"<b>🔗 {L('لینک ساب')}</b>\n{content}")
    if happs:
        content = '\n\n'.join(f'<code>{html.escape(url, quote=False)}</code>' for url in happs)
        if code_links:
            content = '<pre><code>' + html.escape('\n\n'.join(happs), quote=False) + '</code></pre>'
        sections.append(f'<b>📲 Happ</b>\n{content}')
    return '\n\n'.join(sections)


class _CopyTargets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pre = False
        self.code = False
        self.parts = []
        self.configs = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'pre':
            self.pre = True
        if tag == 'code':
            self.code = True
            self.parts = []

    def handle_data(self, data):
        if self.code:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'code' and self.code:
            value = ''.join(self.parts)
            if value:
                if self.pre:
                    self.configs.append(value)
                elif value.startswith(('https://', 'http://', 'happ://')):
                    self.links.append(value)
            self.code = False
        if tag == 'pre':
            self.pre = False


def copy_targets(markup):
    parser = _CopyTargets()
    parser.feed(markup)
    return '\n\n'.join(parser.configs), parser.links


def _copy_button(label, value, kind):
    if text_length(value) <= 256:
        return InlineKeyboardButton(text=label, copy_text=CopyTextButton(text=value))
    base = urlsplit(MINIAPP_URL)
    if base.scheme == 'https' and base.netloc and len(value.encode('utf-8')) <= 24000:
        path = base.path.rstrip('/')
        if path.endswith('/index.html'):
            path = path[:-len('/index.html')]
        payload = base64.urlsafe_b64encode(value.encode('utf-8')).decode().rstrip('=')
        url = urlunsplit((base.scheme, base.netloc, path + '/copy-links.html', '', payload))
        return InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))
    # Never pass an over-limit/truncated value as a successful CopyTextButton.
    return InlineKeyboardButton(text=label, callback_data=f'delivery_copy_help:{kind}')


def delivery_copy_markup(markup, tail=None):
    configs, links = copy_targets(markup)
    rows = []
    if links:
        rows.append([_copy_button(tr('📋 کپی لینک'), links[0], 'link')])
    if configs:
        rows.append([_copy_button(tr('📋 کپی کانفیگ‌ها'), configs, 'configs')])
    if tail:
        rows.extend(tail.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
