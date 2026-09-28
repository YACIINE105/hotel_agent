"""Guest sessions are HMAC-signed tokens bound to one property and one conversation."""

import base64
import hashlib
import hmac
import json
import time


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_guest_token(secret: str, property_id: int, conversation_id: str, minutes: int) -> str:
    body = _b64(json.dumps({"p": property_id, "c": conversation_id, "e": int(time.time()) + minutes * 60}).encode())
    mac = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify_guest_token(secret: str, token: str) -> dict | None:
    try:
        body, mac = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, expected):
            return None
        data = json.loads(_unb64(body))
        return data if data["e"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()
