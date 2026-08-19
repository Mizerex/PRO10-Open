# PRO10 - Diagnostico final do clima
# Uma unica conexao, um unico envio, sem loop de reconexao.
# Mede o tempo entre TX -> ACK e espera 20 s depois do clima atual
# para ajudar a separar "recebeu o pacote" de "atualizou a tela".

import asyncio
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime

from bleak import BleakScanner, BleakClient

DEVICE_NAME = "PRO 10"
UART_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9f"
UART_WRITE  = "6e400002-b5a3-f393-e0a9-e50e24dcca9f"

BATTERY_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
HARDWARE_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
SOFTWARE_UUID = "00002a28-0000-1000-8000-00805f9b34fb"

PROTOCOL_VERSION = 0x10
CMD_SETTING = 0x02
CMD_FEATURE = 0x19
KEY_TIME = 0x01
KEY_WEATHER_CURRENT = 0x1D
KEY_WEATHER_7 = 0x23

CITY = "São Paulo"
LAT = -23.5505
LON = -46.6333

ack_waiters = {}
t_current_ack = None


def stamp():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def frame(cmd, key, payload=b""):
    payload = bytes(payload)
    n = len(payload)
    base = bytes([
        0xDF,
        ((n + 5) >> 8) & 0xFF,
        (n + 5) & 0xFF,
        cmd & 0xFF,
        PROTOCOL_VERSION,
        key & 0xFF,
        (n >> 8) & 0xFF,
        n & 0xFF
    ]) + payload
    checksum = sum(base) & 0xFF
    return base[:3] + bytes([checksum]) + base[3:]


def get_weather():
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": 7,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "PRO10-DIAG/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def watch_weather_code(wmo):
    wmo = int(wmo)
    if wmo == 0: return 0
    if wmo == 1: return 1
    if wmo == 2: return 2
    if wmo == 3: return 3
    if wmo in (45,48): return 8
    if wmo in (51,53,55,56,57,80,81,82): return 4
    if wmo in (61,63,65,66,67): return 5
    if wmo in (71,73,75,77,85,86): return 7
    if wmo in (95,96,99): return 6
    return 0


def time_payload(now):
    y = now.year - 2000
    return bytes([
        ((y << 2) + (now.month >> 2)) & 0xFF,
        (((now.month & 3) << 6) + (now.day << 1) + (now.hour >> 4)) & 0xFF,
        (((now.hour & 15) << 4) + (now.minute >> 2)) & 0xFF,
        (((now.minute & 3) << 6) + now.second) & 0xFF,
    ])


def weather7_payload(w):
    d = w["daily"]
    out = bytearray([7])
    for i in range(7):
        dt = datetime.strptime(d["time"][i][:10], "%Y-%m-%d")
        yo = dt.year - 2000
        tmin = math.floor(float(d["temperature_2m_min"][i]))
        tmax = math.ceil(float(d["temperature_2m_max"][i]))
        out.extend([
            (yo >> 8) & 0xFF, yo & 0xFF,
            dt.month, dt.day,
            watch_weather_code(d["weather_code"][i]),
            tmin & 0xFF, tmax & 0xFF
        ])
    return bytes(out)


def current_payload(w):
    cur = w["current"]
    daily = w["daily"]
    dt = datetime.strptime(daily["time"][0][:10], "%Y-%m-%d")
    city = CITY.encode("utf-8")[:50]
    tmin = math.floor(float(daily["temperature_2m_min"][0]))
    tmax = math.ceil(float(daily["temperature_2m_max"][0]))
    temp = round(float(cur["temperature_2m"]))
    out = bytearray([
        (dt.year >> 8) & 0xFF, dt.year & 0xFF,
        dt.month, dt.day,
        0,
        watch_weather_code(cur["weather_code"]),
        tmin & 0xFF, tmax & 0xFF,
        len(city)
    ])
    out.extend(city)
    out.append(temp & 0xFF)
    return bytes(out)


def notify(sender, data):
    global t_current_ack
    b = bytes(data)
    print(f"[{stamp()}] RX  {b.hex(' ').upper()}")
    if len(b) >= 9 and b[0] == 0xFD:
        cmd, key, status = b[4], b[5], b[8]
        print(f"[{stamp()}] ACK cmd=0x{cmd:02X} key=0x{key:02X} status=0x{status:02X}")
        if cmd == CMD_SETTING and key == KEY_WEATHER_CURRENT and status == 1:
            t_current_ack = time.monotonic()
        fut = ack_waiters.get((cmd, key))
        if fut and not fut.done():
            fut.set_result(status)


async def send(client, cmd, key, packet, label):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    ack_waiters[(cmd, key)] = fut
    started = time.monotonic()
    print(f"\n[{stamp()}] TX  {label}")
    print(f"[{stamp()}]     {packet.hex(' ').upper()}")
    await client.write_gatt_char(UART_WRITE, packet, response=False)
    try:
        status = await asyncio.wait_for(fut, 3.0)
        dt = (time.monotonic() - started) * 1000
        print(f"[{stamp()}] RESULTADO: ACK 0x{status:02X} em {dt:.0f} ms")
        return status
    except asyncio.TimeoutError:
        print(f"[{stamp()}] RESULTADO: sem ACK em 3 s")
        return None
    finally:
        ack_waiters.pop((cmd, key), None)


async def find_watch():
    for attempt in range(1, 4):
        print(f"[{stamp()}] Scan {attempt}/3...")
        devices = await BleakScanner.discover(timeout=8)
        for d in devices:
            if DEVICE_NAME.lower() in (d.name or "").lower():
                return d
        if attempt < 3:
            await asyncio.sleep(3)
    return None


async def main():
    print("=" * 66)
    print(" PRO10 - VARREDURA FINAL DO CLIMA")
    print(" Um envio apenas | medicao TX/ACK | observacao por 20 segundos")
    print("=" * 66)

    target = await find_watch()
    if not target:
        print(f"[{stamp()}] PRO 10 nao encontrado.")
        return

    print(f"[{stamp()}] Encontrado: {target}")

    weather = await asyncio.to_thread(get_weather)
    print(
        f"[{stamp()}] Clima que sera enviado: {CITY} | "
        f"{round(float(weather['current']['temperature_2m']))} C | "
        f"WMO {weather['current']['weather_code']}"
    )

    disconnected = asyncio.Event()

    def on_disconnect(_):
        print(f"\n[{stamp()}] *** BLE DESCONECTOU ***")
        disconnected.set()

    client = BleakClient(target, disconnected_callback=on_disconnect)

    try:
        print(f"[{stamp()}] Conectando...")
        await client.connect()
        print(f"[{stamp()}] Conectado: {client.is_connected}")
        await asyncio.sleep(1.0)

        await client.start_notify(UART_NOTIFY, notify)
        print(f"[{stamp()}] Notify ativo.")

        try:
            b = await client.read_gatt_char(BATTERY_UUID)
            print(f"[{stamp()}] Bateria: {int(b[0])}%")
        except Exception as e:
            print(f"[{stamp()}] Bateria: erro {e}")

        for label, uuid in [
            ("Hardware", HARDWARE_UUID),
            ("Firmware", FIRMWARE_UUID),
        ]:
            try:
                b = await client.read_gatt_char(uuid)
                print(f"[{stamp()}] {label}: {bytes(b).decode('utf-8', errors='replace').strip(chr(0))}")
            except Exception as e:
                print(f"[{stamp()}] {label}: erro {e}")

        await send(client, CMD_FEATURE, 0x00, frame(CMD_FEATURE, 0x00), "Capacidades 0x19/0x00")

        try:
            b = await client.read_gatt_char(SOFTWARE_UUID)
            print(f"[{stamp()}] Software raw: {bytes(b).hex(' ').upper()}")
        except Exception as e:
            print(f"[{stamp()}] Software: erro {e}")

        await send(client, CMD_SETTING, KEY_TIME,
                   frame(CMD_SETTING, KEY_TIME, time_payload(datetime.now())),
                   "Hora 0x02/0x01")

        await asyncio.sleep(0.2)

        await send(client, CMD_SETTING, KEY_WEATHER_7,
                   frame(CMD_SETTING, KEY_WEATHER_7, weather7_payload(weather)),
                   "Previsao 7 dias 0x02/0x23")

        await asyncio.sleep(0.2)

        status = await send(client, CMD_SETTING, KEY_WEATHER_CURRENT,
                            frame(CMD_SETTING, KEY_WEATHER_CURRENT, current_payload(weather)),
                            "Clima atual 0x02/0x1D")

        if status == 1:
            print("\n" + "=" * 66)
            print(f"[{stamp()}] O RELOGIO CONFIRMOU O CLIMA.")
            print("Agora NAO toque em nada. Olhe somente a tela do clima.")
            print("Vou marcar quanto tempo passa depois do ACK:")
            print("=" * 66)

            checkpoints = [2, 5, 10, 15, 20]
            prev = 0
            for sec in checkpoints:
                await asyncio.sleep(sec - prev)
                prev = sec
                connected = client.is_connected
                print(f"[{stamp()}] T+{sec:02d}s apos ACK | BLE conectado={connected}")

        print("\n" + "=" * 66)
        print("DIAGNOSTICO:")
        print("- Se o clima apareceu durante T+02..T+20s, existe atraso de renderizacao.")
        print("- Se ACK=0x01 mas a tela nao mudou em 20s, o pacote foi aceito,")
        print("  mas a interface do relogio nao atualizou imediatamente.")
        print("- Esta varredura nao reconecta e nao fica rodando em segundo plano.")
        print("=" * 66)

    finally:
        if client.is_connected:
            try:
                await client.stop_notify(UART_NOTIFY)
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
        print(f"[{stamp()}] Fim. Conexao encerrada.")


if __name__ == "__main__":
    asyncio.run(main())
