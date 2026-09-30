import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.router import api_router
from src.config import settings
from src.database import AsyncSessionLocal, init_db
from src.workers.memory_queue import requeue_unfinished
from src.workers.queue import get_memory_queue, uses_memory_queue

templates_dir = os.path.join(os.path.dirname(__file__), "ui", "templates")
templates = Jinja2Templates(directory=templates_dir)

static_dir = os.path.join(os.path.dirname(__file__), "ui", "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes tables on startup."""
    # Ensure local data directory exists for SQLite
    os.makedirs("./data", exist_ok=True)
    await init_db()

    if not uses_memory_queue():
        yield
        return

    # Single-container mode: the web process also works the queue
    memory_queue = get_memory_queue()
    memory_queue.start()
    async with AsyncSessionLocal() as session:
        await requeue_unfinished(session, memory_queue.enqueue)
    yield
    await memory_queue.stop()


app = FastAPI(
    title=settings.APP_NAME,
    description="Skycoach Instagram Reels Ad Analyzer for Influence Managers",
    version="0.1.0",
    lifespan=lifespan,
)

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include API routes (/api/tasks, /api/export)
app.include_router(api_router, prefix="/api")


@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    """Serves the main web dashboard for influence managers."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint for container probes."""
    return {"status": "ok", "app": settings.APP_NAME}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
