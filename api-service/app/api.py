"""FastAPI facade that calls the Restate OrderService through the ingress.

Swagger UI:  http://localhost:8000/docs
Restate UI:  http://localhost:9070/ui
"""

from __future__ import annotations

import logging
import os
import uuid

import httpx
from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [api-service] %(message)s",
)
logger = logging.getLogger("api-service")

RESTATE_INGRESS_URL = os.getenv("RESTATE_INGRESS_URL", "http://localhost:8080").rstrip("/")
ORDER_HANDLER_URL = f"{RESTATE_INGRESS_URL}/OrderService/process_order"
REQUEST_TIMEOUT_SECONDS = float(os.getenv("RESTATE_REQUEST_TIMEOUT_SECONDS", "60"))


class OrderRequest(BaseModel):
    order_id: str = Field(
        default_factory=lambda: f"order-{uuid.uuid4().hex[:8]}",
        description=(
            "Order id. It is sent to Restate as the idempotency key, so calling "
            "the endpoint twice with the same order_id returns the same invocation "
            "instead of processing the order again."
        ),
        examples=["order-42"],
    )
    customer: str = Field(examples=["Ada Lovelace"])
    item: str = Field(examples=["Mechanical keyboard"])
    quantity: int = Field(default=1, ge=1, examples=[2])
    amount: float = Field(default=199.90, gt=0, examples=[399.80])


class OrderResult(BaseModel):
    order_id: str
    status: str
    payment_ref: str
    notification_ref: str
    steps: list[str]


api_app = FastAPI(
    title="Order API",
    version="1.0.0",
    description=(
        "FastAPI facade with Swagger. `POST /orders` calls the durable "
        "`OrderService.process_order` handler through the Restate ingress. "
        "The order service then calls `NotificationService.notify_customer` "
        "back through Restate; that handler is served by this same container "
        "on port 9080. Both invocations are visible in the Restate UI at "
        "http://localhost:9070/ui."
    ),
)


@api_app.get("/health", tags=["system"], summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "restate_ingress": RESTATE_INGRESS_URL}


@api_app.post(
    "/orders",
    response_model=OrderResult,
    tags=["orders"],
    summary="Process an order via Restate",
    description=(
        "Calls `OrderService/process_order` on the Restate ingress. Restate "
        "invokes the order-service, records every durable step in the "
        "invocation journal, and the order-service calls NotificationService "
        "back through Restate before returning the result."
    ),
    response_description="The result produced by the Restate OrderService",
)
async def create_order(
    order: OrderRequest = Body(
        ...,
        openapi_examples={
            "default": {
                "summary": "A simple order",
                "value": {
                    "customer": "Ada Lovelace",
                    "item": "Mechanical keyboard",
                    "quantity": 2,
                    "amount": 399.80,
                },
            }
        },
    ),
) -> OrderResult:
    payload = order.model_dump()
    headers = {"idempotency-key": order.order_id}

    logger.info("Calling Restate: POST %s (order_id=%s)", ORDER_HANDLER_URL, order.order_id)

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(ORDER_HANDLER_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.exception("Could not reach Restate")
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Restate ingress at {RESTATE_INGRESS_URL}: {exc}",
        ) from exc

    if response.status_code >= 400:
        logger.error("Restate returned %s: %s", response.status_code, response.text)
        raise HTTPException(
            status_code=502,
            detail=f"Restate returned {response.status_code}: {response.text}",
        )

    return OrderResult.model_validate(response.json())
