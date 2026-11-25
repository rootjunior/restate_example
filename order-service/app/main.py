"""Restate service that durably processes an order.

This process is not called directly by clients. Every call goes through the
Restate ingress, which records each durable step in the invocation journal,
retries failures and guarantees exactly-once execution.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import restate
from pydantic import BaseModel, Field

from .contracts import NotificationRequest, notify_customer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [order-service] %(message)s",
)
logger = logging.getLogger("order-service")


class OrderRequest(BaseModel):
    order_id: str = Field(description="Business order id, also used as the idempotency key")
    customer: str = Field(description="Customer name")
    item: str = Field(description="Item to ship")
    quantity: int = Field(default=1, gt=0, description="Number of items")
    amount: float = Field(gt=0, description="Order total in USD")


class OrderResult(BaseModel):
    order_id: str
    status: str
    payment_ref: str
    notification_ref: str
    steps: list[str]


order_service = restate.Service("OrderService")


def validate_order(order_id: str, customer: str) -> str:
    logger.info("Validating order %s for %s", order_id, customer)
    return "order validated"


def reserve_stock(order_id: str, item: str, quantity: int) -> str:
    logger.info("Reserving %s x %s for order %s", quantity, item, order_id)
    return f"reserved {quantity} x {item}"


def charge_payment(order_id: str, amount: float, payment_id: str) -> str:
    logger.info("Charging %.2f USD for order %s (payment %s)", amount, order_id, payment_id)
    return f"payment {payment_id} charged"


@order_service.handler()
async def process_order(ctx: restate.Context, req: OrderRequest) -> OrderResult:
    """Durable order workflow: every context action is journaled by Restate."""
    steps: list[str] = []

    steps.append(
        await ctx.run_typed(
            "validate order",
            validate_order,
            order_id=req.order_id,
            customer=req.customer,
        )
    )
    steps.append(
        await ctx.run_typed(
            "reserve stock",
            reserve_stock,
            order_id=req.order_id,
            item=req.item,
            quantity=req.quantity,
        )
    )

    # A durable timer. It keeps the invocation in the "running" state for a
    # couple of seconds, which makes the journal easy to explore in the UI.
    await ctx.sleep(timedelta(seconds=2), name="warehouse delay")

    payment_id = str(ctx.uuid())
    steps.append(
        await ctx.run_typed(
            "charge payment",
            charge_payment,
            order_id=req.order_id,
            amount=req.amount,
            payment_id=payment_id,
        )
    )

    # Durable callback: order-service calls NotificationService, which is
    # implemented by api-service. The call goes through Restate, so it is
    # retried, journaled and visible as a child invocation in the UI.
    notification = await ctx.service_call(
        notify_customer,
        NotificationRequest(
            order_id=req.order_id,
            customer=req.customer,
            message=f"Order {req.order_id} is on its way: {req.quantity} x {req.item}",
        ),
    )
    steps.append(f"notification {notification.notification_id} sent")

    return OrderResult(
        order_id=req.order_id,
        status="processed",
        payment_ref=payment_id,
        notification_ref=notification.notification_id,
        steps=steps,
    )


# ASGI application served by Hypercorn over HTTP/2 (Restate talks h2c to it).
app = restate.app(services=[order_service])
