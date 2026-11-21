"""Constants for the walkingpad integration."""

from enum import Enum, unique
from typing import Final, TypedDict

# Re-export BeltState and ProtocolType from the library so that all
# integration modules can continue importing them from .const.
from walkingpad_controller import BeltState, ProtocolType  # noqa: F401

DOMAIN = "king_smith"


CONF_REMOTE_CONTROL: Final = "remote_control"
CONF_REMOTE_CONTROL_ENABLED: Final = "remote_control_enabled"
CONF_MAC: Final = "mac"
CONF_NAME: Final = "name"
CONF_PREFERRED_MODE: Final = "preferred_mode"


@unique
class WalkingPadMode(Enum):
    """An enumeration of the possible WalkingPad modes."""

    AUTO = 0
    MANUAL = 1
    STANDBY = 2


PREFERRED_MODE_MANUAL: Final = WalkingPadMode.MANUAL.name.lower()
DEFAULT_PREFERRED_MODE: Final = PREFERRED_MODE_MANUAL
PREFERRED_MODE_OPTIONS: Final = [
    WalkingPadMode.AUTO.name.lower(),
    WalkingPadMode.MANUAL.name.lower(),
]


class WalkingPadStatus(TypedDict):
    """A type to represent the state of the WalkingPad at a specific time."""

    belt_state: BeltState
    speed: float  # speed in km/h
    mode: WalkingPadMode
    session_running_time: int  # in seconds
    session_distance: int  # distance in meters
    session_steps: int
    session_calories: int  # total energy in kcal (FTMS only)
    session_calories_per_hour: int  # energy rate in kcal/h (FTMS only)
    heart_rate: int  # bpm; 0 when no HR sensor is paired with the treadmill
    training_status: int  # FTMS Training Status (0x2AD3) code; 0 if unknown
    last_fm_event: int  # opcode of most recent FM Status (0x2ADA) event
    vibration_level: int  # vibration intensity 0-4 (Sperax/WLT6200 only)
    incline: int  # incline level 0-10 (Sperax/WLT6200 only)
    status_timestamp: float


# Fitness Machine Status (0x2ADA) opcodes — most-recent event.
FTMS_FM_EVENT_NAMES: Final = {
    0x00: "none",
    0x01: "reset",
    0x02: "stopped_or_paused",
    0x03: "stopped_by_safety_key",
    0x04: "started_or_resumed",
    0x05: "target_speed_changed",
    0x06: "target_inclination_changed",
    0x07: "target_resistance_changed",
    0x08: "target_power_changed",
    0x09: "target_heart_rate_changed",
    0x0A: "target_expended_energy_changed",
    0x0B: "target_time_changed",
    0x14: "spin_down_status",
    0x15: "target_cadence_changed",
    0xFF: "control_permission_lost",
}
