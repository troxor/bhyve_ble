from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_GENERATION,
    CONF_DEVICE_NETWORK_KEY_B64,
    CONF_DEVICES,
    CONF_MESH_DEVICE_ID,
    CONF_NETWORK_KEY_B64,
    CONF_NETWORK_KEY_INPUT,
    CONF_POLL_INTERVAL_HOURS,
    DEFAULT_POLL_INTERVAL_HOURS,
    DOMAIN,
    GENERATION_GEN1,
    GENERATION_GEN2,
    MAX_POLL_INTERVAL_HOURS,
    MIN_POLL_INTERVAL_HOURS,
    normalize_ble_address,
)
from .device_profile import GENERATION_CHOICES, DeviceBleProfile, device_ble_profile
from .entry_data import entry_needs_network_key_prompt, parse_gen1_credentials_submission
from .gen1_discovery import async_discover_gen1_mesh_device_id
from .network_key import parse_or_generate_network_key

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult

_LOGGER = logging.getLogger(__name__)

_CTX_ADDRESS = "add_device_address"
_CTX_GENERATION = "add_device_generation"
_CTX_NETWORK_KEY_RAW = "network_key_raw"
_CTX_GEN1_DEVICE_KEY_RAW = "gen1_device_key_raw"


def _gen1_credentials_schema(discovered: int | None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_MESH_DEVICE_ID,
                default=str(discovered) if discovered is not None else "",
            ): str,
            vol.Optional(CONF_NETWORK_KEY_INPUT, default=""): str,
        }
    )


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


def _address_picker_schema(hass: HomeAssistant) -> vol.Schema:
    discovered = _connectable_ble_device_labels(hass)
    if discovered:
        return vol.Schema(
            {
                vol.Required(CONF_ADDRESS): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": addr, "label": label} for addr, label in discovered.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
            }
        )
    return vol.Schema({vol.Required(CONF_ADDRESS): str})


def _network_key_schema() -> vol.Schema:
    return vol.Schema({vol.Optional(CONF_NETWORK_KEY_INPUT, default=""): str})


@dataclass(frozen=True, slots=True)
class Gen1SetupRequest:
    address: str
    device_key: bytes
    mesh_id: int
    profile: DeviceBleProfile
    user_supplied_key: bool


async def _async_run_gen1_setup(hass: HomeAssistant, request: Gen1SetupRequest) -> None:
    from .onboarding import async_onboard_gen1_device, async_verify_gen1_device_communication

    if request.user_supplied_key:
        await async_verify_gen1_device_communication(
            hass,
            request.address,
            request.device_key,
            request.mesh_id,
            ble_profile=request.profile,
        )
        return
    await async_onboard_gen1_device(
        hass,
        request.address,
        request.device_key,
        request.mesh_id,
        ble_profile=request.profile,
    )


async def _async_run_gen2_onboard(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    profile: DeviceBleProfile,
) -> None:
    from .ble import BleProvisionOptions, async_provision_with_network_key
    from .onboarding import async_verify_device_communication

    _LOGGER.debug(
        "Onboarding %s (gen2): provision + verify tx_delay_ms=%s link_type=0x%02x",
        address,
        profile.tx_delay_ms,
        profile.link_msg_type,
    )
    await async_provision_with_network_key(
        hass,
        address,
        network_key_16,
        BleProvisionOptions(tx_delay_ms=profile.tx_delay_ms),
    )
    await async_verify_device_communication(
        hass,
        address,
        network_key_16,
        ble_profile=profile,
    )


class BhyveBleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def _entry_data(self) -> dict[str, Any]:
        return {}

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """First integration setup: pick the timer from BLE scan, then generation and optional key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = normalize_ble_address(user_input[CONF_ADDRESS].strip())
            self.context[_CTX_ADDRESS] = address
            return await self.async_step_device_generation()

        return self.async_show_form(
            step_id="user",
            data_schema=_address_picker_schema(self.hass),
            errors=errors,
        )

    async def async_step_device_generation(self, user_input: dict | None = None) -> FlowResult:
        from .ble import BhyveBleProvisionError
        from .onboarding import BhyveOnboardingError

        errors: dict[str, str] = {}
        address = self.context.get(_CTX_ADDRESS)
        if not address:
            return await self.async_step_user()

        if user_input is not None:
            generation = user_input[CONF_DEVICE_GENERATION]
            self.context[_CTX_GENERATION] = generation
            profile = device_ble_profile(generation)

            if generation == GENERATION_GEN1:
                return await self.async_step_gen1_mesh()
            if entry_needs_network_key_prompt(self._entry_data()):
                return await self.async_step_network_key()
            try:
                return await self._async_complete_gen2_onboard(address, profile)
            except BhyveBleProvisionError as e:
                _LOGGER.warning("Provision failed for %s: %s", address, e)
                errors["base"] = "cannot_connect"
            except BhyveOnboardingError as e:
                _LOGGER.warning("Onboarding verify failed for %s: %s", address, e)
                errors["base"] = "verify_failed"

        return self.async_show_form(
            step_id="device_generation",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_GENERATION): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": value, "label": label}
                                for value, label in GENERATION_CHOICES
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_network_key(self, user_input: dict | None = None) -> FlowResult:
        from .ble import BhyveBleProvisionError
        from .onboarding import BhyveOnboardingError

        errors: dict[str, str] = {}
        address = self.context.get(_CTX_ADDRESS)
        if not address:
            return await self.async_step_user()
        if self.context.get(_CTX_GENERATION) != GENERATION_GEN2:
            return await self.async_step_device_generation()

        profile = device_ble_profile(GENERATION_GEN2)

        if user_input is not None:
            try:
                raw_key = parse_or_generate_network_key(user_input.get(CONF_NETWORK_KEY_INPUT))
            except ValueError as e:
                errors["base"] = "invalid_key"
                _LOGGER.warning("Invalid network key input: %s", e)
            else:
                self.context[_CTX_NETWORK_KEY_RAW] = raw_key
                try:
                    return await self._async_complete_gen2_onboard(address, profile)
                except BhyveBleProvisionError as e:
                    _LOGGER.warning("Provision failed for %s: %s", address, e)
                    errors["base"] = "cannot_connect"
                except BhyveOnboardingError as e:
                    _LOGGER.warning("Onboarding verify failed for %s: %s", address, e)
                    errors["base"] = "verify_failed"

        return self.async_show_form(
            step_id="network_key",
            data_schema=_network_key_schema(),
            errors=errors,
        )

    async def async_step_gen1_mesh(self, user_input: dict | None = None) -> FlowResult:
        from .ble import BhyveBleProvisionError
        from .onboarding import BhyveOnboardingError

        errors: dict[str, str] = {}
        address = self.context.get(_CTX_ADDRESS)
        if not address:
            return await self.async_step_user()

        profile = device_ble_profile(GENERATION_GEN1)

        if user_input is not None:
            mesh_id, device_key, parse_errors = parse_gen1_credentials_submission(user_input)
            errors.update(parse_errors)
            if mesh_id is not None and not errors:
                if device_key is not None:
                    self.context[_CTX_GEN1_DEVICE_KEY_RAW] = device_key
                try:
                    return await self._async_complete_gen1_onboard(address, mesh_id, profile)
                except BhyveBleProvisionError as e:
                    _LOGGER.warning("Gen1 provision failed for %s: %s", address, e)
                    errors["base"] = "cannot_connect"
                except BhyveOnboardingError as e:
                    _LOGGER.warning("Gen1 onboard failed for %s: %s", address, e)
                    errors["base"] = "verify_failed"

        discovered = await async_discover_gen1_mesh_device_id(self.hass, address)
        return self.async_show_form(
            step_id="gen1_mesh",
            data_schema=_gen1_credentials_schema(discovered),
            errors=errors,
            description_placeholders={
                "address": address,
                "discovered": str(discovered) if discovered is not None else "not found",
            },
        )

    async def _async_complete_gen2_onboard(
        self,
        address: str,
        profile: DeviceBleProfile,
    ) -> FlowResult:
        raw_key: bytes = self.context.get(_CTX_NETWORK_KEY_RAW) or secrets.token_bytes(16)
        await _async_run_gen2_onboard(self.hass, address, raw_key, profile)
        await self._async_set_unique_id_from_key(raw_key)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Orbit B-hyve",
            data={
                CONF_NETWORK_KEY_B64: base64.b64encode(raw_key).decode("ascii"),
                CONF_DEVICES: {address: {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
            },
        )

    async def _async_complete_gen1_onboard(
        self,
        address: str,
        mesh_id: int,
        profile: DeviceBleProfile,
    ) -> FlowResult:
        device_key: bytes = self.context.get(_CTX_GEN1_DEVICE_KEY_RAW) or secrets.token_bytes(16)
        user_supplied_key = _CTX_GEN1_DEVICE_KEY_RAW in self.context
        entry_key = secrets.token_bytes(16)
        _LOGGER.debug(
            "Gen1 setup %s mesh_id=%s (%s)",
            address,
            mesh_id,
            "verify existing credentials" if user_supplied_key else "first-time onboard",
        )
        await _async_run_gen1_setup(
            self.hass,
            Gen1SetupRequest(
                address=address,
                device_key=device_key,
                mesh_id=mesh_id,
                profile=profile,
                user_supplied_key=user_supplied_key,
            ),
        )
        await self._async_set_unique_id_from_key(entry_key)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Orbit B-hyve",
            data={
                CONF_NETWORK_KEY_B64: base64.b64encode(entry_key).decode("ascii"),
                CONF_DEVICES: {
                    address: {
                        CONF_DEVICE_GENERATION: GENERATION_GEN1,
                        CONF_MESH_DEVICE_ID: mesh_id,
                        CONF_DEVICE_NETWORK_KEY_B64: base64.b64encode(device_key).decode("ascii"),
                    }
                },
            },
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

    def _entry_data(self) -> dict[str, Any]:
        return dict(self.config_entry.data)

    def _resolve_gen2_network_key(self) -> bytes:
        raw: bytes | None = self.context.get(_CTX_NETWORK_KEY_RAW)
        if raw is not None:
            return raw
        b64 = self.config_entry.data.get(CONF_NETWORK_KEY_B64)
        if b64:
            return base64.b64decode(b64)
        return secrets.token_bytes(16)

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_device", "poll_interval"],
        )

    async def async_step_poll_interval(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        current = float(
            self.config_entry.options.get(CONF_POLL_INTERVAL_HOURS, DEFAULT_POLL_INTERVAL_HOURS)
        )
        if user_input is not None:
            try:
                hours = float(user_input[CONF_POLL_INTERVAL_HOURS])
            except TypeError, ValueError:
                errors["base"] = "invalid_interval"
            else:
                hours = max(MIN_POLL_INTERVAL_HOURS, min(hours, MAX_POLL_INTERVAL_HOURS))
                return self.async_create_entry(
                    title="",
                    data={CONF_POLL_INTERVAL_HOURS: hours},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_POLL_INTERVAL_HOURS, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL_HOURS,
                        max=MAX_POLL_INTERVAL_HOURS,
                        step=0.25,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="h",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="poll_interval",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_add_device(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            address = normalize_ble_address(user_input[CONF_ADDRESS].strip())
            existing = {
                normalize_ble_address(a) for a in (self.config_entry.data.get(CONF_DEVICES) or {})
            }
            if address in existing:
                errors["base"] = "already_configured"
            else:
                self.context[_CTX_ADDRESS] = address
                return await self.async_step_device_generation()

        return self.async_show_form(
            step_id="add_device",
            data_schema=_address_picker_schema(self.hass),
            errors=errors,
        )

    async def async_step_device_generation(self, user_input: dict | None = None) -> FlowResult:
        from .ble import BhyveBleProvisionError
        from .onboarding import BhyveOnboardingError

        errors: dict[str, str] = {}
        address = self.context.get(_CTX_ADDRESS)
        if not address:
            return await self.async_step_add_device()

        if user_input is not None:
            generation = user_input[CONF_DEVICE_GENERATION]
            self.context[_CTX_GENERATION] = generation
            profile = device_ble_profile(generation)

            if generation == GENERATION_GEN1:
                return await self.async_step_gen1_mesh()
            if entry_needs_network_key_prompt(self._entry_data()):
                return await self.async_step_network_key()
            try:
                return await self._async_complete_gen2_onboard(address, generation, profile)
            except BhyveBleProvisionError as e:
                _LOGGER.warning("Provision failed for %s: %s", address, e)
                errors["base"] = "cannot_connect"
            except BhyveOnboardingError as e:
                _LOGGER.warning("Onboarding verify failed for %s: %s", address, e)
                errors["base"] = "verify_failed"

        return self.async_show_form(
            step_id="device_generation",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_GENERATION): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": value, "label": label}
                                for value, label in GENERATION_CHOICES
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_network_key(self, user_input: dict | None = None) -> FlowResult:
        from .ble import BhyveBleProvisionError
        from .onboarding import BhyveOnboardingError

        errors: dict[str, str] = {}
        address = self.context.get(_CTX_ADDRESS)
        generation = self.context.get(_CTX_GENERATION, GENERATION_GEN2)
        if not address:
            return await self.async_step_add_device()
        if generation != GENERATION_GEN2:
            return await self.async_step_device_generation()

        profile = device_ble_profile(GENERATION_GEN2)

        if user_input is not None:
            try:
                raw_key = parse_or_generate_network_key(user_input.get(CONF_NETWORK_KEY_INPUT))
            except ValueError as e:
                errors["base"] = "invalid_key"
                _LOGGER.warning("Invalid network key input: %s", e)
            else:
                self.context[_CTX_NETWORK_KEY_RAW] = raw_key
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        CONF_NETWORK_KEY_B64: base64.b64encode(raw_key).decode("ascii"),
                    },
                )
                try:
                    return await self._async_complete_gen2_onboard(address, generation, profile)
                except BhyveBleProvisionError as e:
                    _LOGGER.warning("Provision failed for %s: %s", address, e)
                    errors["base"] = "cannot_connect"
                except BhyveOnboardingError as e:
                    _LOGGER.warning("Onboarding verify failed for %s: %s", address, e)
                    errors["base"] = "verify_failed"

        return self.async_show_form(
            step_id="network_key",
            data_schema=_network_key_schema(),
            errors=errors,
        )

    async def async_step_gen1_mesh(self, user_input: dict | None = None) -> FlowResult:
        from .ble import BhyveBleProvisionError
        from .onboarding import BhyveOnboardingError

        errors: dict[str, str] = {}
        address = self.context.get(_CTX_ADDRESS)
        if not address:
            return await self.async_step_add_device()

        profile = device_ble_profile(GENERATION_GEN1)

        if user_input is not None:
            mesh_id, device_key, parse_errors = parse_gen1_credentials_submission(user_input)
            errors.update(parse_errors)
            if mesh_id is not None and not errors:
                if device_key is not None:
                    self.context[_CTX_GEN1_DEVICE_KEY_RAW] = device_key
                try:
                    return await self._async_complete_gen1_onboard(address, mesh_id, profile)
                except BhyveBleProvisionError as e:
                    _LOGGER.warning("Gen1 provision failed for %s: %s", address, e)
                    errors["base"] = "cannot_connect"
                except BhyveOnboardingError as e:
                    _LOGGER.warning("Gen1 onboard failed for %s: %s", address, e)
                    errors["base"] = "verify_failed"

        discovered = await async_discover_gen1_mesh_device_id(self.hass, address)
        return self.async_show_form(
            step_id="gen1_mesh",
            data_schema=_gen1_credentials_schema(discovered),
            errors=errors,
            description_placeholders={
                "address": address,
                "discovered": str(discovered) if discovered is not None else "not found",
            },
        )

    async def _async_complete_gen2_onboard(
        self,
        address: str,
        generation: str,
        profile: DeviceBleProfile,
    ) -> FlowResult:
        key = self._resolve_gen2_network_key()
        await _async_run_gen2_onboard(self.hass, address, key, profile)
        devices = dict(self.config_entry.data.get(CONF_DEVICES) or {})
        devices[address] = {CONF_DEVICE_GENERATION: generation}
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_DEVICES: devices},
        )
        return self.async_abort(reason="device_added")

    async def _async_complete_gen1_onboard(
        self,
        address: str,
        mesh_id: int,
        profile: DeviceBleProfile,
    ) -> FlowResult:
        device_key: bytes = self.context.get(_CTX_GEN1_DEVICE_KEY_RAW) or secrets.token_bytes(16)
        user_supplied_key = _CTX_GEN1_DEVICE_KEY_RAW in self.context
        _LOGGER.debug(
            "Gen1 setup %s mesh_id=%s (%s)",
            address,
            mesh_id,
            "verify existing credentials" if user_supplied_key else "first-time onboard",
        )
        await _async_run_gen1_setup(
            self.hass,
            Gen1SetupRequest(
                address=address,
                device_key=device_key,
                mesh_id=mesh_id,
                profile=profile,
                user_supplied_key=user_supplied_key,
            ),
        )

        devices = dict(self.config_entry.data.get(CONF_DEVICES) or {})
        devices[address] = {
            CONF_DEVICE_GENERATION: GENERATION_GEN1,
            CONF_MESH_DEVICE_ID: mesh_id,
            CONF_DEVICE_NETWORK_KEY_B64: base64.b64encode(device_key).decode("ascii"),
        }
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_DEVICES: devices},
        )
        return self.async_abort(reason="device_added")
