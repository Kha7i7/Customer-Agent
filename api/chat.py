"""客服会话：聊天页面、发消息、转人工、增量拉取消息

状态机：ai（AI 接待）→ waiting（等待人工）→ agent（人工接待）→ closed（已结束）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.memory import MemoryService
from agent.tools import create_ticket
from database import get_db
from models import Conversation, Message, Shop
from schemas import ChatRequest, ChatResponse
from security import get_current_user, guard_page, require_api_role
from web import render

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

# 需要人工介入的状态：此时不再调用 AI，新消息只落库
HUMAN_STATUS = ("waiting", "agent")


def _own_conversation(db: Session, session_id: str, user_id: int) -> Conversation | None:
    """按 session_id + user_id 查询，防止通过 URL 偷看别人的会话"""
    conv = db.scalar(select(Conversation).where(Conversation.session_id == session_id))
    if conv is None or conv.user_id != user_id:
        return None
    return conv


def _conversation_list(db: Session, user_id: int) -> list[Conversation]:
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        ).all()
    )


# ===========================================================================
# 页面
# ===========================================================================
@router.get("/chat")
def chat_page(
    request: Request,
    session_id: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    conversations = _conversation_list(db, user.id)
    current = _own_conversation(db, session_id, user.id) if session_id else None
    if current is None and conversations:
        current = conversations[0]

    messages = []
    if current is not None:
        messages = list(
            db.scalars(
                select(Message)
                .where(Message.conversation_id == current.id)
                .order_by(Message.id)
            ).all()
        )

    # 会话已绑定店铺时，展示该店铺配置的客服名称与欢迎语
    agent_name = "客服小星"
    agent_welcome = "你好，我是客服小星。可以帮你查订单、看物流、办理退款，也可以咨询退换货政策～"
    if current is not None and current.shop_id:
        shop = db.get(Shop, current.shop_id)
        if shop is not None:
            agent_name = shop.agent_name or agent_name
            agent_welcome = shop.agent_welcome or agent_welcome

    return render(
        request,
        "user/chat.html",
        user,
        conversations=conversations,
        current=current,
        messages=messages,
        agent_name=agent_name,
        agent_welcome=agent_welcome,
    )


@router.post("/chat/new")
def new_conversation(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect
    created = MemoryService.create_conversation(user.id, "在线客服")
    return RedirectResponse(f"/chat?session_id={created['session_id']}", status_code=303)


@router.post("/chat/{session_id}/delete")
def delete_conversation(
    session_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除自己的会话及其历史消息"""
    if redirect := guard_page(user, "customer"):
        return redirect

    conv = _own_conversation(db, session_id, user.id)
    if conv is None:
        return RedirectResponse("/chat?msg=会话不存在", status_code=303)

    db.delete(conv)  # messages 关系带 delete-orphan，会一并删除
    db.commit()
    return RedirectResponse("/chat?msg=会话记录已删除", status_code=303)


@router.post("/chat/handoff")
def handoff(
    session_id: str = Form(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户点击「转人工」：创建工单并把会话挂到商户待接入队列"""
    if redirect := guard_page(user, "customer"):
        return redirect

    conv = _own_conversation(db, session_id, user.id)
    if conv is None:
        return RedirectResponse("/chat?msg=会话不存在", status_code=303)

    if conv.status in HUMAN_STATUS:
        return RedirectResponse(f"/chat?session_id={session_id}", status_code=303)

    result = create_ticket.invoke(
        {
            "user_id": user.id,
            "session_id": conv.session_id,
            "question": "用户主动请求转接人工客服",
            "reason": "用户主动请求转人工",
            "route_to_human": True,
        }
    )
    db.add(Message(conversation_id=conv.id, role="assistant", content=result))
    db.commit()
    return RedirectResponse(f"/chat?session_id={session_id}", status_code=303)


# ===========================================================================
# 接口
# ===========================================================================
@router.post("/api/chat", response_model=ChatResponse)
def send_message(
    payload: ChatRequest,
    request: Request,
    user=Depends(require_api_role("customer")),
    db: Session = Depends(get_db),
) -> ChatResponse:
    question = payload.message.strip()
    if not question:
        raise HTTPException(status_code=422, detail="消息不能为空")

    if payload.session_id:
        conv = _own_conversation(db, payload.session_id, user.id)
        if conv is None:
            raise HTTPException(status_code=404, detail="会话不存在")
    else:
        created = MemoryService.create_conversation(user.id, question[:50])
        conv = _own_conversation(db, created["session_id"], user.id)
        if conv is None:
            raise HTTPException(status_code=500, detail="会话创建失败")

    # 已结束的会话再次发言，视为重新开启，交回 AI 接待
    if conv.status == "closed":
        conv.status = "ai"
        conv.assigned_merchant_id = None
        db.commit()

    # 人工接待中：AI 不再插话，只把用户消息落库，等商户回复
    if conv.status in HUMAN_STATUS:
        msg = Message(conversation_id=conv.id, role="user", content=question)
        db.add(msg)
        if not conv.topic:
            conv.topic = question[:50]
        db.commit()
        db.refresh(msg)
        return ChatResponse(
            session_id=conv.session_id,
            intent="human",
            status=conv.status,
            human=True,
            last_id=msg.id,
        )

    # 先把本轮问题落库，保证即使 Agent 异常也不丢消息
    db.add(Message(conversation_id=conv.id, role="user", content=question))
    if not conv.topic:
        conv.topic = question[:50]
    db.commit()

    history = MemoryService.load_history(conv.session_id)
    profile = MemoryService.get_profile(user.id)

    # 会话绑定了店铺时，带上该店铺配置的客服人设
    agent_prompt = ""
    if conv.shop_id:
        shop = db.get(Shop, conv.shop_id)
        if shop is not None:
            agent_prompt = shop.agent_prompt or ""

    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        answer, intent, sources = (
            "智能客服正在维护中，暂时无法回答知识类问题。你可以直接查询订单，或点击「转人工客服」。",
            "error",
            [],
        )
    else:
        try:
            result = graph.invoke(
                {
                    "user_id": user.id,
                    "session_id": conv.session_id,
                    "question": question,
                    "history": history,
                    "profile": profile,
                    "agent_prompt": agent_prompt,
                }
            )
            answer = result.get("answer") or "抱歉，我没能理解你的问题，可以换个说法吗？"
            intent = result.get("intent", "")
            sources = result.get("sources") or []
        except Exception:  # noqa: BLE001
            logger.exception("Agent 执行失败")
            answer, intent, sources = "客服系统开小差了，请稍后重试，或点击「转人工客服」。", "error", []

    # 工具/工单可能已经改过会话状态（例如转 waiting），这里刷新后再写入回复
    db.refresh(conv)
    assistant = Message(
        conversation_id=conv.id,
        role="assistant",
        content=answer,
        sources=sources or None,
    )
    db.add(assistant)
    db.commit()
    db.refresh(assistant)

    return ChatResponse(
        session_id=conv.session_id,
        answer=answer,
        intent=intent,
        sources=sources,
        status=conv.status,
        human=conv.status in HUMAN_STATUS,
        last_id=assistant.id,
    )


@router.get("/api/chat/messages")
def fetch_messages(
    session_id: str,
    after_id: int = 0,
    user=Depends(require_api_role("customer")),
    db: Session = Depends(get_db),
):
    """轮询增量消息：人工接待时用来接收商户回复"""
    conv = _own_conversation(db, session_id, user.id)
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
                "sources": m.sources or [],
                "created_at": m.created_at.strftime("%H:%M") if m.created_at else "",
            }
            for m in rows
        ],
    }
