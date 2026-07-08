# pybhyve

BLE library and `bhyve` CLI for Orbit B-hyve hose timers (Gen 1 and Gen 2).

Bundled inside the Home Assistant integration at `custom_components/bhyve_ble/pybhyve/`.

## Development

```bash
cd custom_components/bhyve_ble
uv sync
uv run bhyve --help
```

From the repository root:

```bash
uv run --project custom_components/bhyve_ble bhyve --help
```

Integration tests run from the repo root (`pytest -q`); see the top-level README.

## CLI usage

Put the **subcommand first**; flags (`-a`, `-k`, etc.) belong on that subcommand.

Gen1 is selected when `-i` / `--device-id` is set; otherwise Gen2.

### Scan

```bash
uv run bhyve scan
uv run bhyve scan -s 30
```

### Pair (first-time binding)

Put the timer in pairing mode. A network key is generated when `-k` is omitted.

```bash
uv run bhyve pair -a 44:67:55:AA:BB:CC --gen 1
uv run bhyve pair -a 44:67:55:DD:EE:FF --gen 2
```

### Start / stop / status

Decode a Base64 network key to hex:

```bash
echo -n 'ASNFZ4mrze8BI0VniavN7w==' | base64 -d | xxd -p -c 256
# -> 0123456789abcdef0123456789abcdef
```

```bash
uv run bhyve start -a 44:67:55:AA:BB:CC -i 4321 -k <hex-or-base64> -s 60

uv run bhyve start -a 44:67:55:DD:EE:FF -k <hex-or-base64> -s 120

uv run bhyve start -a 44:67:55:DD:EE:FF -k <hex-or-base64> -s 120 -f

uv run bhyve status -a 44:67:55:DD:EE:FF -k <hex-or-base64> -p 1

uv run bhyve stop -a 44:67:55:DD:EE:FF -k <hex-or-base64>
```

`-s` / `--seconds` — scan duration on `scan`; run duration on `start` (required).

`-f` / `--foreground` on `start` — keep the BLE session open for the run duration (default: send start and disconnect).

`-p` / `--port` — valve port 1–4 (Gen2 only).

### Connect (handshake test)

```bash
uv run bhyve connect -a 44:67:55:AA:BB:CC -i 4321 -k <hex-or-base64>

uv run bhyve connect -a 44:67:55:DD:EE:FF -k <hex-or-base64>
```

### Common flags

| Short | Long | Purpose |
|-------|------|---------|
| `-a` | `--address` | Bluetooth MAC (required) |
| `-k` | `--network-key` | 16-byte key (32 hex chars or Orbit Base64) |
| `-i` | `--device-id` | Gen1 device ID (decimal) |
| `-p` | `--port` | Gen2 valve port 1–4 |
| `-v` | `--verbose` | `-v` / `-vv` / `-vvv` for more decode detail |
