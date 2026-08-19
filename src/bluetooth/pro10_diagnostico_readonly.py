import asyncio
from datetime import datetime
from pathlib import Path
from bleak import BleakClient, BleakScanner

ADDRESS = "AA:BB:CC:DD:EE:FF"
OUT = Path(__file__).resolve().parent / "pro10_diagnostico_readonly.txt"

BATTERY_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

DEVICE_INFO = {
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision",
}

def add(lines, text=""):
    print(text)
    lines.append(text)

def decode_value(uuid, data):
    if not data:
        return "<vazio>"

    if uuid.lower() == BATTERY_UUID:
        return f"{data[0]}%"

    if uuid.lower() in DEVICE_INFO:
        try:
            return data.decode("utf-8", errors="replace").strip("\x00")
        except Exception:
            pass

    hex_value = data.hex(" ").upper()

    try:
        text = data.decode("utf-8").strip("\x00")
        if text and all(c.isprintable() for c in text):
            return f'HEX={hex_value} | TEXT="{text}"'
    except Exception:
        pass

    return f"HEX={hex_value}"

async def main():
    lines = []

    add(lines, "=" * 72)
    add(lines, "PRO10 - DIAGNOSTICO BLE SOMENTE LEITURA")
    add(lines, f"Data: {datetime.now().isoformat(timespec='seconds')}")
    add(lines, f"Endereco alvo: {ADDRESS}")
    add(lines, "")
    add(lines, "IMPORTANTE: este programa NAO executa write_gatt_char.")
    add(lines, "Nenhuma configuracao sera enviada ao relogio.")
    add(lines, "=" * 72)

    add(lines, "\n[1] Procurando PRO 10...")

    device = None

    for attempt in range(1, 4):
        add(lines, f"Scan {attempt}/3...")
        try:
            devices = await BleakScanner.discover(timeout=8.0)
            for d in devices:
                if d.address.upper() == ADDRESS.upper():
                    device = d
                    break
        except Exception as exc:
            add(lines, f"Erro no scan: {type(exc).__name__}: {exc}")

        if device:
            break

    if not device:
        add(lines, "")
        add(lines, "PRO 10 nao encontrado.")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    add(lines, f"Encontrado: {device.address} | {device.name}")

    add(lines, "\n[2] Conectando...")

    try:
        async with BleakClient(device, timeout=20.0) as client:
            add(lines, f"Conectado: {client.is_connected}")

            services = client.services

            add(lines, "")
            add(lines, "=" * 72)
            add(lines, "SERVICOS E CARACTERISTICAS")
            add(lines, "=" * 72)

            readable = []

            for service in services:
                add(lines, "")
                add(lines, f"SERVICE: {service.uuid}")
                add(lines, f"Descricao: {service.description}")

                for char in service.characteristics:
                    props = ", ".join(char.properties)

                    add(
                        lines,
                        f"  CHAR: {char.uuid} | [{props}] | {char.description}"
                    )

                    if "read" in char.properties:
                        readable.append(char)

                    for desc in char.descriptors:
                        add(
                            lines,
                            f"      DESC: handle={desc.handle} uuid={desc.uuid}"
                        )

            add(lines, "")
            add(lines, "=" * 72)
            add(lines, "LEITURA DAS CARACTERISTICAS QUE DECLARAM READ")
            add(lines, "=" * 72)

            for char in readable:
                uuid = char.uuid.lower()

                try:
                    value = await client.read_gatt_char(char)
                    decoded = decode_value(uuid, bytes(value))

                    label = DEVICE_INFO.get(uuid, "")

                    if uuid == BATTERY_UUID:
                        label = "Battery Level"

                    if label:
                        add(lines, f"{uuid} | {label}: {decoded}")
                    else:
                        add(lines, f"{uuid}: {decoded}")

                except Exception as exc:
                    add(
                        lines,
                        f"{uuid}: LEITURA NAO DISPONIVEL "
                        f"({type(exc).__name__}: {exc})"
                    )

            add(lines, "")
            add(lines, "=" * 72)
            add(lines, "RESUMO")
            add(lines, "=" * 72)

            try:
                value = await client.read_gatt_char(BATTERY_UUID)
                if value:
                    add(lines, f"Bateria informada diretamente pelo PRO 10: {value[0]}%")
            except Exception as exc:
                add(lines, f"Bateria: falha na leitura: {exc}")

            add(lines, "")
            add(lines, "Diagnostico encerrado sem enviar comandos de escrita.")

    except Exception as exc:
        add(lines, "")
        add(lines, f"ERRO DE CONEXAO: {type(exc).__name__}: {exc}")

    OUT.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("=" * 72)
    print(f"Relatorio salvo em:")
    print(OUT)
    print("=" * 72)

asyncio.run(main())
