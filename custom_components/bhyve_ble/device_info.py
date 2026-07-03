from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import DOMAIN
from .device_model import device_generation_label, device_model_name


def _bt_address(address: str) -> str:
    return address.upper().replace("-", ":")


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    s = str(value).strip()
    return s or None


def build_ha_device_info_from_gen2(
    *,
    address: str,
    name: str,
    device_info: dict | None,
    generation: str | None = None,
) -> DeviceInfo:
    """Build registry DeviceInfo from decoded deviceInfo protobuf fields."""
    conn = {(CONNECTION_BLUETOOTH, _bt_address(address))}
    gen_label = device_generation_label(generation)
    model = device_model_name(None, generation=generation)

    if not device_info:
        info: DeviceInfo = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections=conn,
            name=name,
            manufacturer="Orbit",
            model=model,
        )
        if gen_label:
            info["model_id"] = gen_label
        return info

    hw = _str_or_none(device_info.get("hwVersion"))
    fw = _str_or_none(device_info.get("fwVersion"))

    model = device_model_name(hw, generation=generation)

    info = DeviceInfo(
        identifiers={(DOMAIN, address)},
        connections=conn,
        name=name,
        manufacturer="Orbit",
        model=model,
    )
    if gen_label:
        info["model_id"] = gen_label

    if fw:
        info["sw_version"] = fw

    hw_parts: list[str] = []
    dtype = device_info.get("deviceType")
    if dtype is not None:
        hw_parts.append(f"Type: {dtype}")
    pb = device_info.get("powerBoardId")
    if pb is not None:
        hw_parts.append(f"Power board: {pb}")
    bl = device_info.get("bootloaderVersion")
    if bl is not None:
        hw_parts.append(f"Bootloader: {bl}")
    ble_bl = device_info.get("bleBootloaderVersion")
    if ble_bl is not None:
        hw_parts.append(f"BLE bootloader: {ble_bl}")
    ble_app = device_info.get("bleAppVersion")
    if ble_app is not None:
        hw_parts.append(f"BLE app: {ble_app}")
    ble_sdk = device_info.get("bleSdkVersion")
    if ble_sdk is not None:
        hw_parts.append(f"BLE SDK: {ble_sdk}")
    rl78 = device_info.get("rl78Version")
    if rl78 is not None:
        hw_parts.append(f"RL78: {rl78}")
    ble_st = device_info.get("bleStatus")
    if ble_st is not None:
        hw_parts.append(f"BLE: {ble_st}")
    wifi_v = device_info.get("wifiVersion")
    if wifi_v is not None:
        hw_parts.append(f"WiFi: {wifi_v}")

    if hw_parts:
        joined = " · ".join(hw_parts)
        if len(joined) > 250:
            joined = joined[:247] + "…"
        info["hw_version"] = joined

    return info
