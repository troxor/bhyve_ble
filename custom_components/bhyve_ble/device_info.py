"""Maps Orbit protobuf ``deviceInfo`` (decoded JSON) to Home Assistant ``DeviceInfo``."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import DOMAIN


def _bt_address(address: str) -> str:
    return address.upper().replace("-", ":")


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    s = str(value).strip()
    return s or None


def build_ha_device_info_from_orbit(
    *,
    address: str,
    name: str,
    orbit: dict | None,
) -> DeviceInfo:
    """Build registry ``DeviceInfo`` from decoded ``OrbitPbApi_DeviceInfo`` fields."""
    conn = {(CONNECTION_BLUETOOTH, _bt_address(address))}

    if not orbit:
        return DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections=conn,
            name=name,
            manufacturer="Orbit",
            model="Orbit B-hyve",
        )

    hw = _str_or_none(orbit.get("hwVersion"))
    fw = _str_or_none(orbit.get("fwVersion"))

    model = hw or "Unknown Model"

    info: DeviceInfo = DeviceInfo(
        identifiers={(DOMAIN, address)},
        connections=conn,
        name=name,
        manufacturer="Orbit",
        model=model,
    )

    if fw:
        info["sw_version"] = fw

    if hw:
        info["model_id"] = hw

    hw_parts: list[str] = []
    dtype = orbit.get("deviceType")
    if dtype is not None:
        hw_parts.append(f"Type: {dtype}")
    pb = orbit.get("powerBoardId")
    if pb is not None:
        hw_parts.append(f"Power board: {pb}")
    bl = orbit.get("bootloaderVersion")
    if bl is not None:
        hw_parts.append(f"Bootloader: {bl}")
    ble_bl = orbit.get("bleBootloaderVersion")
    if ble_bl is not None:
        hw_parts.append(f"BLE bootloader: {ble_bl}")
    ble_app = orbit.get("bleAppVersion")
    if ble_app is not None:
        hw_parts.append(f"BLE app: {ble_app}")
    ble_sdk = orbit.get("bleSdkVersion")
    if ble_sdk is not None:
        hw_parts.append(f"BLE SDK: {ble_sdk}")
    rl78 = orbit.get("rl78Version")
    if rl78 is not None:
        hw_parts.append(f"RL78: {rl78}")
    ble_st = orbit.get("bleStatus")
    if ble_st is not None:
        hw_parts.append(f"BLE: {ble_st}")
    wifi_v = orbit.get("wifiVersion")
    if wifi_v is not None:
        hw_parts.append(f"WiFi: {wifi_v}")

    if hw_parts:
        joined = " · ".join(hw_parts)
        if len(joined) > 250:
            joined = joined[:247] + "…"
        info["hw_version"] = joined

    return info
