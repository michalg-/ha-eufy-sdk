"""Regression tests for the bridge WebSocket client."""

# These are stdlib unittest cases so the integration needs no extra test runner.
# Ruff's pytest-style rules otherwise reject unittest assertions and exception helpers.
# ruff: noqa: D102, EM101, PT009, PT027, TRY003

from __future__ import annotations

from typing import Any
from unittest import IsolatedAsyncioTestCase

from custom_components.eufy_sdk.api import (
    EufySdkApiClient,
    EufySdkApiClientCommunicationError,
)


class _Session:
    """Minimal ClientSession stand-in."""

    def __init__(self) -> None:
        self.connect_calls = 0

    async def ws_connect(self, *_args: Any, **_kwargs: Any) -> Any:
        self.connect_calls += 1
        raise AssertionError("a closed client must not open a websocket")


class _SendFailureWebSocket:
    """Open-looking socket that drops exactly while sending."""

    closed = False

    async def send_json(self, _message: dict[str, Any]) -> None:
        raise OSError("socket disappeared")


class _ReplyWebSocket:
    """Socket that immediately completes the matching pending RPC."""

    closed = False

    def __init__(self, client: EufySdkApiClient) -> None:
        self.client = client

    async def send_json(self, message: dict[str, Any]) -> None:
        self.client._pending[message["id"]].set_result(  # noqa: SLF001
            {"id": message["id"], "ok": True, "value": 7}
        )


class _DeviceListWebSocket:
    """Socket that records the request and returns an empty device list."""

    closed = False

    def __init__(self, client: EufySdkApiClient) -> None:
        self.client = client
        self.sent: dict[str, Any] | None = None

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent = message
        self.client._pending[message["id"]].set_result(  # noqa: SLF001
            {"id": message["id"], "ok": True, "devices": []}
        )


class EufySdkApiClientTests(IsolatedAsyncioTestCase):
    """Pin failure handling around the RPC/reconnect boundary."""

    async def test_send_failure_is_wrapped_and_does_not_leak_pending_rpc(self) -> None:
        client = EufySdkApiClient("bridge", 3015, _Session())  # type: ignore[arg-type]
        client._ws = _SendFailureWebSocket()  # type: ignore[assignment]  # noqa: SLF001

        with self.assertRaises(EufySdkApiClientCommunicationError):
            await client.rpc("devices.list")

        self.assertEqual(client._pending, {})  # noqa: SLF001

    async def test_successful_rpc_also_cleans_pending_entry(self) -> None:
        client = EufySdkApiClient("bridge", 3015, _Session())  # type: ignore[arg-type]
        client._ws = _ReplyWebSocket(client)  # type: ignore[assignment]  # noqa: SLF001

        reply = await client.rpc("test")

        self.assertEqual(reply["value"], 7)
        self.assertEqual(client._pending, {})  # noqa: SLF001

    async def test_close_is_terminal_for_inflight_reconnect(self) -> None:
        session = _Session()
        client = EufySdkApiClient("bridge", 3015, session)  # type: ignore[arg-type]
        await client.close()

        with self.assertRaises(EufySdkApiClientCommunicationError):
            await client.connect()

        self.assertEqual(session.connect_calls, 0)

    async def test_device_list_can_request_a_fresh_bridge_snapshot(self) -> None:
        client = EufySdkApiClient("bridge", 3015, _Session())  # type: ignore[arg-type]
        websocket = _DeviceListWebSocket(client)
        client._ws = websocket  # type: ignore[assignment]  # noqa: SLF001

        self.assertEqual(await client.list_devices(refresh=True), [])
        self.assertEqual(
            websocket.sent,
            {"id": 1, "cmd": "devices.list", "refresh": True},
        )
