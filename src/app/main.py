import os
FLAKY_MODE = os.environ.setdefault("FLAKY_MODE", "ON").upper()

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import timedelta
import logging

from src.app.db import run_migrations, DB
from src.app import activities as activities_mod
from src.app import workflows as workflows_mod

from temporalio.client import Client
from temporalio.worker import Worker
from datetime import timedelta

DATABASE_URL = os.getenv("DATABASE_URL", "postgres://app:trellisAI@postgres:5432/appdb")
TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "temporal:7233")
# FLAKY_MODE = os.getenv("FLAKY_MODE", "OFF").upper()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trellis_app")

db = DB()

class StartOrder(BaseModel):
    payment_id: str

async def _run_worker(worker: Worker, name: str):
    try:
        logger.info("Starting worker for task queue: %s", name)
        await worker.run()
    except Exception:
        logger.exception("Worker on %s exited", name)

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await run_migrations()
    except Exception as e:
        logger.exception("Failed to run migrations at startup: %s", e)

    client = await Client.connect(TEMPORAL_ADDRESS)
    app.state.temporal_client = client

    worker = Worker(
        client,
        task_queue="default",
        workflows=[workflows_mod.OrderWorkflow],
        activities=[
            activities_mod.receive_order_activity,
            activities_mod.validate_order_activity,
            activities_mod.charge_payment_activity,
            activities_mod.ship_order_activity,
            activities_mod.prepare_package_activity,
            activities_mod.dispatch_carrier_activity,
        ],
    )

    shipping_worker = Worker(
        client,
        task_queue="shipping-tq",
        workflows=[workflows_mod.ShippingWorkflow],
        activities=[
            activities_mod.prepare_package_activity,
            activities_mod.dispatch_carrier_activity,
        ],
    )

    loop = asyncio.get_running_loop()
    loop.create_task(_run_worker(worker, "default"))
    loop.create_task(_run_worker(shipping_worker, "shipping-tq"))
    
    yield
    
    # Shutdown
    # Nothing explicit to shut down here; client/worker cleanup is handled by library.
    pass

app = FastAPI(lifespan=lifespan)

@app.post("/orders/{order_id}/start")
async def start_order(order_id: str, body: StartOrder):
    client: Client = app.state.temporal_client
    if not client:
        raise HTTPException(status_code=503, detail="Temporal client not ready")

    try:
        handle = await client.start_workflow(
            workflows_mod.OrderWorkflow.run,
            args=(order_id, body.payment_id),
            id=order_id,
            task_queue="default",
        )
        return {"workflow_id": handle.id, "run_id": getattr(handle, "run_id", None)}
    except Exception as exc:
        logger.exception("Failed to start workflow: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/orders/{order_id}/signals/cancel")
async def cancel_order(order_id: str, payload: dict = {}):
    client: Client = app.state.temporal_client
    try:
        handle = client.get_workflow_handle(order_id)
        await handle.signal("CancelOrder", payload.get("reason", ""))
        return {"status": "signalled"}
    except Exception as exc:
        logger.exception("Signal CancelOrder failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/orders/{order_id}/signals/update-address")
async def update_address(order_id: str, payload: dict):
    client: Client = app.state.temporal_client
    try:
        handle = client.get_workflow_handle(order_id)
        await handle.signal("UpdateAddress", payload)
        return {"status": "signalled"}
    except Exception as exc:
        logger.exception("Signal UpdateAddress failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/orders/{order_id}/signals/approve")
async def approve(order_id: str):
    client: Client = app.state.temporal_client
    try:
        handle = client.get_workflow_handle(order_id)
        await handle.signal("Approve")
        return {"status": "approved"}
    except Exception as exc:
        logger.exception("Signal Approve failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/orders/{order_id}/status")
async def status(order_id: str):
    client: Client = app.state.temporal_client
    try:
        handle = client.get_workflow_handle(order_id)
        resp = await handle.query("status")
        return resp
    except Exception as exc:
        logger.exception("Workflow query failed; returning DB events: %s", exc)
        from src.app.db import DB as DBClass
        dbconn = DBClass()
        conn = await dbconn.connect()
        try:
            rows = await conn.fetch(
                "SELECT id, type, payload_json, ts FROM events WHERE order_id=$1 ORDER BY ts DESC LIMIT 50",
                order_id,
            )
        finally:
            await conn.close()
        return {"workflow_query_error": str(exc), "recent_events": [dict(r) for r in rows]}