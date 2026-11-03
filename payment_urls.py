"""Public payment URLs work with the standalone panel as well as Mini App."""
from urllib.parse import urlsplit, urlencode


def payment_base(api_base='', panel_base='', override=''):
    value = str(override or api_base or panel_base or '').strip().rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('آدرس عمومی پرداخت باید یک آدرس معتبر http/https بدون پارامتر و مشخصات ورود باشد.')
    return value


def return_url(base, gateway, tenant=''):
    return f'{base}/api/pay-return/{gateway}?' + urlencode({'b': tenant or ''})


def start_url(base, authority, tenant=''):
    return f'{base}/api/pay-start/zarinpal/{authority}?' + urlencode({'b': tenant or ''})
