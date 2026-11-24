"""登录鉴权：密码哈希（PBKDF2）、会话登录、角色校验"""
from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from database import get_db
from models import User

_ITERATIONS = 120_000
_ALGO = "pbkdf2_sha256"

# 两端使用不同 cookie，同一浏览器可以同时登录用户端和商户端
SESSION_COOKIES = {
    "customer": "cs_customer",
    "merchant": "cs_merchant",
}


class RoleSessionMiddleware:
    """按路径前缀选择会话 cookie

    /merchant/*  -> cs_merchant
    其它          -> cs_customer

    每个请求只经过一个 SessionMiddleware，因此两端登录态互不覆盖。
    """

    def __init__(self, app, secret_key: str, max_age: int):
        self.app = app
        options = {"secret_key": secret_key, "max_age": max_age, "same_site": "lax"}
        self.customer = SessionMiddleware(
            app, session_cookie=SESSION_COOKIES["customer"], **options
        )
        self.merchant = SessionMiddleware(
            app, session_cookie=SESSION_COOKIES["merchant"], **options
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "/")
        middleware = self.merchant if path.startswith("/merchant") else self.customer
        await middleware(scope, receive, send)


def hash_password(raw: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(raw: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", raw.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ---------------- 会话 ----------------
def login_user(request: Request, user: User) -> None:
    request.session["uid"] = user.id
    request.session["role"] = user.role


def logout_user(request: Request) -> None:
    request.session.clear()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """任何页面/接口都可以用；未登录返回 None"""
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, int(uid))
    if user is None or user.status != "active":
        request.session.clear()
        return None
    return user


def require_api_role(*roles: str):
    """接口鉴权：未登录 401，角色不符 403"""

    def dependency(user: User | None = Depends(get_current_user)) -> User:
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录")
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="无权限访问")
        return user

    return dependency


def login_for(*roles: str) -> str:
    """按目标角色决定登录入口，避免商户被踢到用户登录页"""
    if roles == ("merchant",):
        return "/merchant/login"
    return "/login"


def guard_page(user: User | None, *roles: str) -> RedirectResponse | None:
    """页面鉴权：不满足条件时返回重定向响应"""
    if user is None:
        return RedirectResponse(login_for(*roles), status_code=303)
    if user.role not in roles:
        return RedirectResponse(home_for(user.role), status_code=303)
    return None


def home_for(role: str) -> str:
    return {
        "customer": "/products",
        "merchant": "/merchant/products",
    }.get(role, "/login")
