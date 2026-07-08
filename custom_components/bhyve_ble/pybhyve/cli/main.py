"""b-hyve BLE CLI (`bhyve`). Usage: see pybhyve/README.md."""

from __future__ import annotations

import argparse
import asyncio
import secrets
import struct
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..ble_trace import BleTraceReporter, network_char_detail
from ..link_crypto import (
    build_aes_char_write_payload,
    build_data_frame,
    build_network_char_payload,
    derive_from_aes_char_exchange,
    parse_inbound_data_frame,
)
from ..constants import GEN1_HANDLES, GEN2_HANDLES, Generation
from ..constants import (
    AES_CHAR_UUID,
    DEFAULT_ATT_MTU,
    GEN1_ACK_DELAY_S,
    GEN1_RESPONSE_TIMEOUT_S,
    GEN1_STATUS_LISTEN_S,
    GEN1_STEP_DELAY_S,
    GEN1_TX_DELAY_MS,
    GEN1_WRITE_SETTLE_S,
    GEN2_STATUS_LISTEN_S,
    GEN2_TX_DELAY_MS,
    NETWORK_CHAR_UUID,
    NOTIFY_CHAR_UUID,
    WRITE_CHAR_UUID,
    format_device_id,
)
from ..gen1_ops import (
    Gen1PairingError,
    gen1_device_id,
    gen1_network_key,
    run_gen1_manual_start,
    run_gen1_onboard,
    run_gen1_status_session,
    run_gen1_stop_watering,
)
from ..gen1_session import Gen1Session, run_gen1_session
from ..gen1_codec import (
    GEN1_DEFAULT_TIMESTAMP_TAIL,
    gen1_mesh_attach_plaintexts,
    gen1_status_snapshot_verified,
    gen1_link_plaintext_acceptable,
)
from ..gen2_ops import (
    run_gen2_manual_start,
    run_gen2_status_queries,
    run_gen2_stop_watering,
)
from ..gen2_codec import (
    MANUAL_WATER_RUN_SEC_MAX,
    MANUAL_WATER_RUN_SEC_MIN,
    ingest_gen2_notify,
)
from .display import (
    brief_gen1_plaintext,
    brief_gen2_plaintext,
    format_gen1_verbose,
    format_gen2_protobuf_only,
    format_plaintext_readable,
    print_device_status_summary,
    print_gatt_plaintext,
)

SendPlaintextFn = Callable[[bytes, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ClientProfile:
    generation: Generation
    link_msg_type: int
    tx_delay_ms: int
    device_id: int | None
    gatt_write_handle: str
    gatt_notify_handle: str


@dataclass(frozen=True, slots=True)
class PostActionPlan:
    mode: str  # "start" | "stop" | "status" | "none"
    listen_seconds: float
    print_status: bool


def _bleak():
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as e:  # pragma: no cover
        raise SystemExit("Install bleak: uv sync") from e
    return BleakClient, BleakScanner


def _print_saved_credentials(
    *,
    generation: Generation,
    address: str,
    network_key: bytes,
    device_id: int | None,
) -> None:
    print("\n=== Save these credentials ===")
    print(f"  --address:    {address}")
    print(f"  --network-key {network_key.hex()}")
    if generation == "gen1" and device_id is not None:
        print(f"  --device-id {device_id}")
    print("===============================\n")


def _parse_start_seconds(s: str) -> int:
    try:
        n = int(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid duration: {s!r}") from e
    if n < MANUAL_WATER_RUN_SEC_MIN or n > MANUAL_WATER_RUN_SEC_MAX:
        raise argparse.ArgumentTypeError(
            f"--seconds must be between [{MANUAL_WATER_RUN_SEC_MIN}, {MANUAL_WATER_RUN_SEC_MAX}], got {n}"
        )
    return n


def _parse_positive_seconds(s: str) -> float:
    try:
        n = float(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid duration: {s!r}") from e
    if n <= 0:
        raise argparse.ArgumentTypeError("--seconds must be positive")
    return n


def _resolve_profile(args: argparse.Namespace) -> ClientProfile:
    if args.command == "pair":
        gen: Generation = "gen1" if args.gen == 1 else "gen2"
    else:
        gen = "gen1" if args.device_id is not None else "gen2"
    handles = GEN1_HANDLES if gen == "gen1" else GEN2_HANDLES
    device_id_val = args.device_id
    if gen == "gen1" and device_id_val is None and args.command != "pair":
        raise SystemExit("gen1 requires --device-id (e.g. 4321)")
    return ClientProfile(
        generation=gen,
        link_msg_type=handles.link_msg_type,
        tx_delay_ms=GEN1_TX_DELAY_MS if gen == "gen1" else GEN2_TX_DELAY_MS,
        device_id=device_id_val,
        gatt_write_handle=handles.write_char,
        gatt_notify_handle=handles.notify_char,
    )



def _apply_learned_gen1_device_id(
    profile: ClientProfile,
    gen1_session: Gen1Session,
    args: argparse.Namespace,
    *,
    connect: bool = False,
) -> ClientProfile:
    assigned = gen1_session.assigned_device_id
    if assigned is None:
        if connect:
            if not gen1_session.received_any_notify:
                # No inbound frames at all: the timer never processed our writes.
                raise SystemExit(
                    "The timer never acknowledged our writes. Check that it is:\n"
                    "  - in pairing mode (blinking blue),\n"
                    "  - within ~1 m of the Bluetooth adapter,\n"
                    "  - not already connected to any other app or integration,\n"
                    "then reset the timer and retry."
                )
        return profile
    if profile.device_id == assigned:
        return profile
    if profile.device_id is not None:
        print(
            f"Device assigned device-id {assigned} (0x{assigned:04x}); "
            f"onboard used {profile.device_id}.",
            file=sys.stderr,
        )
    args.device_id = assigned
    return ClientProfile(
        generation=profile.generation,
        link_msg_type=profile.link_msg_type,
        tx_delay_ms=profile.tx_delay_ms,
        device_id=assigned,
        gatt_write_handle=profile.gatt_write_handle,
        gatt_notify_handle=profile.gatt_notify_handle,
    )


def _resolve_post_action_plan(args: argparse.Namespace, profile: ClientProfile) -> PostActionPlan:
    cmd = getattr(args, "command", None)
    mode = "start" if cmd == "start" else "stop" if cmd == "stop" else "status" if cmd == "status" else "none"
    if cmd == "start":
        run_sec = float(args.seconds)
        if getattr(args, "foreground", False):
            return PostActionPlan(mode="start", listen_seconds=run_sec, print_status=False)
        return PostActionPlan(mode="start", listen_seconds=0.0, print_status=False)
    if mode in ("stop", "status"):
        wait = GEN2_STATUS_LISTEN_S if profile.generation == "gen2" else GEN1_STATUS_LISTEN_S
        return PostActionPlan(mode=mode, listen_seconds=wait, print_status=True)
    return PostActionPlan(mode="none", listen_seconds=0.0, print_status=False)


async def scan_only(timeout: float) -> None:
    _, BleakScanner = _bleak()
    print(f"Scanning {timeout:.0f}s for BLE devices...")
    try:
        adv_map = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except TypeError:
        adv_map = None
    if adv_map is not None:
        for _key, (dev, adv) in sorted(adv_map.items(), key=lambda kv: kv[1][0].address or ""):
            print(f"  {dev.address}  {dev.name!r}  RSSI={adv.rssi}")
        return
    found = await BleakScanner.discover(timeout=timeout)
    for d in sorted(found, key=lambda x: x.address):
        rssi = getattr(d, "rssi", None)
        print(f"  {d.address}  {d.name!r}  RSSI={rssi}")


async def ble_session(args: argparse.Namespace) -> None:
    BleakClient, _ = _bleak()
    address = args.address.upper()

    if args.command == "pair" and args.network_key is None:
        args.network_key = secrets.token_bytes(16)
        print(f"Generated network-key: {args.network_key.hex()}")

    if args.network_key is None:
        raise SystemExit("--network-key is required")

    profile = _resolve_profile(args)
    key: bytes = args.network_key
    pairing_mode = args.command == "pair"
    tail = GEN1_DEFAULT_TIMESTAMP_TAIL
    handles_profile = GEN1_HANDLES if profile.generation == "gen1" else GEN2_HANDLES
    trace: BleTraceReporter | None = (
        BleTraceReporter(address, handles_profile) if args.verbose >= 2 else None
    )

    post_action = _resolve_post_action_plan(args, profile)

    gen_label = 1 if profile.generation == "gen1" else 2
    print(f"Connecting to {address} (Gen {gen_label})")
    device_id: str | None = None
    if profile.generation == "gen1" and profile.device_id is not None:
        device_id = format_device_id(address, "gen1", profile.device_id)
    elif profile.generation == "gen2":
        device_id = format_device_id(address, "gen2")
    if device_id is not None:
        if profile.generation == "gen1":
            print(
                f"Device ID: {device_id} "
                f"(hex={struct.pack('<H', profile.device_id).hex()})"
            )
        else:
            print(f"Device ID: {device_id}")

    if (
        pairing_mode
        and profile.generation == "gen1"
        and profile.device_id is None
        and args.device_id is None
    ):
        generated = gen1_device_id()
        args.device_id = generated
        profile = ClientProfile(
            generation=profile.generation,
            link_msg_type=profile.link_msg_type,
            tx_delay_ms=profile.tx_delay_ms,
            device_id=generated,
            gatt_write_handle=profile.gatt_write_handle,
            gatt_notify_handle=profile.gatt_notify_handle,
        )
        print(
            f"Generated device ID: {generated} "
            f"(0x{struct.pack('<H', generated).hex()}) — device confirms in 0xc4 NOTIFY"
        )

    async with BleakClient(address) as client:
        if not client.is_connected:
            raise SystemExit("Not connected")

        if pairing_mode and profile.generation == "gen1":
            mesh_id = profile.device_id if profile.device_id is not None else args.device_id
            if mesh_id is None:
                raise SystemExit(
                    "Gen 1 pair requires --device-id (re-bind) or first-time auto-generation; "
                    "mesh id is not read from BLE adverts"
                )

        if hasattr(client, "exchange_mtu"):
            try:
                n = await client.exchange_mtu(DEFAULT_ATT_MTU)
                print(f"MTU exchange -> {n}")
            except Exception as ex:
                print(f"MTU exchange skipped: {ex}")

        if pairing_mode:
            if profile.generation == "gen1":
                prov = build_network_char_payload(key, profile.device_id)
            else:
                prov = build_network_char_payload(key)
            if trace:
                trace.write_req("network_char", prov, detail=network_char_detail(prov))
            elif args.verbose >= 1:
                print(
                    f"Write network_char ({len(prov)} B) prefix={prov[0:2].hex()}"
                    + (
                        f" (gen1 session mesh {profile.device_id})"
                        if profile.generation == "gen1" and profile.device_id is not None
                        else ""
                    )
                    + " ..."
                )
            try:
                await client.write_gatt_char(NETWORK_CHAR_UUID, prov, response=True)
            except Exception as ex:
                from bleak.exc import BleakGATTProtocolError, BleakGATTProtocolErrorCode

                if isinstance(ex, BleakGATTProtocolError) and ex.args and ex.args[0] in (
                    BleakGATTProtocolErrorCode.WRITE_NOT_PERMITTED,
                    BleakGATTProtocolErrorCode.INSUFFICIENT_AUTHORIZATION,
                ):
                    raise SystemExit(
                        "Unable to write to network_char, is the device already paired?"
                    ) from ex
                raise

        aes_w = build_aes_char_write_payload(profile.tx_delay_ms)
        if trace:
            trace.write_req("aes_char", aes_w, detail=f"tx_delay_ms={profile.tx_delay_ms}")
        elif args.verbose >= 1:
            print(f"Write aes_char ({len(aes_w)} B) tx_delay_ms={profile.tx_delay_ms} ...")
        await client.write_gatt_char(AES_CHAR_UUID, aes_w, response=True)

        aes_r: bytes | None = None
        for attempt in range(12):
            await asyncio.sleep(0.15 if attempt else 0.05)
            candidate = await client.read_gatt_char(AES_CHAR_UUID)
            if len(candidate) != 20:
                raise SystemExit(f"aes_char read expected 20 bytes, got {len(candidate)}")
            try:
                derive_from_aes_char_exchange(aes_w, candidate)
                aes_r = candidate
                break
            except ValueError as ex:
                if attempt >= 11:
                    raise SystemExit(f"aes_char read did not validate: {ex}") from ex

        assert aes_r is not None
        if trace:
            trace.read_rsp("aes_char", aes_r)
        d = derive_from_aes_char_exchange(aes_w, aes_r)
        print(f"Session Started: iv12={d.iv12.hex()} enc_ctr=0x{d.enc_ctr:08x} dec_ctr=0x{d.dec_ctr:08x}")

        state: dict[str, int] = {"enc": d.enc_ctr, "dec": d.dec_ctr}
        link_t = profile.link_msg_type
        gen2_status: dict[str, Any] = {}
        session_magic = (
            struct.pack("<H", profile.device_id)
            if profile.device_id is not None
            else b""
        )
        loop = asyncio.get_running_loop()
        gen1_session: Gen1Session | None = None
        log_notify_issues = pairing_mode and profile.generation == "gen1" and trace is None

        async def send_plaintext(pt: bytes, *, label: str) -> None:
            frame, state["enc"] = build_data_frame(
                link_t,
                pt,
                key16=key,
                iv12=d.iv12,
                enc_ctr=state["enc"],
            )
            ctr_extra = f"  enc→0x{state['enc']:08x}" if args.verbose >= 2 else ""
            if trace:
                if profile.generation == "gen1":
                    brief = brief_gen1_plaintext(pt, att_direction="write_f")
                else:
                    brief = brief_gen2_plaintext(pt)
                detail = f"{label}  ·  {brief}{ctr_extra}"
                trace.write_f("write_char", frame, plaintext=pt, detail=detail)
            elif args.verbose >= 1 or log_notify_issues:
                print_gatt_plaintext(
                    "WRITE",
                    profile.gatt_write_handle,
                    pt,
                    generation=profile.generation,
                    verbose=max(args.verbose, 1) if log_notify_issues else args.verbose,
                    step=label,
                    wire=frame if args.verbose >= 2 else None,
                    ctr_extra=ctr_extra,
                )
            await client.write_gatt_char(WRITE_CHAR_UUID, frame, response=True)
            if profile.generation == "gen1":
                await asyncio.sleep(GEN1_WRITE_SETTLE_S)
            if args.verbose >= 3 and profile.generation == "gen2":
                print(format_gen2_protobuf_only(pt))
            elif args.verbose >= 3 and profile.generation == "gen1":
                print(format_gen1_verbose(pt, args.verbose))

        if profile.generation == "gen1":
            gen1_session = Gen1Session(
                magic=session_magic,
                send_plaintext=lambda pt, label: send_plaintext(pt, label=label),
                step_delay_s=GEN1_STEP_DELAY_S,
                ack_delay_s=GEN1_ACK_DELAY_S,
                response_timeout_s=GEN1_RESPONSE_TIMEOUT_S,
            )

        async def ingest_notify(raw: bytes) -> None:
            nonlocal session_magic
            if len(raw) < 4:
                if trace:
                    trace.notify(raw, detail=f"short frame ({len(raw)} B)")
                elif log_notify_issues or args.verbose >= 2:
                    print(f"NOTIFY short ({len(raw)} B): {raw.hex()}", file=sys.stderr)
                return
            try:
                _T, pt, state["dec"], skipped = parse_inbound_data_frame(
                    raw,
                    key16=key,
                    iv12=d.iv12,
                    dec_ctr=state["dec"],
                    expected_magic=None,
                    accept_plaintext=(
                        (lambda p: gen1_link_plaintext_acceptable(p, magic=None))
                        if profile.generation == "gen1"
                        else None
                    ),
                )
            except ValueError as ex:
                if trace:
                    trace.notify(raw, detail=f"decrypt failed: {ex!r}")
                else:
                    print(f"NOTIFY decrypt failed: {ex!r}  raw={raw.hex()}", file=sys.stderr)
                return
            if skipped:
                resync = f"counter resync: skipped {skipped} step(s)"
                if not trace and (log_notify_issues or args.verbose >= 1):
                    print(f"  NOTIFY {resync}", file=sys.stderr)
            else:
                resync = ""
            if profile.generation == "gen1" and len(pt) >= 2:
                wire_magic = pt[0:2]
                if session_magic and wire_magic != session_magic:
                    learned = int.from_bytes(wire_magic, "little")
                    if trace:
                        pass  # shown in decoded plaintext prefix
                    else:
                        print(
                            f"  NOTIFY mesh magic {learned} (0x{learned:04x})",
                            file=sys.stderr,
                        )
                    session_magic = wire_magic
            ctr_extra = f"  dec→0x{state['dec']:08x}" if args.verbose >= 2 else ""
            if trace:
                if profile.generation == "gen1":
                    brief = brief_gen1_plaintext(pt, att_direction="notify")
                else:
                    brief = brief_gen2_plaintext(pt)
                detail = f"{resync}  ·  {brief}".strip(" ·") if resync else brief
                if ctr_extra:
                    detail = f"{detail}{ctr_extra}"
                trace.notify(raw, plaintext=pt, detail=detail)
            elif log_notify_issues or args.verbose >= 1:
                print_gatt_plaintext(
                    "NOTIFY",
                    profile.gatt_notify_handle,
                    pt,
                    generation=profile.generation,
                    verbose=max(args.verbose, 1) if log_notify_issues else args.verbose,
                    wire=raw if args.verbose >= 2 else None,
                    ctr_extra=ctr_extra,
                )
            if profile.generation == "gen2" and post_action.print_status:
                ingest_gen2_notify(pt, gen2_status)
            if profile.generation == "gen1" and gen1_session is not None:
                gen1_session.on_notify_plaintext(pt)
            if args.verbose >= 3 and profile.generation == "gen1":
                print(format_gen1_verbose(pt, args.verbose))
            elif args.verbose >= 3 and profile.generation == "gen2":
                print(format_plaintext_readable(pt))

        def on_notify(_handle: int, data: bytearray) -> None:
            raw = bytes(data)
            loop.call_soon_threadsafe(lambda r=raw: asyncio.create_task(ingest_notify(r)))

        await client.start_notify(NOTIFY_CHAR_UUID, on_notify)
        # Subscribed to notifications here
        if profile.generation == "gen1":
            await asyncio.sleep(GEN1_STEP_DELAY_S)

        if profile.generation == "gen1":
            assert gen1_session is not None
            assert profile.device_id is not None
            device_id = int(profile.device_id)
            try:
                if args.command == "pair":
                    await run_gen1_onboard(gen1_session, device_id, tail=tail)
                elif args.command == "stop":
                    await run_gen1_stop_watering(gen1_session, device_id, tail=tail)
                elif args.command == "start":
                    await run_gen1_manual_start(
                        gen1_session,
                        device_id,
                        int(args.seconds),
                        tail=tail,
                        reconnect=True,
                    )
                elif args.command == "status":
                    await run_gen1_status_session(gen1_session, device_id, tail=tail)
                elif args.command == "connect":
                    await run_gen1_session(
                        gen1_session,
                        gen1_mesh_attach_plaintexts(
                            device_id,
                            label_prefix="gen1 reconnect",
                            include_85_clear=True,
                            tail=tail,
                        ),
                    )
                    snapshot = gen1_session.status_snapshot
                    if not gen1_status_snapshot_verified(snapshot):
                        print(
                            "Warning: reconnect finished but no gen1 NOTIFYs decoded "
                            "(check --network-key and --device-id; try "
                            "`bhyve -a ADDR --gen 1 pair` to re-bind).",
                            file=sys.stderr,
                        )
            except Gen1PairingError as exc:
                raise SystemExit(str(exc)) from exc

            profile = _apply_learned_gen1_device_id(
                profile,
                gen1_session,
                args,
                connect=args.command == "pair",
            )

        # Gen 2 timer handling
        else:
            if args.command == "start":
                await run_gen2_manual_start(
                    send_plaintext,
                    int(args.seconds),
                    station_id=int(args.port) - 1,
                )
            if args.command == "stop":
                await run_gen2_stop_watering(send_plaintext)
            if args.command in ("status", "stop"):
                await run_gen2_status_queries(send_plaintext)

        if post_action.listen_seconds > 0:
            await asyncio.sleep(post_action.listen_seconds)
        if post_action.print_status:
            summary_station: int | None = None
            if args.command in ("start", "stop", "status"):
                summary_station = int(args.port) - 1
            print_device_status_summary(
                generation=profile.generation,
                gen1_snapshot=gen1_session.status_snapshot if gen1_session else {},
                gen2_store=gen2_status,
                station_id=summary_station,
            )
        await client.stop_notify(NOTIFY_CHAR_UUID)

    if pairing_mode:
        _print_saved_credentials(
            generation=profile.generation,
            address=address,
            network_key=key,
            device_id=profile.device_id,
        )

    print("Disconnected")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="b-hyve BLE client",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-a",
        "--address",
        required=True,
        metavar="MAC",
        help="Bluetooth address",
    )
    common.add_argument(
        "-k",
        "--network-key",
        type=gen1_network_key,
        default=None,
        help="16-byte network key: 32 hex chars or Base64",
    )
    common.add_argument(
        "-i",
        "--device-id",
        type=int,
        default=None,
        metavar="ID",
        help="Gen1 BLE device ID (decimal integer)",
    )
    common.add_argument(
        "-p",
        "--port",
        type=int,
        choices=(1, 2, 3, 4),
        default=1,
        metavar="PORT",
        help="Valve port for multi-output timers",
    )
    common.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help=(
            "Increase log detail"
        ),
    )
    sub = p.add_subparsers(dest="command")
    scan = sub.add_parser(
        "scan",
        help="Listen for BLE advertisements.",
    )
    scan.add_argument(
        "-s",
        "--seconds",
        type=_parse_positive_seconds,
        default=10.0,
        metavar="SEC",
        help="Scan duration in seconds (default: 10)",
    )
    pair = sub.add_parser(
        "pair",
        parents=[common],
        help="Initial provisioning with timer in pairing mode.",
    )
    pair.add_argument(
        "--gen",
        type=int,
        choices=(1, 2),
        required=True,
        metavar="N",
        help="Timer generation: 1 (BH1G1) or 2 (HT25 Orbit protobuf)",
    )
    start = sub.add_parser("start", parents=[common], help="Start manual watering")
    start.add_argument(
        "-s",
        "--seconds",
        type=_parse_start_seconds,
        required=True,
        metavar="SEC",
        help=f"Run duration in seconds ({MANUAL_WATER_RUN_SEC_MIN}..{MANUAL_WATER_RUN_SEC_MAX})",
    )
    start.add_argument(
        "-f",
        "--foreground",
        action="store_true",
        help="Keep the session open for the given watering interval",
    )
    stop = sub.add_parser("stop", parents=[common], help="Stop manual watering")
    status = sub.add_parser("status", parents=[common], help="Read device status")
    connect = sub.add_parser(
        "connect",
        parents=[common],
        help="Connection test (handshake only)",
    )
    return p


def main() -> None:
    p = build_parser()
    args = p.parse_args()
    if args.command == "scan":
        asyncio.run(scan_only(float(args.seconds)))
        return
    if args.command is None:
        p.error("subcommand required: connect, pair, start, stop, status, or scan")
    asyncio.run(ble_session(args))


if __name__ == "__main__":
    main()
