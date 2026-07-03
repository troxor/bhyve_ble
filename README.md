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

1. Copy the folder `custom_components/bhyve_ble/` from this repository into your **`config/custom_components/`** directory (so you have `config/custom_components/bhyve_ble/manifest.json`). The BLE library (`pybhyve`) ships inside that folder — no separate pip install is required.
1. Restart Home Assistant to load the integration.

## Configuration

1. In Home Assistant navigate to **Settings → Devices & services → Add integration** and choose **Orbit B-hyve**.
1. Click the `+ Add Integration` button.
1. Search for `B-hyve`.
1. If you cannot find `Orbit B-hyve` in the list then be sure to clear your browser cache and/or perform a hard-refresh of the page.
1. Put the timer in **pairing mode** (press the “b” hex button five times quickly) and choose its **Bluetooth address** from the dropdown.
1. Select hardware generation.
1. Provide existing secrets or allow integration to generate new values.


## Usage

Each hose timer exposes, per output port:

- **Status**:  read-only state (`off`, `watering`, `delay`, or `fault`); defaults to `off`. Fault details appear in attributes when the timer reports them.
- **Run time**: seconds for the next manual run. Valid values range from 15 to 14400 (4 hours).
- **Switch**: turn on runs manual watering for the configured run time; turn off stops all ports.

Device info, battery, and related sensors are filled in when the device reports them.

## Development

### Pre-commit hooks

Catch syntax, JSON, and lint issues before commit (Ruff matches the **Lint** GitHub workflow; `check-json` catches invalid `strings.json` / `translations/*.json`).

**prek** (reads `.pre-commit-config.yaml`):

```bash
prek install          # git hooks
prek run -a           # lint entire repo (not just staged files)
```

### Run tests

Use a Python environment where **Home Assistant is installed** so `import homeassistant` works (for example `pip install homeassistant` in a venv). From the **repository root**:

```bash
pytest -q tests
```

### pybhyve (library and CLI)

BLE protocol code and the standalone `bhyve` CLI live in [`custom_components/bhyve_ble/pybhyve/`](custom_components/bhyve_ble/pybhyve/). From the repository root:

```bash
uv sync
uv run bhyve --help
uv run pytest -q
```

