"""
How SDK push-event names map onto HA entities (real-time, not the slow poll).

The bridge forwards semantic events (motion / personDetected / doorbellPress / …) from
the SDK's always-on push channel onto the HA bus as `<DOMAIN>_event`. These maps decide
which become auto-off binary_sensors vs. event-entity fires, and gate each on the
device's capabilities so a device only gets entities for events it can emit.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

# Push events surfaced as auto-off binary_sensors.
# bus event name -> (key, friendly name, device_class, required capability)
PUSH_BINARY_SENSORS: dict[str, tuple[str, str, BinarySensorDeviceClass, str]] = {
    "motion": ("motion", "Motion", BinarySensorDeviceClass.MOTION, "motion"),
    "personDetected": (
        "person",
        "Person",
        BinarySensorDeviceClass.OCCUPANCY,
        "person_detection",
    ),
}

# Push events surfaced on a per-device "Detection" event entity. This is a CATCH-ALL: it
# fires on whatever eufy reported — person, pet, vehicle, dog, … — so the Detection
# entity's timeline matches what advanced "Last event". `motion` and `personDetected`
# are ALSO auto-off binary_sensors above (PUSH_BINARY_SENSORS); listing them here too is
# intentional — the binary_sensor is the current on/off state, the event entity is the
# discrete "something was detected" occurrence.
# bus event name -> HA event_type
DETECTION_EVENTS: dict[str, str] = {
    "motion": "motion",
    "personDetected": "person",
    "petDetection": "pet",
    "dogDetected": "dog",
    "vehicleDetected": "vehicle",
    "strangerDetected": "stranger",
    "soundDetected": "sound",
    "cryingDetected": "crying",
    "packageDelivered": "package_delivered",
    "packageTaken": "package_taken",
    "packageStranded": "package_stranded",
}

# The Motion binary_sensor is an UMBRELLA: AI cameras/doorbells classify their motion
# as person/vehicle/pet/… and may never emit a bare "motion", so any of these VISUAL
# detections flips Motion on (and HA's motion.detected trigger with it). Excludes
# sound/crying (audio) and package (a state), which aren't movement.
MOTION_EVENTS: frozenset[str] = frozenset(
    {
        "motion",
        "personDetected",
        "vehicleDetected",
        "petDetection",
        "dogDetected",
        "strangerDetected",
    }
)

# A doorbell press is its own event entity (device_class DOORBELL).
DOORBELL_EVENT = "doorbellPress"
DOORBELL_EVENT_TYPE = "pressed"

# Capabilities that make a device eligible for a Detection event entity.
DETECTION_CAPABILITIES = frozenset({"motion", "person_detection", "doorbell"})

# A bridge-side nudge (not a device push): the bridge emits it after it has pulled a
# fresh event cover from local or cloud storage and the bytes changed, so the image
# entity re-fetches only after an image really exists. A raw detection is too early:
# local-storage accounts have no push thumbnail and write their crop asynchronously.
EVENT_IMAGE_REFRESH = "eventImageUpdated"

# Push carries no "cleared" signal, so a push binary_sensor auto-offs after this delay.
PUSH_AUTO_OFF_SECONDS = 30
