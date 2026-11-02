"""Local Happ Crypt4 link generation, compatible with @kastov/cryptohapp 1.1.2.

The Crypt4 public key and wire format below come from the upstream MIT-licensed
package's v1.1.2 source. Encryption is performed locally with the existing
``cryptography`` dependency; subscription URLs are never sent to a crypto API.
"""
import base64
import re
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding


_HAPP_CRYPT4_PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA3UZ0M3L4K+WjM3vkbQnz
ozHg/cRbEXvQ6i4A8RVN4OM3rK9kU01FdjyoIgywve8OEKsFnVwERZAQZ1Trv60B
hmaM76QQEE+EUlIOL9EpwKWGtTL5lYC1sT9XJMNP3/CI0gP5wwQI88cY/xedpOEB
W72EmOOShHUm/b/3m+HPmqwc4ugKj5zWV5SyiT829aFA5DxSjmIIFBAms7DafmSq
LFTYIQL5cShDY2u+/sqyAw9yZIOoqW2TFIgIHhLPWek/ocDU7zyOrlu1E0SmcQQb
LFqHq02fsnH6IcqTv3N5Adb/CkZDDQ6HvQVBmqbKZKf7ZdXkqsc/Zw27xhG7OfXC
tUmWsiL7zA+KoTd3avyOh93Q9ju4UQsHthL3Gs4vECYOCS9dsXXSHEY/1ngU/hjO
WFF8QEE/rYV6nA4PTyUvo5RsctSQL/9DJX7XNh3zngvif8LsCN2MPvx6X+zLouBX
zgBkQ9DFfZAGLWf9TR7KVjZC/3NsuUCDoAOcpmN8pENBbeB0puiKMMWSvll36+2M
YR1Xs0MgT8Y9TwhE2+TnnTJOhzmHi/BxiUlY/w2E0s4ax9GHAmX0wyF4zeV7kDkc
vHuEdc0d7vDmdw0oqCqWj0Xwq86HfORu6tm1A8uRATjb4SzjTKclKuoElVAVa5Jo
oh/uZMozC65SmDw+N5p6Su8CAwEAAQ==
-----END PUBLIC KEY-----"""
_MAX_RSA_PKCS1_PLAINTEXT_BYTES = 501  # 4096-bit RSA: 512 - 11 bytes
_PUBLIC_KEY = serialization.load_pem_public_key(_HAPP_CRYPT4_PUBLIC_KEY)


def create_happ_crypt4_link(subscription_url: str) -> str | None:
    """Return the same ``happ://crypt4/<base64>`` form as the supplied page.

    The page accepts HTTPS only. RSA PKCS#1 v1.5 encryption is delegated to
    the maintained ``cryptography`` library, not implemented here.
    """
    if not isinstance(subscription_url, str):
        return None
    value = subscription_url.strip()
    try:
        if not re.fullmatch(r"https://\S+", value, flags=re.IGNORECASE):
            return None
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return None
        payload = value.encode("utf-8")
        if not payload or len(payload) > _MAX_RSA_PKCS1_PLAINTEXT_BYTES:
            return None
        encrypted = _PUBLIC_KEY.encrypt(payload, padding.PKCS1v15())
    except (UnicodeEncodeError, ValueError, TypeError):
        return None
    return "happ://crypt4/" + base64.b64encode(encrypted).decode("ascii")
