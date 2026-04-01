from __future__ import annotations

import base64
import hashlib
import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DEVICES,
    CONF_NETWORK_KEY_B64,
    CONF_NETWORK_KEY_INPUT,
    DOMAIN,
    normalize_ble_address,
)
from .network_key import parse_or_generate_network_key

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult

_LOGGER = logging.getLogger(__name__)


def _connectable_ble_device_labels(hass: HomeAssistant) -> dict[str, str]:
    """
    Labels for all connectable BLE devices in HA's Bluetooth cache (newest discovery).

    Likely Orbit B-hyve timers (GATT UUIDs or name) are sorted first; remainder follow by label.
    """
    from homeassistant.components.bluetooth import async_discovered_service_info

    from .bluetooth import is_bhyve_timer

    rows: list[tuple[bool, str, str]] = []
    seen: set[str] = set()
    for info in async_discovered_service_info(hass, connectable=True):
        if info.address in seen:
            continue
        seen.add(info.address)
        preferred = is_bhyve_timer(info) or (info.name and "b-hyve" in info.name.lower())
        label = f"{info.name or 'Unknown'} ({info.address})"
        rows.append((preferred, info.address, label))
    rows.sort(key=lambda x: (not x[0], x[2].casefold()))
    return {addr: lab for _, addr, lab in rows}


class BhyveBleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """If devices are already registered with the official app, we can reuse the network key here for interoperability. Otherwise, just generate a random key to serve as our "account"."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                raw_key = parse_or_generate_network_key(user_input.get(CONF_NETWORK_KEY_INPUT))
            except ValueError as e:
                errors["base"] = "invalid_key"
                _LOGGER.warning("Invalid network key input: %s", e)
            else:
                await self._async_set_unique_id_from_key(raw_key)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Orbit B-hyve",
                    data={
                        CONF_NETWORK_KEY_B64: base64.b64encode(raw_key).decode("ascii"),
                        CONF_DEVICES: {},
                    },
                )

        schema = vol.Schema(
            {
                vol.Optional(CONF_NETWORK_KEY_INPUT, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def _async_set_unique_id_from_key(self, raw_key: bytes) -> None:
        digest = hashlib.sha256(raw_key).hexdigest()
        await self.async_set_unique_id(digest)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Do not pass config_entry into the flow; handler is the entry id (see OptionsFlow.config_entry)."""
        return BhyveBleOptionsFlow()


class BhyveBleOptionsFlow(config_entries.OptionsFlow):
    """Device onboarding: Pairing mode -> GATT handshake -> confirm with valid device traffic."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        return await self.async_step_add_device()

    async def async_step_add_device(self, user_input: dict | None = None) -> FlowResult:
        from .ble import BhyveBleProvisionError, async_provision_with_network_key
        from .onboarding import BhyveOnboardingError, async_verify_device_communication

        errors: dict[str, str] = {}
        discovered = _connectable_ble_device_labels(self.hass)

        if user_input is not None:
            address = normalize_ble_address(user_input[CONF_ADDRESS].strip())
            key = base64.b64decode(self.config_entry.data[CONF_NETWORK_KEY_B64])

            existing = {
                normalize_ble_address(a) for a in (self.config_entry.data.get(CONF_DEVICES) or {})
            }
            if address in existing:
                errors["base"] = "already_configured"
            else:
                try:
                    await async_provision_with_network_key(self.hass, address, key)
                    await async_verify_device_communication(self.hass, address, key)
                except BhyveBleProvisionError as e:
                    _LOGGER.warning("Provision failed for %s: %s", address, e)
                    errors["base"] = "cannot_connect"
                except BhyveOnboardingError as e:
                    _LOGGER.warning("Onboarding verify failed for %s: %s", address, e)
                    errors["base"] = "verify_failed"
                else:
                    devices = dict(self.config_entry.data.get(CONF_DEVICES) or {})
                    devices[address] = {}
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={**self.config_entry.data, CONF_DEVICES: devices},
                    )
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                    return self.async_abort(reason="device_added")

        if discovered:
            schema = vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": addr, "label": label}
                                for addr, label in discovered.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                            custom_value=True,
                        )
                    ),
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): str,
                }
            )

        return self.async_show_form(
            step_id="add_device",
            data_schema=schema,
            errors=errors,
        )
