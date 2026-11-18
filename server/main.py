"""FastAPI 应用入口。"""
from __future__ import annotations

import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import images, jobs, models, processing, projects, results, system
from .core.config import APP_NAME, APP_VERSION, WEB_DIR, ensure_dirs
from .core.store import ImageNotFound, ProjectNotFound
from .services.models import ModelNotFound

ensure_dirs()

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="FreeFarmNRS 氮素遥感监测与反演服务",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.include_router(system.router)
app.include_router(projects.router)
app.include_router(images.router)
app.include_router(processing.router)
app.include_router(processing.batch_router)
app.include_router(models.router)
app.include_router(results.image_router)
app.include_router(results.project_router)
app.include_router(jobs.router)


@app.get("/api/health", tags=["system"])
def health():
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION}


@app.exception_handler(ProjectNotFound)
async def project_not_found_handler(request: Request, exc: ProjectNotFound):
    return JSONResponse(status_code=404, content={"detail": "项目不存在或已被删除"})


@app.exception_handler(ImageNotFound)
async def image_not_found_handler(request: Request, exc: ImageNotFound):
    return JSONResponse(status_code=404, content={"detail": "影像不存在或已被删除"})


@app.exception_handler(ModelNotFound)
async def model_not_found_handler(request: Request, exc: ModelNotFound):
    return JSONResponse(status_code=404, content={"detail": "模型不存在或已被删除"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器处理失败：{exc}"},
    )


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
