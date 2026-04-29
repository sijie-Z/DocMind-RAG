import base64
import json
import hmac
import hashlib
import time
from typing import Dict, Any, Optional


SECRET = b"dev_secret_change_me"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def create_jwt(payload: Dict[str, Any], ttl_seconds: int = 3600) -> str:
    """
    功能：生成一个简单的 JWT（HS256），包含过期时间。
    小白解释：把用户信息做成三段字符串，并加上签名，别人不能伪造。
    """
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = dict(payload)
    payload.setdefault("iat", now)
    payload.setdefault("exp", now + ttl_seconds)

    head = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{head}.{body}".encode("utf-8")
    sig = hmac.new(SECRET, signing_input, hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(sig)}"


def verify_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    功能：验证签名与过期时间，返回用户载荷；失败返回 None。
    小白解释：检查这张“通行证”是否真的由服务器签发且没有过期。
    """
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        signing_input = f"{head_b64}.{body_b64}".encode("utf-8")
        expected = hmac.new(SECRET, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        payload = json.loads(_b64url_decode(body_b64))
        if int(time.time()) >= int(payload.get("exp", 0)):
            return None
        return payload
    except Exception:
        return None