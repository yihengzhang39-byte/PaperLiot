"""FastAPI entry point for PaperPilot."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.paper import router as paper_router
from app.core.config import ensure_storage_dirs


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

    @app.get("/health")
    def health_check() -> dict[str, str]:
        """Return service health status."""
        return {"status": "ok"}

    return app


app = create_app()
