import pytest
import time
from temporalio.client import Client
from src.app import workflows as workflows_mod

pytestmark = pytest.mark.asyncio

TEMPORAL_ADDRESS = "localhost:7233"


async def test_order_workflow_end_to_end():
    client = await Client.connect(TEMPORAL_ADDRESS)

    wf_id = f"int-test-{int(time.time())}"
    payment_id = f"pay-{wf_id}"

    handle = await client.start_workflow(
        workflows_mod.OrderWorkflow.run,
        args=(wf_id, payment_id),
        id=wf_id,
        task_queue="default",
    )

    await handle.signal("Approve")

    result = await handle.result()
    assert isinstance(result, dict)
    assert result.get("order_id") == wf_id



