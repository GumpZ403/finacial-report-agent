"""
FastAPI 主入口
- 挂载 Agent 对话路由（统一对话入口，意图 + 工具调度）
- 配置 CORS
- 提供健康检查接口
- 托管前端构建产物（frontend/dist，SPA fallback）与报告下载目录

启动（二选一，均以仓库根为工作目录）：
    cd backend && python -m uvicorn app.main:app --reload --port 8000
    python -m backend.app.main          # 需在仓库根执行
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.platform.settings import get_settings
from app.interfaces.http.routes.agent import router as agent_router
# 组合根：显式 import 各领域的工具包以触发注册（否则 uvicorn 启动后 LLM 看不到任何工具）
import app.domains.financial.tools  # noqa: F401  注册 query_company / query_financials / query_data_stats
# 注：app.domains.retrieval.tools（rag_search）**故意不注册**（2026-10 冻结）：
#     当前语料是"结构化数值转成的文本"，检索回来仍是可精确查询的数字且精度更低，
#     注册只会让模型多花一轮预算。等接入财报/公告原文语料后再打开这一行，
#     并配合检索评测（recall@k / 引用正确率）一起上线。


_NOT_BUILT = {
    "error": "前端尚未构建",
    "hint": "cd frontend && npm install && npm run build",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动初始化 + 关闭清理"""
    settings = get_settings()
    logger.info(f"启动财报解读 Agent v{__import__('app').__version__}")
    logger.info(f"LLM 模型: {settings.LLM_MODEL}")
    logger.info(f"Embedding 模型: {settings.EMBEDDING_MODEL}")
    logger.info(f"SQLite: {settings.DATABASE_URL}")
    logger.info(f"Chroma: {settings.CHROMA_PERSIST_DIR}")
    logger.info(f"前端产物: {settings.frontend_dist_dir} (存在={settings.frontend_dist_dir.is_dir()})")

    # 会话记忆库：首次获取即建表（sessions / conversations）。失败只记日志，不影响服务启动
    from app.harness.store import get_conversation_store

    store = get_conversation_store()
    logger.info(f"会话记忆库: {store.db_path} · {store.stats()}")

    yield

    logger.info("系统关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    settings = get_settings()

    app = FastAPI(
        title="财报解读 Agent",
        description="财报数据检索、解读与研究辅助系统",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    # 注意：allow_origins=["*"] 与 allow_credentials=True 在规范上互斥，
    # 浏览器会直接拒绝带凭据的跨域请求。本服务不用 Cookie 鉴权（无会话 Cookie），
    # 所以用通配 + 关闭凭据；需要带凭据时改成显式 origin 白名单。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载路由（API 只保留对话入口 + 健康检查）
    app.include_router(agent_router)

    # 健康检查
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "financial-report-agent", "version": "1.0.0"}

    # 报告导出目录（Agent 生成的 .md/.docx 经 /reports/<文件名> 下载）
    reports_dir = settings.reports_dir
    app.mount("/reports", StaticFiles(directory=str(reports_dir)), name="reports")

    # 前端构建产物（Vite 输出到 frontend/dist；为唯一前端，旧 app/static 已删除）
    dist_dir = settings.frontend_dist_dir
    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # API 摘要（供 /docs 与调试）
    @app.get("/api")
    async def api_index():
        return {
            "service": "财报解读 Agent",
            "version": "1.0.0",
            "endpoints": {
                "Agent对话": "/api/agent/chat",
                "流式对话": "/api/agent/chat/stream",
                "记忆": "/api/agent/memory",
                "清空记忆": "/api/agent/clear",
            },
        }

    # 根路径：返回前端入口
    @app.get("/", include_in_schema=False)
    async def index():
        index_file = dist_dir / "index.html"
        if not index_file.is_file():
            return JSONResponse(_NOT_BUILT, status_code=503)
        return FileResponse(index_file)

    # SPA fallback：非 API 路径一律交给前端路由（必须在所有路由/挂载之后注册）
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        candidate = dist_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index_file = dist_dir / "index.html"
        if not index_file.is_file():
            return JSONResponse(_NOT_BUILT, status_code=503)
        return FileResponse(index_file)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
        log_level=settings.LOG_LEVEL.lower(),
    )
