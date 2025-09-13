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

# Stop and remove containers
docker compose down
```

This starts all required services:
- Temporal dev server (port 7233)
- PostgreSQL database (port 5432)
- FastAPI app (port 8000) with workers registered on `default` & `shipping-tq` task queues

## API Usage

### Start Order Workflow

```bash
curl -i -X POST "http://localhost:8000/orders/order-1/start" -H "Content-Type: application/json" -d "{\"payment_id\":\"pay-001\"}"
```

### Send Signals

**Approve order** (must be sent before review timer expires i.e. within 10sec):
```bash
curl -i -X POST "http://localhost:8000/orders/ord-1/signals/approve"
```

**Cancel order**:
```bash
curl -X POST "http://localhost:8000/orders/ord-1/signals/cancel" -H "Content-Type: application/json" -d "{}"
```

**Update address**:
```bash
curl -X POST "http://localhost:8000/orders/ord-1/signals/address" -H "Content-Type: application/json" -d "{\"line1\":\"123 Main St\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\"}"
```

### Check Status

```bash
curl -i "http://localhost:8000/orders/ord-1/status"
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

### Connect to Database

```bash
# Connect to PostgreSQL database
docker exec -it trellis_postgres psql -U app -d appdb
```

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
# Run all tests
python -m pytest

# Run individual test files
python -m pytest tests/test_workflows_integration.py
python -m pytest tests/test_activities.py
python -m pytest tests/test_child_workflows.py
```

### Run Tests in Docker Compose

```bash
docker compose exec trellis_app pytest -q
```

### Test Coverage

- **`tests/test_activities.py`** — Unit tests with mocked database; verifies payment writes and idempotency
- **`tests/test_workflows_integration.py`** — Parent workflow tests (approve within time window; time-skipping)
- **`tests/test_child_workflows.py`** — Child workflow tests on `shipping-tq` task queue

