"""Inventário GATT somente leitura para a pesquisa PRO10 File Explorer."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner


SAFE_READ_UUIDS = {
    "00002a19-0000-1000-8000-00805f9b34fb": "battery_level",
    "00002a25-0000-1000-8000-00805f9b34fb": "serial_number",
    "00002a26-0000-1000-8000-00805f9b34fb": "firmware_revision",
    "00002a27-0000-1000-8000-00805f9b34fb": "hardware_revision",
    "00002a28-0000-1000-8000-00805f9b34fb": "software_revision",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventaria o GATT do PRO 10 sem executar operações de escrita."
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--address", help="Endereço BLE do relógio")
    target.add_argument("--name", default="PRO 10", help="Nome BLE para procurar")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("pro10_gatt_inventory.json"),
        help="Arquivo JSON de saída",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args()


def decode_known_value(uuid: str, value: bytes) -> object:
    if uuid.lower() == "00002a19-0000-1000-8000-00805f9b34fb" and value:
        return {"percent": value[0]}
    return {
        "text": value.decode("utf-8", errors="replace").strip("\x00"),
        "hex": value.hex(" ").upper(),
    }


async def find_target(address: str | None, name: str, timeout: float):
    if address:
        return await BleakScanner.find_device_by_address(address, timeout=timeout)
    return await BleakScanner.find_device_by_name(name, timeout=timeout)


async def inventory(args: argparse.Namespace) -> dict:
    device = await find_target(args.address, args.name, args.timeout)
    if device is None:
        raise RuntimeError("PRO 10 não encontrado. Confirme o Bluetooth e o alvo informado.")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "device": {"name": device.name, "address": "<redacted>"},
        "mode": "read-only",
        "services": [],
        "safe_reads": {},
    }

    async with BleakClient(device) as client:
        for service in client.services:
            service_item = {
                "uuid": str(service.uuid),
                "description": service.description,
                "characteristics": [],
            }
            for characteristic in service.characteristics:
                characteristic_item = {
                    "uuid": str(characteristic.uuid),
                    "description": characteristic.description,
                    "properties": sorted(characteristic.properties),
                    "descriptors": [
                        {"uuid": str(item.uuid), "handle": item.handle}
                        for item in characteristic.descriptors
                    ],
                }
                service_item["characteristics"].append(characteristic_item)

                uuid = str(characteristic.uuid).lower()
                if uuid in SAFE_READ_UUIDS and "read" in characteristic.properties:
                    try:
                        value = bytes(await client.read_gatt_char(characteristic))
                        report["safe_reads"][SAFE_READ_UUIDS[uuid]] = decode_known_value(
                            uuid, value
                        )
                    except Exception as exc:
                        report["safe_reads"][SAFE_READ_UUIDS[uuid]] = {
                            "error": type(exc).__name__
                        }
            report["services"].append(service_item)

    return report


async def main() -> None:
    args = parse_args()
    report = await inventory(args)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Inventário somente leitura salvo em: {args.output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
