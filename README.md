[![HACS Validation](https://github.com/troxor/bhyve_ble/actions/workflows/validate.yml/badge.svg)](https://github.com/troxor/bhyve_ble/actions/workflows/validate.yml)
[![Hassfest Validation](https://github.com/troxor/bhyve_ble/actions/workflows/hassfest.yml/badge.svg)](https://github.com/troxor/bhyve_ble/actions/workflows/hassfest.yml)
[![Lint](https://github.com/troxor/bhyve_ble/actions/workflows/lint.yml/badge.svg)](https://github.com/troxor/bhyve_ble/actions/workflows/lint.yml)
[![CodeQL Advanced](https://github.com/troxor/bhyve_ble/actions/workflows/codeql.yml/badge.svg)](https://github.com/troxor/bhyve_ble/actions/workflows/codeql.yml)

# Orbit B-hyve for Home Assistant

![bhyve_ble-logo](custom_components/bhyve_ble/brand/icon.png)

This integration lets Home Assistant control Orbit B-hyve hose timers locally over Bluetooth Low Energy. Only a subset of official-app features is implemented.

This project is **unofficial** and not endorsed by Orbit. It's intended for local control of B-hyve devices you own. It may be useful when you have little or no internet connectivity, or when you cannot use the official Android or iOS apps.

Use at your own risk. The author is not responsible for bricked hardware, high water bills, unhappy soaked pets, or any other undesirable outcome.

## Installation

### HACS (recommended)

#### Quickstart

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=troxor&repository=bhyve_ble)

#### Manually

1. Open [HACS](https://hacs.xyz/) in Home Assistant → **Integrations**.
2. Open the menu (⋮) → **Custom repositories**.
3. Add this repository URL, category **Integration**, then **Add**.
4. Search for **Orbit B-hyve** (or this repo name), open it, and **Download**.
1. Restart Home Assistant to load the integration.

### Manual Installation (not recommended)

1. Copy the folder `custom_components/bhyve_ble/` from this repository into **`config/custom_components/`** directory (so you have `config/custom_components/bhyve_ble/manifest.json`)
1. Restart Home Assistant to load the integration.

## Configuration

1. In Home Assistant navigate to **Settings → Devices & services → Add integration** and choose **Orbit B-hyve**.
1. Click the `+ Add Integration` button.
1. Search for `B-hyve`.
1. If you cannot find `Orbit B-hyve` in the list then be sure to clear your browser cache and/or perform a hard-refresh of the page.
1. **Network key** step: set a unique identifier used to talk to your hose timers. This is analogous to your B-hyve account in the official app.
1. **Configure** — Put the timer in **pairing mode** (press the “b” hex button five times quickly), choose the BLE address from the dropdown


## Usage

Each hose timer exposes a **switch** per output port to start and stop watering. The default maximum runtime is ten minutes unless you turn it off earlier.

Device info, battery, and related sensors are filled in when the device reports them.

## Development

### Run tests

Use a Python environment where **Home Assistant is installed** so `import homeassistant` works (for example `pip install homeassistant` in a venv). From the **repository root**:

```bash
pytest -q tests
```

`pytest.ini` sets `pythonpath = custom_components` so `bhyve_ble` resolves like it does under Home Assistant.
