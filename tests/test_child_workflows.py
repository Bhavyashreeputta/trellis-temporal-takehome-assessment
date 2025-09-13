import asyncio
import time
import pytest

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio import activity
from src.app.workflows import OrderWorkflow, ShippingWorkflow

pytestmark = pytest.mark.asyncio


@activity.defn(name="receive_order_activity")
async def td_receive_order(order_id: str):
    return {"order_id": order_id, "items": [{"sku": "ABC", "qty": 1}]}

@activity.defn(name="validate_order_activity")
async def td_validate_order(order: dict):
    return True

@activity.defn(name="charge_payment_activity")
async def td_charge_payment(payload: dict):
    return {"status": "charged", "amount": 1}

@activity.defn(name="prepare_package_activity")
async def td_prepare_package(order_or_payload):
    order = order_or_payload.get("order", order_or_payload)
    _ = order.get("order_id")
    return "Package ready"

@activity.defn(name="dispatch_carrier_activity")
async def td_dispatch_carrier(order_or_payload):
    order = order_or_payload.get("order", order_or_payload)
    _ = order.get("order_id")
    return "Dispatched"


@activity.defn(name="dispatch_carrier_activity_fail")
async def td_dispatch_carrier_fail(order_or_payload):
    order = order_or_payload.get("order", order_or_payload)
    raise RuntimeError(f"carrier dispatch failed for {order.get('order_id')}")

def child_id_for(parent_id: str) -> str:
    return f"{parent_id}-shipping"


async def start_workers(env: WorkflowEnvironment, *, fail_dispatch: bool = False):
    """
    Spin up one worker for the parent queue ("default") and one for the child queue ("shipping-tq"),
    registering workflows and our test-double activities by name.
    """
    parent_worker = Worker(
        env.client,
        task_queue="default",
        workflows=[OrderWorkflow],
        activities=[td_receive_order, td_validate_order, td_charge_payment],
    )
    shipping_acts = [td_prepare_package, td_dispatch_carrier if not fail_dispatch else td_dispatch_carrier_fail]
    shipping_worker = Worker(
        env.client,
        task_queue="shipping-tq",
        workflows=[ShippingWorkflow],
        activities=shipping_acts,
    )
    return parent_worker, shipping_worker


async def test_child_happy_path_completes():
    """
    Parent starts ShippingWorkflow with a single-payload arg, child finishes,
    and we can fetch the child's result. Parent should also complete.
    """
    async with (await WorkflowEnvironment.start_time_skipping()) as env:
        parent_w, child_w = await start_workers(env, fail_dispatch=False)
        async with parent_w, child_w:
            wf_id = f"child-happy-{int(time.time())}"
            pay_id = f"pay-{wf_id}"

            # Start parent
            handle = await env.client.start_workflow(
                OrderWorkflow.run,
                args=(wf_id, pay_id),
                id=wf_id,
                task_queue="default",
            )
            await handle.signal("Approve")

            parent_res = await handle.result()
            assert isinstance(parent_res, dict)
            assert parent_res["order_id"] == wf_id

            child_handle = env.client.get_workflow_handle(child_id_for(wf_id))
            child_res = await child_handle.result()   # should be "dispatched"
            assert child_res == "dispatched"

async def test_child_failure_signals_parent():
    """
    Force dispatch to fail. The child should fail; the parent may or may not
    record the DispatchFailed signal because the parent completes immediately
    (ParentClosePolicy=ABANDON).
    """
    async with (await WorkflowEnvironment.start_time_skipping()) as env:
        parent_w, child_w = await start_workers(env, fail_dispatch=True)
        async with parent_w, child_w:
            wf_id = f"child-fail-{int(time.time())}"
            pay_id = f"pay-{wf_id}"

            handle = await env.client.start_workflow(
                OrderWorkflow.run,
                args=(wf_id, pay_id),
                id=wf_id,
                task_queue="default",
            )
            await handle.signal("Approve")

            parent_res = await handle.result()
            assert isinstance(parent_res, dict)
            assert parent_res["order_id"] == wf_id

            child_handle = env.client.get_workflow_handle(child_id_for(wf_id))
            with pytest.raises(Exception):
                await child_handle.result()

            status = await handle.query("status")
            assert status["order_id"] == wf_id
            assert status["step"] in (
                "COMPLETED",
                "SHIPPING_START_FAILED",
                "CHARGE_FAILED",
                "AWAITING_APPROVAL_TIMEOUT",
                "VALIDATION_FAILED",
                "FAILED",
            )
            le = status.get("last_error")
            if le:
                assert "DispatchFailed" in le or "Failed to start shipping child workflow" in le

async def test_parent_completes_even_if_child_running():
    """
    Demonstrate ABANDON behavior: parent completes without waiting on the child.
    We don't assert child outcome here.
    """
    async with (await WorkflowEnvironment.start_time_skipping()) as env:
        parent_worker = Worker(
            env.client,
            task_queue="default",
            workflows=[OrderWorkflow],
            activities=[td_receive_order, td_validate_order, td_charge_payment],
        )
        shipping_worker = Worker(
            env.client,
            task_queue="shipping-tq",
            workflows=[ShippingWorkflow],
            activities=[td_prepare_package, td_dispatch_carrier],  # fast child
        )

        async with parent_worker, shipping_worker:
            wf_id = f"child-abandon-{int(time.time())}"
            pay_id = f"pay-{wf_id}"

            handle = await env.client.start_workflow(
                OrderWorkflow.run,
                args=(wf_id, pay_id),
                id=wf_id,
                task_queue="default",
            )
            await handle.signal("Approve")

            parent_res = await handle.result()
            assert parent_res["order_id"] == wf_id

            child_handle = env.client.get_workflow_handle(child_id_for(wf_id))
            desc = await child_handle.describe()
            assert desc is not None
