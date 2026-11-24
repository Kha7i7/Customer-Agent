"""Agent 工具：订单查询、物流查询、退款申请、人工工单

全部基于 PostgreSQL 真实业务数据，且严格按 user_id 做归属过滤，
避免用户通过对话查询到他人订单。
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import SessionLocal
from models import Conversation, Order, Shop, Ticket

STATUS_LABELS = {
    "pending": "待付款",
    "paid": "待发货",
    "shipped": "已发货",
    "completed": "已完成",
    "cancelled": "已取消",
}

_NOT_FOUND_TIP = (
    "未查询到订单号 {no}。请确认订单号是否正确（形如 CS20251101103012345），"
    "或提供下单手机号后四位，我可以帮您进一步核实。"
)


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "无"


def _load_order(order_no: str, user_id: int | str) -> dict[str, Any] | None:
    """按订单号 + 用户 ID 查询，确保只能看到自己的订单"""
    with SessionLocal() as db:
        order = db.scalar(
            select(Order)
            .options(selectinload(Order.items))
            .where(
                Order.order_no == order_no.strip().upper(),
                Order.user_id == int(user_id),
            )
        )
        if order is None:
            return None
        return {
            "order_no": order.order_no,
            "status": order.status,
            "status_label": STATUS_LABELS.get(order.status, order.status),
            "total": float(order.total_amount),
            "receiver": order.receiver_name,
            "phone": order.receiver_phone,
            "address": order.address,
            "created_at": _fmt(order.created_at),
            "shipped_at": _fmt(order.shipped_at),
            "delivered_at": _fmt(order.delivered_at),
            "refund_status": order.refund_status,
            "refund_id": order.refund_id,
            "items": [
                {"name": i.product_name, "price": float(i.price), "quantity": i.quantity}
                for i in order.items
            ],
        }


@tool
def query_order(order_no: str, user_id: int) -> str:
    """根据订单号查询当前用户的订单状态、商品、金额、收货地址。用户询问订单状态、是否发货时使用。"""
    order = _load_order(order_no, user_id)
    if order is None:
        return _NOT_FOUND_TIP.format(no=order_no.strip())

    items = "；".join(
        f"{i['name']} × {i['quantity']}（¥{i['price']:.2f}）" for i in order["items"]
    )
    lines = [
        f"订单号：{order['order_no']}",
        f"订单状态：{order['status_label']}",
        f"商品：{items}",
        f"实付金额：¥{order['total']:.2f}",
        f"下单时间：{order['created_at']}",
        f"发货时间：{order['shipped_at']}",
        f"签收时间：{order['delivered_at']}",
        f"收货信息：{order['receiver']} {order['phone']}",
        f"收货地址：{order['address']}",
    ]
    if order["refund_status"]:
        lines.append(f"售后状态：{order['refund_status']}（退款单号 {order['refund_id']}）")
    return "查询结果：\n" + "\n".join(lines)


@tool
def query_logistics(order_no: str, user_id: int) -> str:
    """根据订单号查询物流进度。用户询问快递到哪了、什么时候到、物流异常时使用。"""
    order = _load_order(order_no, user_id)
    if order is None:
        return _NOT_FOUND_TIP.format(no=order_no.strip())

    if order["status"] == "pending":
        return f"订单 {order['order_no']} 尚未支付，未产生物流信息。请先完成支付。"
    if order["status"] == "paid":
        return (
            f"订单 {order['order_no']} 当前状态为「待发货」，商家尚未发货。"
            "一般情况下 48 小时内发出，发货后可再次查询。"
        )
    if order["status"] == "cancelled":
        return f"订单 {order['order_no']} 已取消，未产生物流信息。"

    lines = [
        f"订单号：{order['order_no']}",
        f"当前状态：{order['status_label']}",
        f"发货时间：{order['shipped_at']}",
    ]
    if order["status"] == "shipped":
        lines.append("物流轨迹：商家已发货，包裹运输中，请保持电话畅通。")
    else:
        lines.append(f"物流轨迹：包裹已于 {order['delivered_at']} 签收。")
    return "\n".join(lines)


@tool
def apply_refund(order_no: str, user_id: int, reason: str = "用户未说明具体原因") -> str:
    """为当前用户的指定订单提交退款或退货申请。需要订单号和原因，用户明确要退款、退货时使用。"""
    no = order_no.strip().upper()
    with SessionLocal() as db:
        order = db.scalar(
            select(Order).where(Order.order_no == no, Order.user_id == int(user_id))
        )
        if order is None:
            return _NOT_FOUND_TIP.format(no=no)

        if order.refund_status and order.refund_status != "已退款":
            return (
                f"订单 {no} 已于此前提交售后申请，退款单号 {order.refund_id}，"
                f"当前状态：{order.refund_status}。请勿重复提交，审核结果会通过站内消息通知您。"
            )
        if order.status == "cancelled":
            return f"订单 {no} 已取消，无需重复申请退款。若款项未到账，我可以为您转人工核实。"

        refund_id = f"R{datetime.now():%Y%m%d}{random.randint(1000, 9999)}"

        if order.status == "pending":
            return f"订单 {no} 尚未支付，无需退款。"

        if order.status == "paid":
            order.refund_status = "退款审核中"
            order.refund_id = refund_id
            order.refund_reason = (reason or "用户未说明具体原因")[:200]
            db.commit()
            return (
                f"已为订单 {no} 提交「仅退款」申请，退款单号 {refund_id}。\n"
                "商家需在 1-3 个工作日内审核，通过后按原支付渠道退回："
                "微信/支付宝 1-3 个工作日，储蓄卡 1-7 个工作日，信用卡 3-15 个工作日。"
            )

        if order.status == "shipped":
            order.refund_status = "退货退款审核中"
            order.refund_id = refund_id
            order.refund_reason = (reason or "用户未说明具体原因")[:200]
            db.commit()
            return (
                f"已为订单 {no} 提交「退货退款」申请，退款单号 {refund_id}。\n"
                "包裹运输中，仓库会尝试拦截；若已派送请拒收，或签收后 7 个自然日内寄回。"
            )

        # 已完成：校验 7 天无理由时限
        if order.delivered_at:
            days = (datetime.now(order.delivered_at.tzinfo) - order.delivered_at).days
            if days > 7:
                return (
                    f"订单 {no} 签收于 {_fmt(order.delivered_at)}，距今已 {days} 天，"
                    "超出 7 天无理由退货期限，无法直接提交无理由退货。\n"
                    "如果商品存在质量问题，可按三包政策申请退货退款，建议转人工客服核实处理。"
                )

        order.refund_status = "退货退款审核中"
        order.refund_id = refund_id
        db.commit()
        return (
            f"已为订单 {no} 提交「退货退款」申请，退款单号 {refund_id}。\n"
            "请保持商品完好、不影响二次销售，并在 7 个自然日内寄回。"
            "七天无理由退货运费由买家承担，审核通过后款项原路退回。"
        )


@tool
def create_ticket(
    user_id: int,
    session_id: str,
    question: str,
    reason: str = "",
    route_to_human: bool = False,
) -> str:
    """创建人工客服工单。用户投诉、要求转人工、或问题超出知识库范围时使用。"""
    reason = (reason or "用户要求转人工")[:60]
    try:
        with SessionLocal() as db:
            ticket = Ticket(
                user_id=int(user_id),
                session_id=session_id,
                question=question,
                reason=reason,
                status="pending",
            )
            db.add(ticket)

            # 转人工：把会话挂到商户待接入队列
            if route_to_human:
                conv = db.scalar(
                    select(Conversation).where(Conversation.session_id == session_id)
                )
                if conv is not None:
                    conv.status = "waiting"
                    if conv.shop_id is None:
                        # 优先派给用户最近下单的店铺，其次兜底第一个正常营业的店铺
                        conv.shop_id = db.scalar(
                            select(Order.shop_id)
                            .where(Order.user_id == int(user_id))
                            .order_by(Order.created_at.desc())
                            .limit(1)
                        ) or db.scalar(
                            select(Shop.id)
                            .where(Shop.status == "active")
                            .order_by(Shop.id)
                            .limit(1)
                        )

            db.commit()
            db.refresh(ticket)
            created = ticket.created_at.strftime("%Y%m%d") if ticket.created_at else datetime.now().strftime("%Y%m%d")
            ticket_no = f"T{created}{ticket.id:04d}"

        if route_to_human:
            return (
                f"已为您转接人工客服，工单号：{ticket_no}。\n"
                "人工客服已收到您的会话，接入后可直接对话，请稍候。"
            )
        return (
            f"已创建人工工单。\n工单号：{ticket_no}\n"
            f"问题摘要：{question[:80]}\n"
            "人工客服工作时间为每日 9:00-22:00，我们会尽快跟进处理。"
        )
    except Exception:
        return "人工工单系统暂时不可用，未能创建工单，请稍后重试。"


TOOLS = [query_order, query_logistics, apply_refund, create_ticket]
