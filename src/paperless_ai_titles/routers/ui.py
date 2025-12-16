from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..core.models import ProcessingJobStatus
from ..services.settings import SettingsService

templates = Jinja2Templates(directory="templates")
settings_service = SettingsService()

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if settings_service.needs_onboarding():
        return templates.TemplateResponse("setup.html", {"request": request})
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {"request": request})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


@router.get("/jobs/history", response_class=HTMLResponse)
async def job_history_page(request: Request):
    if settings_service.needs_onboarding():
        return templates.TemplateResponse("setup.html", {"request": request})
    statuses = [status.value for status in ProcessingJobStatus]
    return templates.TemplateResponse(
        "job_history.html",
        {
            "request": request,
            "job_statuses": statuses,
        },
    )
