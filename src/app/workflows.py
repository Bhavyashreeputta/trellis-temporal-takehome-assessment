from __future__ import annotations
from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

RECV_ACT = "receive_order_activity"
VALIDATE_ACT = "validate_order_activity"
CHARGE_ACT = "charge_payment_activity"
SHIP_ACT = "ship_order_activity"
PREPARE_ACT = "prepare_package_activity"
DISPATCH_ACT = "dispatch_carrier_activity"

ACTIVITY_SCHEDULE_TO_CLOSE_TIMEOUT = timedelta(seconds=12)
ACTIVITY_START_TO_CLOSE_TIMEOUT = timedelta(seconds=4)
MANUAL_REVIEW_WINDOW = timedelta(seconds=10)
SHIPPING_WORKFLOW_TIMEOUT = timedelta(seconds=12)

DEFAULT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(milliseconds=250),
    maximum_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_attempts=5,
    non_retryable_error_types=["ValueError", "TypeError"],
)


def _is_timeout_exception(exc: Exception) -> bool:
    try:
        msg = str(exc).lower()
        return "timed out" in msg or "timeout" in msg
    except Exception:
        return False


@workflow.defn
class OrderWorkflow:
    def __init__(self) -> None:
        self.order_id: Optional[str] = None
        self.current_step: str = "initialized"
        self._approved: bool = False
        self._cancelled: bool = False
        self._last_error: Optional[str] = None

    async def _wait_for_approval_or_cancel(self, timeout: timedelta) -> None:
        try:
            await workflow.wait_condition(lambda: self._approved or self._cancelled, timeout=timeout)
        except TimeoutError:
            return

    @workflow.run
    async def run(self, order_id: str, payment_id: str) -> Dict[str, Any]:
        self.order_id = order_id

        try:
            self.current_step = "RECEIVING_ORDER"
            try:
                order = await workflow.execute_activity(
                    RECV_ACT,
                    order_id,
                    schedule_to_close_timeout=ACTIVITY_SCHEDULE_TO_CLOSE_TIMEOUT,
                    start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                    retry_policy=DEFAULT_RETRY_POLICY,
                )
            except Exception as exc:
                if _is_timeout_exception(exc):
                    self._last_error = "Activity task timed out during RECEIVE"
                else:
                    self._last_error = f"Receive error: {exc}"
                workflow.logger.error("Receive activity failed for %s: %s", order_id, self._last_error)
                order = {"order_id": order_id, "items": []}

            self.current_step = "VALIDATING_ORDER"
            try:
                valid = await workflow.execute_activity(
                    VALIDATE_ACT,
                    order,
                    schedule_to_close_timeout=ACTIVITY_SCHEDULE_TO_CLOSE_TIMEOUT,
                    start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                    retry_policy=DEFAULT_RETRY_POLICY,
                )
                valid = bool(valid)
            except Exception as exc:
                if _is_timeout_exception(exc):
                    self._last_error = "Activity task timed out during VALIDATION"
                else:
                    self._last_error = f"Validation error: {exc}"
                workflow.logger.error("Validate activity failed for %s: %s", order_id, self._last_error)
                valid = False

            self.current_step = "WAITING_FOR_APPROVAL"
            await self._wait_for_approval_or_cancel(MANUAL_REVIEW_WINDOW)

            if self._cancelled:
                self.current_step = "CANCELLED"
                return {"status": "cancelled", "order_id": order_id}
            if not self._approved:
                self._last_error = self._last_error or "Manual review timed out"
                self.current_step = "AWAITING_APPROVAL_TIMEOUT"
                return {"status": "manual_review_timeout", "order_id": order_id, "error": self._last_error}

            if not valid:
                self.current_step = "VALIDATION_FAILED"
                self._last_error = self._last_error or "Order validation failed"
                return {"status": "validation_failed", "order_id": order_id, "error": self._last_error}

            self.current_step = "CHARGING_PAYMENT"
            try:
                payment_result = await workflow.execute_activity(
                    CHARGE_ACT,
                    {"order": order, "payment_id": payment_id},
                    schedule_to_close_timeout=ACTIVITY_SCHEDULE_TO_CLOSE_TIMEOUT,
                    start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                    retry_policy=DEFAULT_RETRY_POLICY,
                )
            except Exception as exc:
                if _is_timeout_exception(exc):
                    self._last_error = "Activity task timed out during CHARGE"
                else:
                    self._last_error = f"Charge error: {exc}"
                workflow.logger.error("Charge activity failed for %s: %s", order_id, self._last_error)
                self.current_step = "CHARGE_FAILED"
                return {"status": "payment_failed", "order_id": order_id, "error": self._last_error}

            self.current_step = "STARTING_SHIPPING"
            try:
                # start child workflow asynchronously; do not await its completion here
                wid = workflow.info().workflow_id
                await workflow.start_child_workflow(
                    "ShippingWorkflow",
                    {"order": order, "parent_workflow_id": wid},
                    id=f"{wid}-shipping",
                    task_queue="shipping-tq",
                    execution_timeout=SHIPPING_WORKFLOW_TIMEOUT,
                    parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                )
            except Exception as exc:
                # If starting a child fails, record and return gracefully
                self._last_error = f"Failed to start shipping child workflow: {exc}"
                workflow.logger.error(self._last_error)
                self.current_step = "SHIPPING_START_FAILED"
                return {"status": "shipping_start_failed", "order_id": order_id, "error": self._last_error}

            self.current_step = "COMPLETED"
            self._last_error = None
            return {"status": "success", "order_id": order_id, "payment": payment_result}

        except Exception as exc:
            self._last_error = str(exc)
            self.current_step = "FAILED"
            workflow.logger.error(f"OrderWorkflow[{self.order_id}] failed: {exc}")
            # Re-raise so history reflects the failure 
            raise

    # --- Signals ---
    @workflow.signal
    def CancelOrder(self, reason: str = ""):
        self._cancelled = True

    @workflow.signal
    def UpdateAddress(self, address: Dict[str, Any]):
        self._address = address

    @workflow.signal
    def Approve(self):
        self._approved = True

    @workflow.signal
    def DispatchFailed(self, reason: str):
        self._last_error = f"DispatchFailed: {reason}"

    # --- Query ---
    @workflow.query
    def status(self) -> Dict[str, Any]:
        return {"order_id": self.order_id, "step": self.current_step, "last_error": self._last_error}


@workflow.defn
class ShippingWorkflow:
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> str:
        order: Dict[str, Any] = (payload or {}).get("order") or {}
        parent_workflow_id: Optional[str] = (payload or {}).get("parent_workflow_id")
        try:
            await workflow.execute_activity(
                PREPARE_ACT,
                order,
                schedule_to_close_timeout=ACTIVITY_SCHEDULE_TO_CLOSE_TIMEOUT,
                start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_RETRY_POLICY,
            )
            await workflow.execute_activity(
                DISPATCH_ACT,
                order,
                schedule_to_close_timeout=ACTIVITY_SCHEDULE_TO_CLOSE_TIMEOUT,
                start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_RETRY_POLICY,
            )
            return "dispatched"
        except Exception as exc:
            reason = str(exc)
            self._last_error = reason
            workflow.logger.error(f"ShippingWorkflow for order {order.get('order_id')} failed: {reason}")
            if parent_workflow_id:
                try:
                    await workflow.signal_external_workflow(parent_workflow_id, "", "DispatchFailed", reason)
                except Exception:
                    workflow.logger.exception("Failed to signal parent about dispatch failure")
            raise
 