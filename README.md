# Orbit B-hyve for Home Assistant

This integration lets Home Assistant control Orbit B-hyve hose timers locally over Bluetooth Low Energy. Only a subset of official-app features is implemented.

This project is **unofficial** and not endorsed by Orbit. It exists for local control of B-hyve devices you own. It may be useful when you have little or no internet connectivity, or when you cannot use the official Android or iOS apps.

Use at your own risk. The author is not responsible for bricked hardware, high water bills, unhappy soaked pets, or any other undesirable outcome.

## Installation

### HACS (recommended)

1. Open [HACS](https://hacs.xyz/) in Home Assistant → **Integrations**.
2. Open the menu (⋮) → **Custom repositories**.
3. Add this repository URL, category **Integration**, then **Add**.
4. Search for **Orbit B-hyve BLE** (or this repo name), open it, and **Download**.
5. Restart Home Assistant.
6. **Settings → Devices & services → Add integration** and choose **Orbit B-hyve**.

See also [HACS general requirements](https://hacs.xyz/docs/publish/start) and [integration layout](https://hacs.xyz/docs/publish/integration). For the default HACS store, your GitHub repo also needs a short **description** and **topics** (for example `home-assistant`, `integration`, `bhyve`, `ble`).

**Add to HACS (My link):** generate a one-click link with [My Home Assistant → HACS repository](https://my.home-assistant.io/create-link/?redirect=hacs_repository).

### Manual

Copy the folder `custom_components/bhyve_ble/` from this repository into your Home Assistant **`config/custom_components/`** directory (so you have `config/custom_components/bhyve_ble/manifest.json`), then restart Home Assistant.

## Configuring devices

1. **Add integration** — sets a **shared network key** used to talk to your hose timers (analogous to your B-hyve account in the official app).
2. **Configure → Add timer** — put the timer in **pairing mode** (press the “b” hex button five times quickly), pick the BLE address (or enter it), and optionally set a friendly name.

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
