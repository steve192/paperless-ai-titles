from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.config import get_settings
from .core.logging_config import configure_logging
from .core.database import Base, get_engine
from .routers import api as api_router
from .routers import ui as ui_router
from .services.scanner import get_scanner_service

settings = get_settings()
configure_logging(settings)
Base.metadata.create_all(bind=get_engine())
scanner_service = get_scanner_service()

app = FastAPI(title="Paperless AI Titles")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router.router)
app.include_router(ui_router.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def _startup() -> None:  # pragma: no cover - framework hook
    await scanner_service.start()


@app.on_event("shutdown")
async def _shutdown() -> None:  # pragma: no cover - framework hook
    await scanner_service.stop()
