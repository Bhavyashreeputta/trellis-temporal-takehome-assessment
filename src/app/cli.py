import asyncio
import sys
from temporalio.client import Client
from datetime import timedelta

TEMPORAL_ADDRESS = "localhost:7233" 

async def start(order_id: str, payment_id: str):
    client = await Client.connect(TEMPORAL_ADDRESS)
    handle = await client.start_workflow(
        "src.app.workflows.OrderWorkflow.run",
        order_id,
        payment_id,
        id=order_id,
        task_queue="default",
        workflow_run_timeout=timedelta(seconds=15)
    )
    print("Started:", handle.id, handle.run_id)

async def signal(order_id: str, name: str, payload=None):
    client = await Client.connect(TEMPORAL_ADDRESS)
    await client.signal_workflow(order_id, "", name, payload or "")
    print("Signalled", name, "to", order_id)

async def query(order_id: str):
    client = await Client.connect(TEMPORAL_ADDRESS)
    resp = await client.query_workflow(order_id, "", "status")
    print(resp)

def usage():
    print("Usage:")
    print("  cli.py start <order_id> <payment_id>")
    print("  cli.py signal <order_id> <SignalName> [payload_json]")
    print("  cli.py query <order_id>")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "start" and len(sys.argv) == 4:
        asyncio.run(start(sys.argv[2], sys.argv[3]))
    elif cmd == "signal" and len(sys.argv) >= 4:
        payload = None
        if len(sys.argv) == 5:
            import json
            payload = json.loads(sys.argv[4])
        asyncio.run(signal(sys.argv[2], sys.argv[3], payload))
    elif cmd == "query" and len(sys.argv) == 3:
        asyncio.run(query(sys.argv[2]))
    else:
        usage()
