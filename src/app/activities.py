from temporalio import activity
from src.app import utils
from src.app.function_stubs import (
    order_received,
    order_validated,
    payment_charged,
    order_shipped,
    package_prepared,
    carrier_dispatched,
)
from src.app.db import DB

db = DB()


@activity.defn
async def receive_order_activity(order_id: str):
    try:
        result = await order_received(order_id)
        await utils.upsert_order(order_id, "RECEIVED")
        await utils.insert_event(order_id, "ORDER_RECEIVED", result)
        return result
    except Exception as exc:
        err = str(exc)
        try:
            await utils.upsert_order(order_id, "RECEIVE_ERROR")
            await utils.insert_event(order_id, "ORDER_RECEIVE_FAILED", {"error": err})
        except Exception:
            activity.logger.exception("DB write failed during receive_order error")
        return {"order_id": order_id, "status": "receive_failed", "error": err}


@activity.defn
async def validate_order_activity(order: dict):
    order_id = order.get("order_id")
    try:
        valid = await order_validated(order)
        await utils.update_order_status(order_id, "VALIDATED" if valid else "INVALID")
        await utils.insert_event(order_id, "ORDER_VALIDATED", {"valid": valid})
        return bool(valid)
    except Exception as exc:
        err = str(exc)
        try:
            await utils.update_order_status(order_id, "VALIDATION_ERROR")
            await utils.insert_event(order_id, "ORDER_VALIDATION_FAILED", {"error": err})
        except Exception:
            activity.logger.exception("DB write failed during validate_order error")
        return False

@activity.defn(name="charge_payment_activity")
async def charge_payment_activity(payload: dict):
    order = (payload or {}).get("order") or {}
    payment_id = (payload or {}).get("payment_id")
    order_id = order.get("order_id")

    if not payment_id or not order_id:
        err = f"missing identifiers (order_id={order_id!r}, payment_id={payment_id!r})"
        await utils.insert_event(order_id or "unknown", "PAYMENT_FAILED",
                                 {"payment_id": payment_id, "error": err})
        return {"status": "failed", "payment_id": payment_id, "error": err}

    await utils.insert_payment_init(payment_id, order_id)

    existing = await (utils.ensure_payment_idempotent(payment_id)
                      if hasattr(utils, "ensure_payment_idempotent")
                      else utils.get_payment(payment_id))
    if existing and existing.get("status") == "CHARGED":
        try:
            await utils.insert_event(
                order_id, "PAYMENT_ALREADY_CHARGED",
                {"payment_id": payment_id, "amount": existing.get("amount")}
            )
        except Exception:
            activity.logger.exception("Event write failed for PAYMENT_ALREADY_CHARGED")
        return {"status": "already_charged", "amount": existing.get("amount", 0)}

    try:
        result = await payment_charged(order, payment_id, None)  
        amount = float(result.get("amount", 0.0))

        await utils.update_payment_charged(payment_id, amount)

        await utils.insert_event(
            order_id, "PAYMENT_CHARGED", {"payment_id": payment_id, "result": result}
        )
        await utils.update_order_status(order_id, "PAID")

        return {"status": "charged", "amount": amount}

    except Exception as exc:
        err = str(exc)
        try:
            await utils.update_payment_failed(payment_id)
        except Exception:
            activity.logger.exception("payments FAIL status update failed")
        try:
            await utils.insert_event(
                order_id, "PAYMENT_FAILED", {"payment_id": payment_id, "error": err}
            )
            await utils.update_order_status(order_id, "PAYMENT_FAILED")
        except Exception:
            activity.logger.exception("DB write failed during charge_payment failure handling")

        return {"status": "failed", "payment_id": payment_id, "error": err}

@activity.defn
async def ship_order_activity(order: dict):
    order_id = order.get("order_id")
    try:
        result = await order_shipped(order)
        await utils.update_order_status(order_id, "SHIPPED")
        await utils.insert_event(order_id, "ORDER_SHIPPED", {"result": result})
        return result
    except Exception as exc:
        err = str(exc)
        try:
            await utils.update_order_status(order_id, "SHIP_ERROR")
            await utils.insert_event(order_id, "ORDER_SHIP_FAILED", {"error": err})
        except Exception:
            activity.logger.exception("DB write failed during ship_order error")
        return {"status": "ship_failed", "order_id": order_id, "error": err}


@activity.defn
async def prepare_package_activity(order: dict):
    order_id = order.get("order_id")
    try:
        result = await package_prepared(order)
        await utils.insert_event(order_id, "PACKAGE_PREPARED", {"result": result})
        return result
    except Exception as exc:
        err = str(exc)
        try:
            await utils.insert_event(order_id, "PACKAGE_PREPARE_FAILED", {"error": err})
        except Exception:
            activity.logger.exception("DB write failed during prepare_package error")
        return {"status": "prepare_failed", "order_id": order_id, "error": err}


@activity.defn
async def dispatch_carrier_activity(order: dict):
    order_id = order.get("order_id")
    try:
        result = await carrier_dispatched(order)
        await utils.insert_event(order_id, "CARRIER_DISPATCHED", {"result": result})
        return result
    except Exception as exc:
        err = str(exc)
        try:
            await utils.insert_event(
                order_id, "CARRIER_DISPATCH_FAILED", {"error": err}
            )
        except Exception:
            activity.logger.exception("DB write failed during dispatch_carrier error")
        return {"status": "dispatch_failed", "order_id": order_id, "error": err}
