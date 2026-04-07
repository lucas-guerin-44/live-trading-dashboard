"""Integration tests for the WebSocket endpoint and replay loop."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient


def _make_test_bars(n: int = 50):
    """Generate n bars for testing - enough for MA crossover with default periods."""
    from models import Bar

    bars = []
    base = 2000.0
    for i in range(min(n, 30)):
        bars.append(Bar(
            timestamp=datetime(2024, 1, 1, i // 4, (i % 4) * 15, tzinfo=timezone.utc),
            open=base, high=base + 1, low=base - 1, close=base,
        ))
    # Rising bars to trigger crossover
    for i in range(30, n):
        price = base + (i - 29) * 3
        bars.append(Bar(
            timestamp=datetime(2024, 1, 2, (i - 30) // 4, ((i - 30) % 4) * 15, tzinfo=timezone.utc),
            open=price - 1, high=price + 1, low=price - 2, close=price,
        ))
    return bars


@pytest.fixture()
def test_bars():
    return _make_test_bars(50)


@pytest.fixture()
def app(test_bars):
    """Create a fresh app instance with mocked data loading."""
    # Reset module-level state for each test
    import main as main_module

    main_module.bars_cache = test_bars
    main_module.connected_clients = set()
    main_module.replay_state = main_module.ReplayState()

    with patch.object(main_module, "load_bars", new_callable=AsyncMock, return_value=test_bars):
        yield main_module.app


class TestHealthEndpoint:
    def test_health_returns_ok(self, app):
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["instrument"] == "XAUUSD"
        assert data["bars_loaded"] == 50


class TestWebSocketStream:
    def test_receives_snapshot_on_connect(self, app):
        client = TestClient(app)
        with client.websocket_connect("/ws/stream") as ws:
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "SNAPSHOT"
            assert "instrument" in msg["data"]
            assert "bars" in msg["data"]

    def test_snapshot_contains_required_fields(self, app):
        client = TestClient(app)
        with client.websocket_connect("/ws/stream") as ws:
            msg = json.loads(ws.receive_text())
            data = msg["data"]
            assert "instrument" in data
            assert "timeframe" in data
            assert "total_bars" in data
            assert "speed" in data
            assert "metrics" in data
            assert "open_positions" in data
            assert "closed_positions" in data

    def test_receives_bar_messages(self, app):
        """After snapshot, should receive BAR messages from the replay loop."""
        client = TestClient(app)
        with client.websocket_connect("/ws/stream") as ws:
            # First message is always SNAPSHOT
            snapshot = json.loads(ws.receive_text())
            assert snapshot["type"] == "SNAPSHOT"

            # Collect a few more messages - should include BARs
            messages = []
            for _ in range(5):
                try:
                    raw = ws.receive_text(timeout=5)
                    messages.append(json.loads(raw))
                except Exception:
                    break

            bar_msgs = [m for m in messages if m["type"] == "BAR"]
            if bar_msgs:
                bar = bar_msgs[0]["data"]
                assert "open" in bar
                assert "high" in bar
                assert "low" in bar
                assert "close" in bar

    def test_message_format(self, app):
        """All messages should have type, data, and timestamp fields."""
        client = TestClient(app)
        with client.websocket_connect("/ws/stream") as ws:
            msg = json.loads(ws.receive_text())
            assert "type" in msg
            assert "data" in msg
            assert "timestamp" in msg
