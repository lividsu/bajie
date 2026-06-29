from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any


class _CurrentLoop:
    """Thread-safe proxy that delegates to the current thread's event loop.

    The lark_oapi.ws.client library uses a module-level ``loop`` variable
    shared across *all* threads.  When two tenants run in separate threads,
    thread B overwrites thread A's loop, causing "Task attached to a
    different loop" and "This event loop is already running".

    Replacing the module variable with a proxy ensures each thread's calls
    hit its own event loop transparently.  ``asyncio.get_event_loop()`` is
    thread-local, so every attribute access resolves to the calling thread's
    loop — even if the proxy object itself was created elsewhere.
    """

    def __getattr__(self, name: str):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return getattr(loop, name)


class FeishuWebSocketService:
    def __init__(self, tenant_registry):
        self.tenant_registry = tenant_registry
        self._workers: list[_TenantWebSocketWorker] = []

    def start(self):
        if self._workers:
            return
        for tenant_config in self.tenant_registry.list_configs():
            if not tenant_config.feishu.app_id or not tenant_config.feishu.app_secret:
                logging.warning(
                    "Skipping tenant %s: missing Feishu app_id/app_secret",
                    tenant_config.tenant_id,
                )
                continue
            try:
                tenant = self.tenant_registry.get(tenant_config.tenant_id)
            except Exception as exc:
                logging.error(
                    "Skipping tenant %s: failed to initialize runtime: %s",
                    tenant_config.tenant_id,
                    exc,
                )
                continue
            worker = _TenantWebSocketWorker(tenant)
            worker.start()
            self._workers.append(worker)

    def stop(self):
        for worker in self._workers:
            worker.stop()
        self._workers.clear()


class _TenantWebSocketWorker:
    def __init__(self, tenant):
        self.tenant = tenant
        self._running = False
        self._thread: threading.Thread | None = None
        self._client: Any = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name=f"feishu-ws-{self.tenant.tenant_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        import lark_oapi as lark
        import lark_oapi.ws.client as lark_ws_client

        from core.event_handler import message_receive_event_handler

        # Patch the module-level loop so every thread gets its own.
        previous_loop = getattr(lark_ws_client, "loop", None)
        lark_ws_client.loop = _CurrentLoop()

        try:
            feishu = self.tenant.config.feishu
            builder = lark.EventDispatcherHandler.builder(
                feishu.encrypt_key or "",
                feishu.verification_token or "",
            ).register_p2_im_message_receive_v1(
                lambda data: message_receive_event_handler(data, self.tenant)
            )
            event_handler = builder.build()
            self._client = lark.ws.Client(
                feishu.app_id,
                feishu.app_secret,
                domain=feishu.lark_host,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )

            while self._running:
                try:
                    logging.info("Starting Feishu WebSocket for tenant=%s", self.tenant.tenant_id)
                    self._client.start()
                except Exception:
                    logging.exception("Feishu WebSocket stopped for tenant=%s", self.tenant.tenant_id)
                if self._running:
                    time.sleep(5)
        finally:
            lark_ws_client.loop = previous_loop
