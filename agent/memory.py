"""记忆服务：短期会话历史 + 长期用户画像，统一存 PostgreSQL"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from config import settings
from database import SessionLocal
from models import Conversation, Message, UserProfile


class MemoryService:
    # ------------------------------------------------------------------
    # 短期记忆：会话消息
    # ------------------------------------------------------------------
    @staticmethod
    def create_conversation(user_id: int, topic: str = "") -> dict:
        with SessionLocal() as db:
            conv = Conversation(
                session_id=uuid4().hex,
                user_id=user_id,
                status="ai",
                topic=(topic[:50] or None),
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
            return {
                "id": conv.id,
                "session_id": conv.session_id,
                "status": conv.status,
                "shop_id": conv.shop_id,
            }

    @staticmethod
    def load_history(session_id: str, limit: int | None = None) -> list[dict]:
        """取最近 N 轮消息作为上下文；人工客服消息统一映射为 assistant"""
        limit = limit or settings.short_term_window
        with SessionLocal() as db:
            conv = db.scalar(
                select(Conversation).where(Conversation.session_id == session_id)
            )
            if conv is None:
                return []
            rows = db.scalars(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.id.desc())
                .limit(limit)
            ).all()
        return [
            {
                "role": "user" if m.role == "user" else "assistant",
                "content": m.content,
            }
            for m in reversed(rows)
        ]

    # ------------------------------------------------------------------
    # 长期记忆：用户画像
    # ------------------------------------------------------------------
    @staticmethod
    def get_profile(user_id: int) -> dict:
        with SessionLocal() as db:
            profile = db.scalar(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            if profile is None:
                return {}
            return {
                "nickname": profile.nickname,
                "preferences": profile.preferences or {},
                "facts": profile.facts or {},
            }

    @staticmethod
    def format_profile(profile: dict) -> str:
        if not profile:
            return "（暂无用户画像）"
        parts = []
        if profile.get("nickname"):
            parts.append(f"称呼：{profile['nickname']}")
        for key, value in (profile.get("preferences") or {}).items():
            parts.append(f"{key}：{value}")
        for key, value in (profile.get("facts") or {}).items():
            parts.append(f"{key}：{value}")
        return "\n".join(parts) if parts else "（暂无用户画像）"
