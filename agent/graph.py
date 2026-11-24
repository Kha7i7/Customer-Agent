"""LangGraph 编排：意图识别 → 路由 → 检索 / 工具 / 转人工 → 生成回答

图结构：
    START → classify ─┬→ retrieve ─┐
                      ├→ tool ─────┤
                      ├→ handoff ──┼→ generate → END
                      └→ chitchat ─┘
"""
from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from agent.memory import MemoryService
from agent.prompts import (
    ANSWER_USER,
    CHITCHAT_SYSTEM,
    HANDOFF_SYSTEM,
    INTENT_SYSTEM,
    RAG_SYSTEM,
    TOOL_SYSTEM,
    TOOL_USER,
)
from agent.state import AgentState
from agent.tools import apply_refund, create_ticket, query_logistics, query_order
from config import settings
from rag.retriever import Retriever

logger = logging.getLogger(__name__)

ORDER_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,2}\d{8,})(?![A-Za-z0-9])")

HUMAN_KEYWORDS = (
    "转人工", "人工客服", "真人", "投诉", "举报", "曝光",
    "12315", "消协", "起诉", "律师", "差评", "欺诈", "骗子",
)
LOGISTICS_KEYWORDS = (
    "物流", "快递", "到哪", "到哪儿", "运输", "签收", "派送",
    "轨迹", "运单", "单号", "什么时候到", "几天到",
)
REFUND_KEYWORDS = ("退款", "退货", "换货", "退钱", "售后", "退掉", "能退")
ORDER_KEYWORDS = ("订单", "发货", "地址", "取消", "买了", "下单", "支付")
POLICY_KEYWORDS = (
    "多久", "几天", "哪天", "从哪", "开始算", "时效", "运费", "谁承担", "谁出",
    "政策", "规则", "条件", "标准", "怎么", "如何", "能不能", "可以", "拆封", "无理由",
)
GREETING_KEYWORDS = (
    "你好", "您好", "hi", "hello", "在吗", "在么",
    "早上好", "中午好", "下午好", "晚上好", "谢谢", "感谢", "再见", "拜拜",
)
INTENTS = {"knowledge", "order", "logistics", "refund", "human", "chitchat"}

_llm = None


def get_llm():
    """懒加载 DeepSeek 客户端；未配置 key 时返回 None，走无 LLM 兜底"""
    global _llm
    if not settings.deepseek_api_key:
        return None
    if _llm is None:
        from langchain_openai import ChatOpenAI

        _llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=settings.llm_temperature,
            timeout=30,
            max_retries=2,
        )
    return _llm


def _chat(system: str, user: str) -> str:
    """单轮 LLM 调用，失败返回空串由调用方兜底"""
    llm = get_llm()
    if llm is None:
        return ""
    try:
        resp = llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
    except Exception as exc:
        logger.warning("LLM 调用失败：%s", exc)
        return ""
    return resp.content.strip() if isinstance(resp.content, str) else str(resp.content)


def _extract_order_no(text: str) -> str | None:
    match = ORDER_RE.search(text)
    return match.group(1).upper() if match else None


def _is_greeting(text: str) -> bool:
    normalized = text.strip().lower()
    return len(normalized) <= 10 and any(k in normalized for k in GREETING_KEYWORDS)


def _rule_classify(question: str) -> tuple[str | None, str | None]:
    """规则优先：大多数客服问题有明确关键词，比每次调 LLM 更稳更省"""
    text = question.strip()
    order_no = _extract_order_no(text)

    if any(k in text for k in HUMAN_KEYWORDS):
        return "human", order_no

    has_refund = any(k in text for k in REFUND_KEYWORDS)
    has_logistics = any(k in text for k in LOGISTICS_KEYWORDS)
    has_order = any(k in text for k in ORDER_KEYWORDS)
    is_policy = any(k in text for k in POLICY_KEYWORDS)

    if order_no:
        if has_refund:
            return "refund", order_no
        if has_logistics:
            return "logistics", order_no
        return "order", order_no

    # 不带订单号的通用规则咨询，归到 knowledge
    if has_refund and is_policy:
        return "knowledge", None
    if has_refund and any(k in text for k in ("吗", "呢", "？", "?")):
        return "knowledge", None
    if has_refund:
        return "refund", None          # 缺订单号，tool 节点会追问
    if has_logistics and (has_order or "我的" in text):
        return "logistics", None
    if has_order and ("我的" in text or "订单" in text):
        return "order", None
    if _is_greeting(text):
        return "chitchat", None

    return None, None


def _llm_classify(question: str) -> str:
    llm = get_llm()
    if llm is None:
        return "knowledge"

    try:
        resp = llm.invoke(
            [SystemMessage(content=INTENT_SYSTEM), HumanMessage(content=question)]
        )
    except Exception as exc:
        logger.warning("意图识别失败，回落 knowledge：%s", exc)
        return "knowledge"

    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    label = re.sub(r"[^a-z]", "", raw.strip().lower())
    return label if label in INTENTS else "knowledge"


def _format_history(history: list[dict]) -> str:
    if not history:
        return "（无历史对话）"
    lines = []
    for item in history:
        role = "用户" if item.get("role") == "user" else "客服"
        lines.append(f"{role}：{item.get('content', '')}")
    return "\n".join(lines)


def _dedupe_sources(docs: list[dict]) -> list[str]:
    sources: list[str] = []
    for doc in docs:
        source = doc.get("source")
        if source and source not in sources:
            sources.append(source)
    return sources


def _open_ticket(
    user_id: int,
    session_id: str,
    question: str,
    reason: str,
    route_to_human: bool = False,
) -> str:
    try:
        return create_ticket.invoke(
            {
                "user_id": user_id,
                "session_id": session_id,
                "question": question,
                "reason": reason,
                "route_to_human": route_to_human,
            }
        )
    except Exception:
        logger.exception("创建工单失败")
        return "人工工单系统暂时不可用，请稍后重试或直接联系在线人工客服。"


def build_graph(retriever: Retriever | None = None):
    """构建并编译 Agent 图。Milvus 连接在此建立，服务启动时调用一次即可"""
    retriever = retriever or Retriever()

    def classify_node(state: AgentState) -> dict[str, Any]:
        question = state.get("question", "")
        intent, order_no = _rule_classify(question)
        if intent is None:
            intent = _llm_classify(question)
        logger.info("意图识别 intent=%s order_no=%s", intent, order_no)
        return {"intent": intent, "order_no": order_no}

    def retrieve_node(state: AgentState) -> dict[str, Any]:
        try:
            hits = retriever.retrieve(state["question"])
        except Exception as exc:
            logger.warning("知识库检索失败：%s", exc)
            return {"docs": []}

        threshold = settings.rag_score_threshold
        docs = [hit for hit in hits if hit.get("score", 0) >= threshold]
        logger.info(
            "检索到 %s 条，阈值过滤后 %s 条（top1=%s）",
            len(hits),
            len(docs),
            f"{hits[0]['score']:.3f}" if hits else "-",
        )
        return {"docs": docs}

    def tool_node(state: AgentState) -> dict[str, Any]:
        intent = state.get("intent", "order")
        order_no = state.get("order_no")

        if not order_no:
            return {
                "tool_result": (
                    "系统没有识别到订单号。请礼貌地请用户提供订单号（形如 A20251101001），"
                    "拿到订单号后才能查询或办理售后。"
                )
            }

        try:
            uid = state.get("user_id", 0)
            if intent == "logistics":
                result = query_logistics.invoke({"order_no": order_no, "user_id": uid})
            elif intent == "refund":
                result = apply_refund.invoke(
                    {
                        "order_no": order_no,
                        "user_id": uid,
                        "reason": state.get("question", ""),
                    }
                )
            else:
                result = query_order.invoke({"order_no": order_no, "user_id": uid})
        except Exception as exc:
            logger.exception("工具执行失败")
            result = f"订单系统暂时不可用（{exc}），请稍后再试或转人工客服。"

        return {"tool_result": result}

    def handoff_node(state: AgentState) -> dict[str, Any]:
        question = state.get("question", "")
        hot_words = ("投诉", "曝光", "12315", "消协", "起诉", "差评", "欺诈")
        reason = "投诉/纠纷升级" if any(k in question for k in hot_words) else "用户要求转人工"
        return {
            "tool_result": _open_ticket(
                state.get("user_id", 0),
                state.get("session_id", ""),
                question,
                reason,
                route_to_human=True,
            )
        }

    def generate_node(state: AgentState) -> dict[str, Any]:
        intent = state.get("intent", "knowledge")
        question = state.get("question", "")

        # 商户在后台配置的客服人设，拼接到各场景的 system prompt
        extra = (state.get("agent_prompt") or "").strip()

        def system(base: str) -> str:
            if not extra:
                return base
            return f"{base}\n\n【店铺客服配置】\n{extra}\n（与平台规则冲突时以平台规则为准）"

        # ---- 转人工：安抚 + 报工单号 + 服务时间 ----
        if intent == "human":
            tool_result = state.get("tool_result", "")
            prompt = (
                f"用户问题：{question}\n\n"
                f"工单信息：\n{tool_result}\n\n"
                "请按规则回复用户。"
            )
            answer = _chat(system(HANDOFF_SYSTEM), prompt)
            if not answer:
                answer = f"非常理解您的心情，已为您转接人工处理。\n{tool_result}"
            return {"answer": answer, "sources": []}

        # ---- 订单 / 物流 / 退款：基于工具结果组织语言 ----
        if intent in {"order", "logistics", "refund"}:
            tool_result = state.get("tool_result", "")
            prompt = TOOL_USER.format(question=question, tool_result=tool_result)
            answer = _chat(system(TOOL_SYSTEM), prompt)
            return {"answer": answer or tool_result, "sources": []}

        # ---- 闲聊 ----
        if intent == "chitchat":
            answer = _chat(system(CHITCHAT_SYSTEM), question)
            return {
                "answer": answer
                or "您好，我是客服小星～请问有什么可以帮您？可以查订单、看物流，或咨询退换货政策。",
                "sources": [],
            }

        # ---- 知识问答 ----
        docs = state.get("docs") or []
        if not docs:
            ticket_result = _open_ticket(
                state.get("user_id", 0),
                state.get("session_id", ""),
                question,
                "知识库未覆盖",
            )
            answer = (
                "抱歉，这个问题我暂时没查到准确信息，已为您转人工进一步核实。\n"
                f"{ticket_result}"
            )
            return {"answer": answer, "sources": []}

        prompt = ANSWER_USER.format(
            profile=MemoryService.format_profile(state.get("profile") or {}),
            history=_format_history(state.get("history") or []),
            context=Retriever.build_context(docs),
            question=question,
        )
        answer = _chat(system(RAG_SYSTEM), prompt)

        if not answer:
            top = docs[0]
            preview = top["text"].strip()
            if len(preview) > 500:
                preview = preview[:500] + "……"
            answer = (
                f"根据《{top['source']}》中的说明：\n{preview}\n\n"
                "如需进一步确认，可以补充具体情况，或告诉我为您转人工客服。"
            )

        return {"answer": answer, "sources": _dedupe_sources(docs)}

    def route_after_classify(state: AgentState) -> str:
        intent = state.get("intent", "knowledge")
        if intent == "human":
            return "handoff"
        if intent in {"order", "logistics", "refund"}:
            return "tool"
        if intent == "chitchat":
            return "chitchat"
        return "retrieve"

    graph = StateGraph(AgentState)

    graph.add_node("classify", classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("tool", tool_node)
    graph.add_node("handoff", handoff_node)
    graph.add_node("generate", generate_node)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "retrieve": "retrieve",
            "tool": "tool",
            "handoff": "handoff",
            "chitchat": "generate",
        },
    )
    graph.add_edge("retrieve", "generate")
    graph.add_edge("tool", "generate")
    graph.add_edge("handoff", "generate")
    graph.add_edge("generate", END)

    return graph.compile()
