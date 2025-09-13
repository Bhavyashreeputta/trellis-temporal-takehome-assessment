# Temporal Order Lifecycle (Python)

Orchestrates **Order → Manual Review → Payment → Shipping** with Temporal workflows.

## Architecture Overview

- **Parent Workflow**: `OrderWorkflow` on `default` task queue
- **Child Workflow**: `ShippingWorkflow` on `shipping-tq` task queue (ParentClosePolicy = **ABANDON**)
- **Activities**: receive, validate, charge, prepare, dispatch
- **API**: FastAPI server on port **8000**
- **Database**: PostgreSQL with tables `orders`, `payments`, `events` (idempotent payment writes)

## Quick Start

### Run with Docker Compose (Recommended)

```bash
# From repository root
docker compose up --build
```

This starts all required services:
- Temporal dev server (port 7233)
- PostgreSQL database (port 5432)
- FastAPI app (port 8000) with workers registered on `default` & `shipping-tq` task queues

### Manual Local Setup (Optional)

1. **Start Temporal dev server:**
   ```bash
   docker run --rm -d --name temporal -p 7233:7233 temporalio/auto-setup
   ```

2. **Start PostgreSQL:**
   ```bash
   docker run --rm -d --name appdb \
     -e POSTGRES_PASSWORD=postgres \
     -e POSTGRES_USER=postgres \
     -e POSTGRES_DB=appdb \
     -p 5432:5432 postgres:15
   ```

3. **Start API and workers:**
   ```bash
   uvicorn src.app.main:app --host 0.0.0.0 --port 8000
   ```

## API Usage

### Start Order Workflow

```bash
curl -X POST "http://localhost:8000/orders/ord-1/start" \
  -H "Content-Type: application/json" \
  -d '{"payment_id":"pay-1"}'
```

### Send Signals

**Approve order** (must be sent before review timer expires i.e. within 10sec):
```bash
curl -X POST "http://localhost:8000/orders/ord-1/signals/approve"
```

**Cancel order** (optional):
```bash
curl -X POST "http://localhost:8000/orders/ord-1/signals/cancel" \
  -H "Content-Type: application/json" \
  -d "{}"
```

**Update address**:
```bash
curl -X POST "http://localhost:8000/orders/ord-1/signals/address" \
  -H "Content-Type: application/json" \
  -d "{\"line1\":\"123 Main St\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\"}"
```

### Check Status

```bash
curl "http://localhost:8000/orders/ord-1/status"
```

Response format:
```json
{
  "order_id": "ord-1",
  "step": "...",
  "last_error": null
}
```

## Database Schema

### Tables

```sql
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  state TEXT,
  address_json JSONB,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY,
  order_id   TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  status     TEXT NOT NULL,
  amount     NUMERIC NOT NULL,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
  id SERIAL PRIMARY KEY,
  order_id TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json JSONB,
  ts TIMESTAMP DEFAULT now()
);
```

### Design Rationale

- **`orders`**: Current state snapshot for quick reads
- **`payments`**: Idempotency via unique `payment_id`
  - Reserve row (`INIT` status, amount 0) with `INSERT ... ON CONFLICT DO NOTHING`
  - On success: `UPDATE` status to `CHARGED` with actual amount
  - On failure: `UPDATE` status to `FAILED`
  - Safe under retries and concurrent execution
- **`events`**: Append-only audit trail for steps, retries, and errors (observability)

## Testing

### Run Tests Locally

```bash
python -m pytest
```

### Run Tests in Docker Compose

```bash
docker compose exec trellis_app pytest -q
```

### Test Coverage

- **`tests/test_activities.py`** — Unit tests with mocked database; verifies payment writes and idempotency
- **`tests/test_workflows_integration.py`** — Parent workflow tests (approve within time window; time-skipping)
- **`tests/test_child_workflows.py`** — Child workflow tests on `shipping-tq` task queue

