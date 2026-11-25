"""Client-side contract for the callback service implemented by api-service.

The real handler lives in `api-service/app/notifications.py`. This stub only
exists so that order-service can make a typed durable call:

    await ctx.service_call(notify_customer, NotificationRequest(...))

The stub is deliberately not listed in `restate.app(services=[...])` in
`main.py`, so order-service does not expose a second NotificationService
deployment.
"""

from __future__ import annotations

import restate
from pydantic import BaseModel


class NotificationRequest(BaseModel):
    order_id: str
    customer: str
    message: str


class NotificationResult(BaseModel):
    notification_id: str
    delivered: bool


notification_service = restate.Service("NotificationService")


@notification_service.handler()
async def notify_customer(ctx: restate.Context, req: NotificationRequest) -> NotificationResult:
    raise NotImplementedError("NotificationService is implemented by api-service")
