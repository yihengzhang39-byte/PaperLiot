"""FastAPI entry point for PaperPilot."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.chat import router as chat_router
from app.api.routes.paper import router as paper_router
from app.core.config import ensure_storage_dirs


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    ensure_storage_dirs()

    app = FastAPI(
        title="PaperPilot",
        description="科研论文阅读与精读 Agent 后端",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(paper_router, prefix="/api/papers", tags=["papers"])
    app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def read_index() -> FileResponse:
        """返回浏览器前端页面。"""
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health_check() -> dict[str, str]:
        """Return service health status."""
        return {"status": "ok"}

    return app


app = create_app()
