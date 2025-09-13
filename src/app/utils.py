from typing import Dict, Any, Optional
from src.app.db import DB
import json

db = DB()

async def insert_event(order_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """
    Insert an event row tied to an order.
    Payload is JSON-serialized.
    """
    payload_json = json.dumps(payload or {})
    conn = await db.connect()
    try:
        await conn.execute(
            "INSERT INTO events (order_id, type, payload_json) VALUES ($1, $2, $3)",
            order_id, event_type, payload_json
        )
    finally:
        await conn.close()


async def upsert_order(order_id: str, status: str) -> None:
    """
    Insert a new order (idempotent) or update existing order state.
    Use this whenever you need to ensure an orders row exists.
    """
    conn = await db.connect()
    try:
        await conn.execute(
            """
            INSERT INTO orders (id, state, created_at, updated_at)
            VALUES ($1, $2, now(), now())
            ON CONFLICT (id) DO UPDATE SET
                state = EXCLUDED.state,
                updated_at = now()
            """,
            order_id, status
        )
    finally:
        await conn.close()


async def update_order_status(order_id: str, status: str) -> None:
    """
    Update the order status. This function delegates to upsert_order so it is safe to call
    even if the order row does not yet exist.
    """
    # Delegate to upsert to guarantee the row exists (idempotent).
    await upsert_order(order_id, status)


async def ensure_payment_idempotent(payment_id: str) -> Optional[Dict[str, Any]]:
    """
    Return an existing payment row (dict) if payment_id already exists, else None.
    Caller should treat None => no previous payment and proceed to charge.
    """
    conn = await db.connect()
    try:
        row = await conn.fetchrow(
            "SELECT payment_id, order_id, status, amount FROM payments WHERE payment_id=$1",
            payment_id
        )
        if not row:
            return None
        return {
            "payment_id": row["payment_id"],
            "order_id": row["order_id"],
            "status": row["status"],
            "amount": float(row["amount"])
        }
    finally:
        await conn.close()

async def insert_payment_init(payment_id: str, order_id: str) -> None:
    """
    Reserve a payment row idempotently. Safe across retries.
    """
    conn = await db.connect()
    try:
        await conn.execute(
            """
            INSERT INTO payments (payment_id, order_id, status, amount, created_at)
            VALUES ($1, $2, 'INIT', 0, now())
            ON CONFLICT (payment_id) DO NOTHING
            """,
            payment_id, order_id
        )
    finally:
        await conn.close()

async def update_payment_charged(payment_id: str, amount: float) -> None:
    """
    Mark a payment as CHARGED and set the amount.
    """
    conn = await db.connect()
    try:
        await conn.execute(
            "UPDATE payments SET status='CHARGED', amount=$2 WHERE payment_id=$1",
            payment_id, amount
        )
    finally:
        await conn.close()

async def update_payment_failed(payment_id: str) -> None:
    """
    Mark a payment as FAILED.
    """
    conn = await db.connect()
    try:
         await conn.execute(
            "UPDATE payments SET status='FAILED' WHERE payment_id=$1",
            payment_id
        )
    finally:
        await conn.close()

async def get_payment(payment_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch a payment row for idempotency checks.
    """
    conn = await db.connect()
    try:
        row = await conn.fetchrow(
            "SELECT payment_id, order_id, status, amount FROM payments WHERE payment_id=$1",
            payment_id
        )
        if not row:
            return None
        return {
            "payment_id": row["payment_id"],
            "order_id": row["order_id"],
            "status": row["status"],
            "amount": float(row["amount"] or 0),
        }
    finally:
        await conn.close()