"""模板渲染统一入口"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from config import BASE_DIR

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

ROLE_LABELS = {"customer": "用户", "merchant": "商户"}
STATUS_LABELS = {
    "pending": "待付款",
    "paid": "待发货",
    "shipped": "已发货",
    "completed": "已完成",
    "cancelled": "已取消",
}
CONV_STATUS_LABELS = {
    "ai": "AI 接待中",
    "waiting": "等待人工",
    "agent": "人工接待中",
    "closed": "已结束",
}

templates.env.globals["ROLE_LABELS"] = ROLE_LABELS
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["CONV_STATUS_LABELS"] = CONV_STATUS_LABELS


def render(request: Request, name: str, user=None, **context: Any):
    return templates.TemplateResponse(request, name, {"user": user, **context})
