# Orbit B-hyve for Home Assistant

This Integration allows Home Assistant to locally control Orbit B-hyve hose timers through Home Assistant. Currently, not all features from the official app are implemented aside from the basics.

This project is **unofficial** and not endorsed by Orbit. It exists for local control of B-hyve devices you own. It may be useful in areas with no internet connectivity, and/or when you cannot use the official Android or iOS apps.

Use at your own risk. The author is not responsible for bricked hardware, astronomical water bills, unhappy soaked pets, or any other undesirable outcome!

## Installation

Copy to **`config/custom_components/bhyve_ble/`** and restart Home Assistant.

## Configuring Devices

1. **Add Integration.** Sets a **shared network key** for establishing a connection to the hose timers. This is equivalent to your B-hyve account in the official app.
2. **Configure → Add timer** Put it in **pairing mode** by pressing the "b" hex button 5 times quickly, pick the BLE address (or type it), with an optional friendly name.


## Usage

Once added, Hose timers will expose a switch control for each detected Output Port to start and stop watering. The default max runtime is 10 minutes unless switched off before then.

Device Info, Battery, and other information from the devices will also be populated.

## Development

### Run Tests

Use a Python environment where **Home Assistant is installed** so `import homeassistant` works (for example `pip install homeassistant` in a venv). Then:

```bash
cd /path/to/bhyve_ble
PYTHONPATH=.. pytest -q tests
```

