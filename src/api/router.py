from fastapi import APIRouter

from src.api.endpoints.export import router as export_router
from src.api.endpoints.tasks import router as tasks_router

api_router = APIRouter()
api_router.include_router(tasks_router)
api_router.include_router(export_router)
