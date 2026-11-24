"""商户端：商品、订单、售后处理、个人中心、Agent 客服配置与调试"""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from config import settings
from database import get_db
from models import Conversation, Message, Order, Product, Review, Shop
from security import get_current_user, guard_page, require_api_role
from web import render

router = APIRouter(prefix="/merchant", tags=["merchant"])

# 售后审核中的订单状态
REFUND_PENDING = ("退款审核中", "退货退款审核中")


def _my_shop(db: Session, user) -> Shop | None:
    return db.scalar(select(Shop).where(Shop.owner_id == user.id))


def _shop_conversation(db: Session, session_id: str, shop: Shop) -> Conversation | None:
    """只能操作自己店铺的会话"""
    conv = db.scalar(select(Conversation).where(Conversation.session_id == session_id))
    if conv is None or conv.shop_id != shop.id:
        return None
    return conv


def _shop_orders(db: Session, shop_id: int) -> list[Order]:
    return list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.shop_id == shop_id)
            .order_by(Order.created_at.desc())
        ).all()
    )


@router.get("")
def merchant_home(user=Depends(get_current_user)):
    if redirect := guard_page(user, "merchant"):
        return redirect
    return RedirectResponse("/merchant/products", status_code=303)


# ===========================================================================
# 商品管理
# ===========================================================================
@router.get("/products")
def product_manage(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/profile?msg=未找到店铺信息", status_code=303)

    products = db.scalars(
        select(Product).where(Product.shop_id == shop.id).order_by(Product.created_at.desc())
    ).all()
    return render(request, "merchant/products.html", user, shop=shop, products=products)


@router.post("/products")
def product_create(
    name: str = Form(...),
    category: str = Form("其他"),
    price: float = Form(...),
    stock: int = Form(0),
    description: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/products?msg=未找到店铺信息", status_code=303)
    if not name.strip() or price <= 0:
        return RedirectResponse("/merchant/products?msg=商品名称和价格不能为空", status_code=303)

    db.add(
        Product(
            shop_id=shop.id,
            name=name.strip(),
            category=(category.strip() or "其他"),
            price=price,
            stock=max(0, stock),
            description=description.strip() or None,
            status="on",
        )
    )
    db.commit()
    return RedirectResponse("/merchant/products?msg=商品已上架", status_code=303)


@router.post("/products/{product_id}/toggle")
def product_toggle(
    product_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    product = db.get(Product, product_id)
    if shop is None or product is None or product.shop_id != shop.id:
        return RedirectResponse("/merchant/products?msg=商品不存在", status_code=303)

    product.status = "off" if product.status == "on" else "on"
    db.commit()
    return RedirectResponse(
        f"/merchant/products?msg=商品已{'下架' if product.status == 'off' else '上架'}",
        status_code=303,
    )


# ===========================================================================
# 订单管理
# ===========================================================================
@router.get("/orders")
def order_manage(
    request: Request,
    tab: str = "all",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/profile?msg=未找到店铺信息", status_code=303)

    all_orders = _shop_orders(db, shop.id)

    def match(order: Order, key: str) -> bool:
        if key == "ship":
            return order.status == "paid"
        if key == "shipped":
            return order.status == "shipped"
        if key == "completed":
            return order.status == "completed"
        if key == "refund":
            return order.refund_status in REFUND_PENDING
        return True

    counts = {
        key: sum(1 for o in all_orders if match(o, key))
        for key in ("all", "ship", "shipped", "completed", "refund")
    }
    orders = [o for o in all_orders if match(o, tab)]

    return render(
        request,
        "merchant/orders.html",
        user,
        shop=shop,
        orders=orders,
        tab=tab,
        counts=counts,
    )


@router.post("/orders/{order_id}/ship")
def order_ship(
    order_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    order = db.get(Order, order_id)
    if shop is None or order is None or order.shop_id != shop.id:
        return RedirectResponse("/merchant/orders?msg=订单不存在", status_code=303)

    if order.status == "paid":
        order.status = "shipped"
        order.shipped_at = datetime.now()
        db.commit()
        return RedirectResponse("/merchant/orders?msg=已发货", status_code=303)
    return RedirectResponse("/merchant/orders?msg=当前状态不能发货", status_code=303)


@router.post("/orders/{order_id}/deliver")
def order_deliver(
    order_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """模拟物流签收：商家确认送达"""
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    order = db.get(Order, order_id)
    if shop is None or order is None or order.shop_id != shop.id:
        return RedirectResponse("/merchant/orders?msg=订单不存在", status_code=303)

    if order.status == "shipped":
        order.status = "completed"
        order.delivered_at = datetime.now()
        db.commit()
        return RedirectResponse("/merchant/orders?msg=已确认送达", status_code=303)
    return RedirectResponse("/merchant/orders?msg=只有已发货订单可以确认送达", status_code=303)


# ===========================================================================
# 售后处理：人工会话 + 退款审核
# ===========================================================================
@router.get("/service")
def service_page(
    request: Request,
    session_id: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/profile?msg=未找到店铺信息", status_code=303)

    waiting = db.scalars(
        select(Conversation)
        .where(Conversation.shop_id == shop.id, Conversation.status == "waiting")
        .order_by(Conversation.updated_at.desc())
    ).all()
    mine = db.scalars(
        select(Conversation)
        .where(
            Conversation.assigned_merchant_id == user.id,
            Conversation.status.in_(("agent", "closed")),
        )
        .order_by(Conversation.updated_at.desc())
    ).all()
    refund_count = (
        db.scalar(
            select(func.count(Order.id)).where(
                Order.shop_id == shop.id, Order.refund_status.in_(REFUND_PENDING)
            )
        )
        or 0
    )

    current = _shop_conversation(db, session_id, shop) if session_id else None
    messages = []
    if current is not None:
        messages = list(
            db.scalars(
                select(Message)
                .where(Message.conversation_id == current.id)
                .order_by(Message.id)
            ).all()
        )
    return render(
        request,
        "merchant/service.html",
        user,
        shop=shop,
        waiting=waiting,
        mine=mine,
        current=current,
        messages=messages,
        refund_count=refund_count,
    )


@router.post("/service/{session_id}/accept")
def service_accept(
    session_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    conv = _shop_conversation(db, session_id, shop) if shop else None
    if conv is not None and conv.status == "waiting":
        conv.status = "agent"
        conv.assigned_merchant_id = user.id
        db.commit()
    return RedirectResponse(f"/merchant/service?session_id={session_id}", status_code=303)


@router.post("/service/{session_id}/reply")
def service_reply(
    session_id: str,
    content: str = Form(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    conv = _shop_conversation(db, session_id, shop) if shop else None
    if conv is None:
        return RedirectResponse("/merchant/service?msg=会话不存在", status_code=303)
    if conv.status == "closed":
        return RedirectResponse(
            f"/merchant/service?session_id={session_id}&msg=会话已结束", status_code=303
        )

    # 商户第一次回复即视为接入
    if conv.status == "waiting":
        conv.status = "agent"
        conv.assigned_merchant_id = user.id
    if content.strip():
        db.add(Message(conversation_id=conv.id, role="merchant", content=content.strip()))
    db.commit()
    return RedirectResponse(f"/merchant/service?session_id={session_id}", status_code=303)


@router.post("/service/{session_id}/close")
def service_close(
    session_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    conv = _shop_conversation(db, session_id, shop) if shop else None
    if conv is not None and conv.status != "closed":
        conv.status = "closed"
        db.add(
            Message(
                conversation_id=conv.id,
                role="merchant",
                content="本次人工服务已结束。如还有问题，可以直接继续留言，系统会重新为你接待。",
            )
        )
        db.commit()
    return RedirectResponse(f"/merchant/service?session_id={session_id}", status_code=303)


@router.get("/refunds")
def refund_page(
    request: Request,
    tab: str = "pending",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/profile?msg=未找到店铺信息", status_code=303)

    all_orders = [o for o in _shop_orders(db, shop.id) if o.refund_status]
    pending = [o for o in all_orders if o.refund_status in REFUND_PENDING]
    orders = pending if tab == "pending" else all_orders

    return render(
        request,
        "merchant/refunds.html",
        user,
        shop=shop,
        orders=orders,
        tab=tab,
        pending_count=len(pending),
        total_count=len(all_orders),
    )


@router.post("/refunds/{order_id}/approve")
def refund_approve(
    order_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    order = db.get(Order, order_id)
    if shop is None or order is None or order.shop_id != shop.id:
        return RedirectResponse("/merchant/refunds?msg=订单不存在", status_code=303)
    if order.refund_status not in REFUND_PENDING:
        return RedirectResponse("/merchant/refunds?msg=该订单没有待处理的退款申请", status_code=303)

    # 同意退款：订单关闭并回滚库存
    if order.status != "cancelled":
        for item in order.items:
            product = db.get(Product, item.product_id)
            if product is not None:
                product.stock += item.quantity
    order.status = "cancelled"
    order.refund_status = "已退款"
    db.commit()
    return RedirectResponse("/merchant/refunds?msg=已同意退款，款项原路退回", status_code=303)


@router.post("/refunds/{order_id}/reject")
def refund_reject(
    order_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    order = db.get(Order, order_id)
    if shop is None or order is None or order.shop_id != shop.id:
        return RedirectResponse("/merchant/refunds?msg=订单不存在", status_code=303)
    if order.refund_status not in REFUND_PENDING:
        return RedirectResponse("/merchant/refunds?msg=该订单没有待处理的退款申请", status_code=303)

    order.refund_status = "已拒绝"
    db.commit()
    return RedirectResponse("/merchant/refunds?msg=已拒绝退款，建议在会话中联系客户", status_code=303)


# ===========================================================================
# 个人中心
# ===========================================================================
@router.get("/profile")
def profile_page(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/logout?msg=未找到店铺信息", status_code=303)

    product_count = (
        db.scalar(select(func.count(Product.id)).where(Product.shop_id == shop.id)) or 0
    )
    order_count = (
        db.scalar(select(func.count(Order.id)).where(Order.shop_id == shop.id)) or 0
    )
    sales = (
        db.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.shop_id == shop.id,
                Order.status.in_(("paid", "shipped", "completed")),
            )
        )
        or 0
    )
    avg_rating = (
        db.scalar(
            select(func.avg(Review.service_rating)).join(
                Order, Review.order_id == Order.id
            ).where(Order.shop_id == shop.id)
        )
        or 0
    )

    return render(
        request,
        "merchant/profile.html",
        user,
        shop=shop,
        product_count=product_count,
        order_count=order_count,
        sales=float(sales),
        avg_rating=float(avg_rating),
    )


@router.post("/profile")
def profile_update(
    shop_name: str = Form(...),
    description: str = Form(""),
    phone: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/profile?msg=未找到店铺信息", status_code=303)
    if not shop_name.strip():
        return RedirectResponse("/merchant/profile?msg=店铺名称不能为空", status_code=303)

    shop.name = shop_name.strip()[:60]
    shop.description = description.strip() or None
    user.phone = phone.strip() or None
    db.commit()
    return RedirectResponse("/merchant/profile?msg=资料已保存", status_code=303)


# ===========================================================================
# Agent 客服配置与调试
# ===========================================================================
@router.get("/agent")
def agent_page(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/profile?msg=未找到店铺信息", status_code=303)

    runtime = {
        "llm_model": settings.llm_model,
        "llm_temperature": settings.llm_temperature,
        "embedding_model": settings.embedding_model,
        "collection": settings.collection_name,
        "top_k": settings.top_k,
        "threshold": settings.rag_score_threshold,
        "memory_window": settings.short_term_window,
    }
    return render(
        request,
        "merchant/agent.html",
        user,
        shop=shop,
        runtime=runtime,
        graph_ready=getattr(request.app.state, "graph", None) is not None,
    )


@router.post("/agent")
def agent_save(
    agent_name: str = Form(""),
    agent_welcome: str = Form(""),
    agent_prompt: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "merchant"):
        return redirect
    shop = _my_shop(db, user)
    if shop is None:
        return RedirectResponse("/merchant/profile?msg=未找到店铺信息", status_code=303)

    shop.agent_name = agent_name.strip()[:40] or "客服小星"
    shop.agent_welcome = agent_welcome.strip()[:200] or None
    shop.agent_prompt = agent_prompt.strip() or None
    db.commit()
    return RedirectResponse("/merchant/agent?msg=客服配置已保存，新会话立即生效", status_code=303)


class AgentTestIn(BaseModel):
    question: str


@router.post("/api/agent/test")
def agent_test(
    payload: AgentTestIn,
    request: Request,
    user=Depends(require_api_role("merchant")),
    db: Session = Depends(get_db),
):
    """在线调试：用真实 Agent 跑一轮问答（不写入用户会话）"""
    shop = _my_shop(db, user)
    if shop is None:
        raise HTTPException(status_code=404, detail="未找到店铺")

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")

    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        return {"ok": False, "error": "Agent 未就绪，请检查 Milvus 与模型配置"}

    started = time.perf_counter()
    try:
        result = graph.invoke(
            {
                "user_id": user.id,
                "session_id": f"debug-{uuid.uuid4().hex[:12]}",
                "question": question,
                "history": [],
                "profile": {},
                "agent_prompt": shop.agent_prompt or "",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"调试失败：{exc}"}

    return {
        "ok": True,
        "intent": result.get("intent", ""),
        "answer": result.get("answer", ""),
        "sources": result.get("sources") or [],
        "tool_result": result.get("tool_result", ""),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


@router.post("/api/agent/retrieve")
def agent_retrieve(
    payload: AgentTestIn,
    request: Request,
    user=Depends(require_api_role("merchant")),
):
    """知识库检索测试：看命中的片段与相似度"""
    retriever = getattr(request.app.state, "retriever", None)
    if retriever is None:
        return {"ok": False, "error": "知识库未就绪，请检查 Milvus 连接"}

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")

    started = time.perf_counter()
    try:
        docs = retriever.retrieve(question)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"检索失败：{exc}"}

    return {
        "ok": True,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "docs": [
            {
                "source": doc.get("source", ""),
                "score": round(float(doc.get("score", 0)), 4),
                "text": doc.get("text", "")[:240],
                "hit": float(doc.get("score", 0)) >= settings.rag_score_threshold,
            }
            for doc in docs
        ],
    }


@router.get("/api/messages")
def fetch_messages(
    session_id: str,
    after_id: int = 0,
    user=Depends(require_api_role("merchant")),
    db: Session = Depends(get_db),
):
    """商户轮询：获取用户新消息"""
    shop = _my_shop(db, user)
    conv = _shop_conversation(db, session_id, shop) if shop else None
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.id > after_id)
        .order_by(Message.id)
    ).all()
    return {
        "status": conv.status,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.strftime("%H:%M") if m.created_at else "",
            }
            for m in rows
        ],
    }
