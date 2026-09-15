"""Unit tests for bridge event state handling."""

# Ruff's pytest-style rules otherwise reject unittest assertions.
# ruff: noqa: D102, PT009

from __future__ import annotations

from unittest import TestCase

from custom_components.eufy_sdk.coordinator import EufySdkDataUpdateCoordinator


class CoordinatorEventTests(TestCase):
    """Pin immediate propertyChanged updates without a network refresh."""

    def test_property_changed_replaces_only_the_target_state(self) -> None:
        coordinator = object.__new__(EufySdkDataUpdateCoordinator)
        original = {
            "CAM1": {"sn": "CAM1", "state": {"motion": False, "battery": 74}},
            "CAM2": {"sn": "CAM2", "state": {"motion": False}},
        }
        coordinator.data = original
        published: list[dict[str, dict]] = []
        coordinator.async_set_updated_data = published.append  # type: ignore[method-assign]

        changed = coordinator.apply_property_changed(
            {
                "event": "propertyChanged",
                "deviceSn": "CAM1",
                "property": "motion",
                "value": True,
            }
        )

        self.assertTrue(changed)
        self.assertEqual(
            published,
            [
                {
                    "CAM1": {
                        "sn": "CAM1",
                        "state": {"motion": True, "battery": 74},
                    },
                    "CAM2": {"sn": "CAM2", "state": {"motion": False}},
                }
            ],
        )
        self.assertIsNot(published[0], original)
        self.assertFalse(original["CAM1"]["state"]["motion"])

    def test_property_changed_ignores_unknown_devices(self) -> None:
        coordinator = object.__new__(EufySdkDataUpdateCoordinator)
        coordinator.data = {}
        published: list[dict[str, dict]] = []
        coordinator.async_set_updated_data = published.append  # type: ignore[method-assign]

        changed = coordinator.apply_property_changed(
            {"deviceSn": "MISSING", "property": "motion", "value": True}
        )

        self.assertFalse(changed)
        self.assertEqual(published, [])
