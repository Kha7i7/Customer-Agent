"""注册 / 登录 / 退出：用户端、商户端各自独立登录

两端走不同路径、不同 cookie，同一浏览器可以同时在线：
    /login            -> cs_customer
    /merchant/login   -> cs_merchant
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import Shop, User
from security import (
    get_current_user,
    hash_password,
    home_for,
    login_user,
    logout_user,
    verify_password,
)
from web import render

router = APIRouter(tags=["auth"])

ENTRANCES = {
    "customer": {
        "login": "/login",
        "register": "/register",
        "logout": "/logout",
        "login_title": "用户登录",
        "login_subtitle": "登录后可以购物、查订单、咨询在线客服",
        "register_title": "注册用户账号",
        "register_subtitle": "注册后可以浏览商品、下单并联系客服",
        "label": "用户",
    },
    "merchant": {
        "login": "/merchant/login",
        "register": "/merchant/register",
        "logout": "/merchant/logout",
        "login_title": "商户登录",
        "login_subtitle": "登录后管理商品、订单与人工客服",
        "register_title": "商户入驻",
        "register_subtitle": "入驻后可以上架商品、处理订单、接待客户",
        "label": "商户",
    },
}


def _render_login(request: Request, role: str, error: str | None = None):
    cfg = ENTRANCES[role]
    return render(
        request,
        "auth/login.html",
        None,
        role=role,
        action=cfg["login"],
        title=cfg["login_title"],
        subtitle=cfg["login_subtitle"],
        error=error,
    )


def _render_register(request: Request, role: str, error: str | None = None, form=None):
    cfg = ENTRANCES[role]
    return render(
        request,
        "auth/register.html",
        None,
        role=role,
        action=cfg["register"],
        title=cfg["register_title"],
        subtitle=cfg["register_subtitle"],
        error=error,
        form=form or {},
    )


def _login(request: Request, db: Session, username: str, password: str, role: str):
    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or not verify_password(password, user.password_hash):
        return _render_login(request, role, "用户名或密码错误")
    if user.role != role:
        return _render_login(
            request, role, f"该账号不是{ENTRANCES[role]['label']}账号，请从对应入口登录"
        )
    if user.status != "active":
        return _render_login(request, role, "账号已被禁用，请联系平台")

    login_user(request, user)
    return RedirectResponse(home_for(user.role), status_code=303)


def _register(
    request: Request,
    db: Session,
    username: str,
    password: str,
    confirm: str,
    phone: str,
    shop_name: str,
    role: str,
):
    username = username.strip()
    shop_name = shop_name.strip()
    form = {"username": username, "phone": phone, "shop_name": shop_name}

    def fail(message: str):
        return _render_register(request, role, message, form)

    if len(username) < 3:
        return fail("用户名至少 3 个字符")
    if len(password) < 6:
        return fail("密码至少 6 位")
    if password != confirm:
        return fail("两次输入的密码不一致")
    if db.scalar(select(User).where(User.username == username)) is not None:
        return fail("用户名已存在，请换一个")
    if role == "merchant" and not shop_name:
        return fail("商户注册需要填写店铺名称")

    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        phone=phone.strip() or None,
        status="active",
    )
    db.add(user)
    try:
        db.flush()
        if role == "merchant":
            db.add(
                Shop(
                    owner_id=user.id,
                    name=shop_name,
                    description="新入驻商户",
                    status="active",
                )
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        return fail("用户名已存在，请换一个")

    login_user(request, user)
    return RedirectResponse(home_for(user.role), status_code=303)


# ===========================================================================
# 用户端
# ===========================================================================
@router.get("/login")
def login_page(request: Request, user=Depends(get_current_user)):
    if user is not None:
        return RedirectResponse(home_for(user.role), status_code=303)
    return _render_login(request, "customer")


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    return _login(request, db, username, password, "customer")


@router.get("/register")
def register_page(request: Request, user=Depends(get_current_user)):
    if user is not None:
        return RedirectResponse(home_for(user.role), status_code=303)
    return _render_register(request, "customer")


@router.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    return _register(request, db, username, password, confirm, phone, "", "customer")


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


# ===========================================================================
# 商户端
# ===========================================================================
@router.get("/merchant/login")
def merchant_login_page(request: Request, user=Depends(get_current_user)):
    if user is not None:
        return RedirectResponse(home_for(user.role), status_code=303)
    return _render_login(request, "merchant")


@router.post("/merchant/login")
def merchant_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    return _login(request, db, username, password, "merchant")


@router.get("/merchant/register")
def merchant_register_page(request: Request, user=Depends(get_current_user)):
    if user is not None:
        return RedirectResponse(home_for(user.role), status_code=303)
    return _render_register(request, "merchant")


@router.post("/merchant/register")
def merchant_register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    shop_name: str = Form(""),
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    return _register(request, db, username, password, confirm, phone, shop_name, "merchant")


@router.get("/merchant/logout")
def merchant_logout(request: Request):
    logout_user(request)
    return RedirectResponse("/merchant/login", status_code=303)
