"""NotificationService: the callback handler implemented by api-service.

order-service invokes this service through Restate after it has charged the
payment. Running it here (instead of an outbound HTTP call from order-service)
means Restate owns retries, idempotency and the invocation journal for the
callback as well.
"""

from __future__ import annotations

import logging

import restate
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [api-service] %(message)s",
)
logger = logging.getLogger("api-service")


class NotificationRequest(BaseModel):
    order_id: str
    customer: str
    message: str


class NotificationResult(BaseModel):
    notification_id: str
    delivered: bool


notification_service = restate.Service("NotificationService")


def deliver_notification(notification_id: str, customer: str, message: str) -> str:
    logger.info(
        "Delivering notification %s to %s: %s",
        notification_id,
        customer,
        message,
    )
    return "delivered"


@notification_service.handler()
async def notify_customer(
    ctx: restate.Context,
    req: NotificationRequest,
) -> NotificationResult:
    notification_id = str(ctx.uuid())
    await ctx.run_typed(
        "deliver notification",
        deliver_notification,
        notification_id=notification_id,
        customer=req.customer,
        message=req.message,
    )
    return NotificationResult(notification_id=notification_id, delivered=True)


# ASGI app served by Hypercorn on port 9080 (HTTP/2, h2c).
restate_app = restate.app(services=[notification_service])
