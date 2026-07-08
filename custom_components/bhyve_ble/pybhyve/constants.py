from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Generation = Literal["gen1", "gen2"]

VALID_TIMER_PORT_COUNTS: frozenset[int] = frozenset({1, 2, 4})
MAX_TIMER_PORTS = 4
MAX_TIMER_STATION_ID = MAX_TIMER_PORTS - 1

# Manual watering duration bounds (seconds).
MANUAL_WATER_RUN_SEC_MIN = 15
MANUAL_WATER_RUN_SEC_MAX = 4 * 3600


def normalize_ble_address(address: str) -> str:
    """Normalize BLE MAC for storage and comparison."""
    return address.upper().replace("-", ":")


def format_device_id(
    address: str,
    generation: Generation,
    device_id: int | None = None,
) -> str:
    """
    Orbit app device id for display only (not used on the gen2 BLE wire).

    Gen1: numeric BLE / device id. Gen2: lowercased last three MAC octets.
    """
    if generation == "gen1":
        if device_id is None:
            msg = "gen1 format_device_id requires device_id"
            raise ValueError(msg)
        return str(int(device_id))
    mac = normalize_ble_address(address).replace(":", "")
    suffix = mac[-6:] if len(mac) >= 6 else mac
    return suffix.lower()


# Orbit B-hyve hose timer GATT characteristic UUIDs (shared by gen1 and gen2).
NETWORK_CHAR_UUID = "00006c76-fe32-4f58-8b78-98e42b2c047f"
AES_CHAR_UUID = "00006c71-fe32-4f58-8b78-98e42b2c047f"
WRITE_CHAR_UUID = "00006c72-fe32-4f58-8b78-98e42b2c047f"
NOTIFY_CHAR_UUID = "00006c73-fe32-4f58-8b78-98e42b2c047f"
READ_CHAR_UUID = NOTIFY_CHAR_UUID


# GATT handle layouts (ATT handle strings from pairing captures).
@dataclass(frozen=True, slots=True)
class PairingHandleProfile:
    generation: Generation
    network_char: str
    aes_char: str
    write_char: str
    notify_char: str

    @property
    def link_msg_type(self) -> int:
        return 0x10 if self.generation == "gen1" else 0x11


GEN1_HANDLES = PairingHandleProfile(
    generation="gen1",
    network_char="0x0010",
    aes_char="0x0009",
    write_char="0x000b",
    notify_char="0x000d",
)

GEN2_HANDLES = PairingHandleProfile(
    generation="gen2",
    network_char="0x0014",
    aes_char="0x000d",
    write_char="0x000f",
    notify_char="0x0011",
)

# AES-char init tx-delay (written during handshake).
GEN1_TX_DELAY_MS = 100
GEN2_TX_DELAY_MS = 0

DEFAULT_ATT_MTU = 247

# Post-write pause after gen1 GATT write-without-response.
GEN1_WRITE_SETTLE_S = 0.20

# NOTIFY listen window after status/stop (gen1 shorter than gen2).
GEN1_STATUS_LISTEN_S = 0.5
GEN2_STATUS_LISTEN_S = 1.0

# Pause between gen2 status query writes (getDeviceStatusInfo / getDeviceInfo / battery).
GEN2_STATUS_QUERY_DELAY_S = 0.35

# Listen windows for start/stop/command actions.
COMMAND_LISTEN_S = 1.0
START_CONFIRM_LISTEN_S = 3.0

# Gap between sequential status query frames.
STATUS_QUERY_GAP_S = 0.2

# Gen1 scripted session pacing (ACK cadence, step gaps, per-write response wait).
GEN1_STEP_DELAY_S = 0.10
GEN1_ACK_DELAY_S = 0.02
GEN1_RESPONSE_TIMEOUT_S = 8.0

# Hose-timer pack mV -> percent (linear map shared by gen1 and gen2).
BATTERY_MV_EMPTY = 2400
BATTERY_MV_FULL = 3000


def mv_to_percent_linear(mv: int, mv_empty: int, mv_full: int) -> int:
    """Clamp mV to [empty, full], linear scale to 0-100, truncate toward zero."""
    low = min(mv_empty, mv_full)
    high = max(mv_empty, mv_full)
    if high <= low:
        return 0
    clamped = max(low, min(mv, high))
    return int((clamped - low) * 100 / (high - low))


def estimate_battery_percent_from_mv(mv: int) -> int:
    """Map pack millivolts to 0-100 when the device omits batteryLevelPercent."""
    return mv_to_percent_linear(mv, BATTERY_MV_EMPTY, BATTERY_MV_FULL)
