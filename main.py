import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import config
from core.cleanup import cleanup_tenant_files
from core.dependencies import event_manager, tenant_registry
from lark_bot.ws import FeishuWebSocketService

# Import handlers so their decorators register with the event manager.
import core.event_handler  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_tenant_files(tenant_registry.list_configs())
    ws_service = None
    if config.RUN_MODE in {"websocket", "ws", "both"}:
        ws_service = FeishuWebSocketService(tenant_registry)
        ws_service.start()
    try:
        yield
    finally:
        if ws_service is not None:
            ws_service.stop()


app = FastAPI(title="Bajie Bot", lifespan=lifespan)


@app.exception_handler(Exception)
async def msg_error_handler(request: Request, exc: Exception):
    logging.exception("Request failed")
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", 500)
    return JSONResponse(status_code=status_code, content={"message": str(exc)})


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/")
async def callback_event_handler(request: Request):
    return await _handle_callback(request, config.DEFAULT_TENANT_ID)


@app.post("/tenant/{tenant_id}/callback")
async def tenant_callback_event_handler(tenant_id: str, request: Request):
    return await _handle_callback(request, tenant_id)


async def _handle_callback(request: Request, tenant_id: str):
    try:
        tenant = tenant_registry.get(tenant_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"message": f"tenant not found: {tenant_id}"})

    body = await request.body()
    event_handler, event = event_manager.get_handler_with_event(
        tenant.config.feishu.verification_token,
        tenant.config.feishu.encrypt_key or "",
        body,
        request.headers,
    )
    if event_handler is None:
        return JSONResponse(status_code=404, content={"message": "event handler not found"})
    result = event_handler(event, tenant)
    return result if result is not None else {}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=config.DEBUG,
    )
