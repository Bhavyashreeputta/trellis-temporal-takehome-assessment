import pytest
from unittest.mock import AsyncMock
import src.app.activities as activities_mod

pytestmark = pytest.mark.asyncio


class FakeConn:
    def __init__(self):
        self.executed = []
        self.rows = {}

    async def execute(self, query, *args):
        # record every executed SQL
        self.executed.append((query, args))

    async def fetchrow(self, query, *args):
        # simulate payments lookups used by idempotency helpers
        if "FROM payments" in query:
            return self.rows.get(("payments", args[0]))
        return None

    async def fetch(self, query, *args):
        return []

    async def close(self):
        pass


class FakeDB:
    def __init__(self, conn):
        self._conn = conn

    async def connect(self):
        return self._conn


@pytest.fixture
def fake_conn():
    return FakeConn()


@pytest.fixture(autouse=True)
def disable_flaky_and_db(monkeypatch, fake_conn):
    monkeypatch.setattr(
        activities_mod,
        "order_received",
        AsyncMock(side_effect=lambda oid: {"order_id": oid, "items": [{"sku": "ABC", "qty": 1}]}),
    )
    monkeypatch.setattr(activities_mod, "order_validated", AsyncMock(return_value=True))
    monkeypatch.setattr(
        activities_mod,
        "payment_charged",
        AsyncMock(return_value={"status": "charged", "amount": 1}),
    )
    monkeypatch.setattr(activities_mod, "order_shipped", AsyncMock(return_value="Shipped"))
    monkeypatch.setattr(
        activities_mod, "package_prepared", AsyncMock(return_value="Package ready")
    )
    monkeypatch.setattr(
        activities_mod, "carrier_dispatched", AsyncMock(return_value="Dispatched")
    )

    monkeypatch.setattr(activities_mod.utils, "db", FakeDB(fake_conn))


async def test_receive_order_activity_inserts(fake_conn):
    res = await activities_mod.receive_order_activity("order-1")
    assert res["order_id"] == "order-1"
    sqls = " ".join(q for q, _ in fake_conn.executed)
    assert "INSERT INTO orders" in sqls
    assert ("ON CONFLICT" in sqls) or ("DO UPDATE" in sqls)
    assert "INSERT INTO events" in sqls


async def test_charge_payment_idempotent_short_circuit(fake_conn):
    fake_conn.rows[("payments", "pay-1")] = {
        "payment_id": "pay-1",
        "order_id": "order-2",
        "status": "CHARGED",
        "amount": 1,
    }
    payload = {"order": {"order_id": "order-2"}, "payment_id": "pay-1"}

    res = await activities_mod.charge_payment_activity(payload)

    assert res["status"].lower() in {"already_charged", "charged"}
    sqls = " ".join(q for q, _ in fake_conn.executed)
    assert "INSERT INTO payments" in sqls


async def test_charge_payment_success_persists_to_payments(fake_conn):
    payload = {"order": {"order_id": "order-3"}, "payment_id": "pay-2"}

    res = await activities_mod.charge_payment_activity(payload)
    assert res["status"] == "charged"
    assert res["amount"] == 1

    sqls = " ; ".join(q for q, _ in fake_conn.executed)
    assert "INSERT INTO payments" in sqls
    assert "UPDATE payments SET status='CHARGED'" in sqls or "UPDATE payments SET status = 'CHARGED'" in sqls
    assert "INSERT INTO events" in sqls
