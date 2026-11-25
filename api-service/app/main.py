"""Serve both ASGI apps of api-service from one process:

* FastAPI + Swagger on 0.0.0.0:8000 (public, published by Compose)
* the Restate endpoint on 0.0.0.0:9080 (internal, used by the Restate runtime
  for NotificationService callbacks)

Hypercorn runs both servers in the same event loop. This keeps the container
single-process while letting Restate discover the callback service on its own
port.
"""

from __future__ import annotations

import asyncio
import signal

from hypercorn.asyncio import serve
from hypercorn.config import Config

from .api import api_app
from .notifications import restate_app


def _config(bind: str) -> Config:
    config = Config()
    config.bind = [bind]
    config.workers = 1
    # Restate keeps a long-lived bidirectional HTTP/2 stream to 9080.
    config.h2_max_concurrent_streams = 2147483647
    config.keep_alive_max_requests = 2147483647
    config.keep_alive_timeout = 2147483647
    return config


async def _serve() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await asyncio.gather(
        serve(api_app, _config("0.0.0.0:8000"), shutdown_trigger=stop.wait),
        serve(restate_app, _config("0.0.0.0:9080"), shutdown_trigger=stop.wait),
    )


if __name__ == "__main__":
    asyncio.run(_serve())
