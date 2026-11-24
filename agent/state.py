"""LangGraph 状态定义：节点之间流转的数据结构"""
from typing import TypedDict


class AgentState(TypedDict, total=False):
    # 输入
    user_id: int
    session_id: str
    question: str
    history: list[dict]          # [{"role": "user"/"assistant", "content": "..."}]
    profile: dict                # 长期记忆：nickname / preferences / facts
    agent_prompt: str            # 店铺自定义客服人设（商户在后台配置）

    # 中间结果
    intent: str                  # knowledge / order / logistics / refund / human / chitchat
    order_no: str | None
    docs: list[dict]             # 检索结果 [{"text","source","score"}]
    tool_result: str             # 工具或工单返回的文本

    # 输出
    answer: str
    sources: list[str]
