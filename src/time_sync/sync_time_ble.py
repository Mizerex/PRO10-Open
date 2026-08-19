from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from bleak import BleakClient

from sync_time import build_time_packet, decode_time_packet, format_hex


ADDRESS = "AA:BB:CC:DD:EE:FF"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9f"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9f"
ACK_HEADER = 0xFD
ACK_COMMAND_SETTING = 0x02
ACK_KEY_SYSTEM_TIME = 0x01
ACK_SUCCESS = 0x01
ACK_TIMEOUT_SECONDS = 10.0


def is_time_ack(data: bytes) -> bool:
    return (
        len(data) >= 9
        and data[0] == ACK_HEADER
        and data[4] == ACK_COMMAND_SETTING
        and data[5] == ACK_KEY_SYSTEM_TIME
    )


def ack_is_success(data: bytes) -> bool:
    return is_time_ack(data) and data[8] == ACK_SUCCESS


async def send_current_time() -> None:
    ack_event = asyncio.Event()
    ack_packet: bytes | None = None

    def notification_handler(_sender, data: bytearray) -> None:
        nonlocal ack_packet
        received = bytes(data)
        print("Notificação recebida:", format_hex(received))
        if is_time_ack(received):
            ack_packet = received
            ack_event.set()

    async with BleakClient(ADDRESS) as client:
        if not client.is_connected:
            raise RuntimeError("Não foi possível conectar ao PRO 10.")

        print("Conectado ao PRO 10.")
        await client.start_notify(NOTIFY_UUID, notification_handler)
        print("Canal de ACK ativado:", NOTIFY_UUID)

        current_time = datetime.now().replace(microsecond=0)
        packet = build_time_packet(current_time)
        if decode_time_packet(packet) != current_time:
            raise RuntimeError("A validação local do pacote falhou.")

        print("Data/hora:", current_time.strftime("%d/%m/%Y %H:%M:%S"))
        print("Pacote:", format_hex(packet))
        await client.write_gatt_char(WRITE_UUID, packet, response=False)
        print("Pacote enviado; aguardando ACK...")

        try:
            await asyncio.wait_for(ack_event.wait(), timeout=ACK_TIMEOUT_SECONDS)
        except TimeoutError as error:
            raise RuntimeError("O PRO 10 não respondeu com ACK em 10 segundos.") from error
        finally:
            await client.stop_notify(NOTIFY_UUID)

        if ack_packet is None or not ack_is_success(ack_packet):
            status = "ausente" if ack_packet is None else f"0x{ack_packet[8]:02X}"
            raise RuntimeError(f"ACK recebido sem confirmação de sucesso: {status}.")

        print("ACK de sincronização confirmado com status 0x01.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza a hora do PRO 10 usando o protocolo do Olywear."
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="autoriza conectar e enviar o pacote ao relógio",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.send:
        current_time = datetime.now().replace(microsecond=0)
        packet = build_time_packet(current_time)
        print("Modo seguro: nenhum dado será enviado por Bluetooth.")
        print("Data/hora:", current_time.strftime("%d/%m/%Y %H:%M:%S"))
        print("Pacote:", format_hex(packet))
        print("Use --send somente após a revisão final.")
        return

    asyncio.run(send_current_time())


if __name__ == "__main__":
    main()
