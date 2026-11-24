"""FastAPI 入口：星选商城 · 用户 / 商户 两端 + AI 客服"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from api import auth, chat, merchant
from api import user as user_api
from config import BASE_DIR, settings
from security import RoleSessionMiddleware, get_current_user, home_for

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时构建 Agent 图；Milvus 不可用时降级，不影响商城功能"""
    try:
        from agent.graph import build_graph
        from rag.retriever import Retriever

        retriever = Retriever()
        app.state.retriever = retriever
        app.state.graph = build_graph(retriever)
        logger.info("Agent 图构建完成")
    except Exception as exc:  # noqa: BLE001
        app.state.graph = None
        app.state.retriever = None
        logger.warning("Agent 初始化失败，知识问答降级：%s", exc)
    yield


app = FastAPI(title="星选商城 · 智能客服平台", lifespan=lifespan)

app.add_middleware(
    RoleSessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

for router in (auth.router, user_api.router, chat.router, merchant.router):
    app.include_router(router)


@app.get("/")
def index(user=Depends(get_current_user)):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse(home_for(user.role), status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}
