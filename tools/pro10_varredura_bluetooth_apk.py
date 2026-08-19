# PRO10 - Varredura de Bluetooth no APK Olywear
# Apenas leitura: NÃO envia nenhum comando ao relógio.
#
# Uso:
#   python .\pro10_varredura_bluetooth_apk.py
#
# APK esperado:
#   %USERPROFILE%\Downloads\olywear-base.apk

import os
import re
import struct
import zipfile
from pathlib import Path

APK = Path.home() / "Downloads" / "olywear-base.apk"
OUT = Path.home() / "Documents" / "PRO10-Open" / "pro10_apk_bluetooth_resultado.txt"

KEYWORDS = [
    "bluetooth", "ble", "classic", "audio", "a2dp", "avrcp", "hfp",
    "handsfree", "headset", "call", "phone", "music", "media",
    "power", "powersave", "power_save", "lowpower", "low_power",
    "battery", "switch", "enable", "disable", "radio", "spp",
    "bt_", "_bt", "btmode", "bt_mode", "btstatus", "bt_status",
    "btcall", "bt_call", "call_switch", "audio_switch",
    "bluetooth_switch", "bluetoothswitch", "onekey", "one_key",
    "connect", "disconnect", "pair", "bond",
    "cmd_", "command_", "key_"
]

# Palavras de maior interesse: aparecem primeiro no relatório.
PRIORITY = [
    "bluetooth", "bt_", "_bt", "btcall", "bt_call",
    "call", "audio", "a2dp", "hfp", "handsfree",
    "power", "low_power", "lowpower", "switch", "radio",
    "cmd_", "command_", "key_"
]

def uleb128(data, off):
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, off
        shift += 7

def dex_strings(data):
    if len(data) < 112 or not data.startswith(b"dex\n"):
        return []
    string_ids_size = struct.unpack_from("<I", data, 56)[0]
    string_ids_off = struct.unpack_from("<I", data, 60)[0]
    out = []
    for i in range(string_ids_size):
        pos = string_ids_off + 4 * i
        if pos + 4 > len(data):
            break
        string_data_off = struct.unpack_from("<I", data, pos)[0]
        if string_data_off >= len(data):
            continue
        try:
            _, p = uleb128(data, string_data_off)
            end = data.find(b"\x00", p)
            if end == -1:
                continue
            raw = data[p:end]
            # DEX usa modified UTF-8; para nossa caça de identificadores,
            # utf-8 tolerante resolve os casos relevantes.
            s = raw.decode("utf-8", errors="replace")
            out.append(s)
        except Exception:
            continue
    return out

def relevant(s):
    low = s.lower()
    return any(k in low for k in KEYWORDS)

def score(s):
    low = s.lower()
    points = 0
    for i, k in enumerate(PRIORITY):
        if k in low:
            points += (len(PRIORITY) - i) * 10
    # Favorece nomes de classes/métodos/constantes e reduz textos enormes.
    if "/" in s or "." in s or "_" in s:
        points += 15
    if s.isupper():
        points += 20
    if len(s) > 250:
        points -= 40
    return points

def clean(s):
    return s.replace("\r", "\\r").replace("\n", "\\n").strip()

def main():
    lines = []
    lines.append("=" * 78)
    lines.append(" PRO10 - VARREDURA APK OLYWEAR: BLUETOOTH / AUDIO / POWER")
    lines.append(" SOMENTE LEITURA - nenhum comando e enviado ao relogio")
    lines.append("=" * 78)

    if not APK.exists():
        lines.append(f"\n[ERRO] APK nao encontrado:\n{APK}")
        lines.append("\nConfirme se o arquivo olywear-base.apk continua em Downloads.")
        text = "\n".join(lines)
        print(text)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        return

    lines.append(f"\n[APK] {APK}")
    lines.append(f"[TAMANHO] {APK.stat().st_size:,} bytes")

    all_hits = []
    file_hits = []

    with zipfile.ZipFile(APK, "r") as z:
        names = z.namelist()

        # Nomes de arquivos relevantes dentro do APK.
        for name in names:
            if relevant(name):
                file_hits.append(name)

        dex_names = [n for n in names if re.fullmatch(r"classes\d*\.dex", Path(n).name)]
        lines.append(f"[DEX] {len(dex_names)} arquivo(s): {', '.join(dex_names)}")

        for dex_name in dex_names:
            data = z.read(dex_name)
            strings = dex_strings(data)
            for s in strings:
                if relevant(s):
                    all_hits.append((score(s), dex_name, clean(s)))

    # Deduplicar mantendo melhor score/origem.
    unique = {}
    for sc, dex, s in all_hits:
        if not s:
            continue
        key = s.lower()
        if key not in unique or sc > unique[key][0]:
            unique[key] = (sc, dex, s)

    ranked = sorted(unique.values(), key=lambda x: (-x[0], x[2].lower()))

    lines.append("\n" + "=" * 78)
    lines.append(" ARQUIVOS/CAMINHOS RELEVANTES DENTRO DO APK")
    lines.append("=" * 78)
    if file_hits:
        for name in sorted(set(file_hits)):
            lines.append(name)
    else:
        lines.append("(nenhum nome de arquivo relevante encontrado)")

    lines.append("\n" + "=" * 78)
    lines.append(" STRINGS / CLASSES / METODOS / CONSTANTES CANDIDATOS")
    lines.append("=" * 78)

    # Dividir em faixas para facilitar leitura.
    high = [x for x in ranked if x[0] >= 100]
    medium = [x for x in ranked if 50 <= x[0] < 100]
    low = [x for x in ranked if x[0] < 50]

    for title, group, limit in [
        ("ALTA PRIORIDADE", high, 300),
        ("MEDIA PRIORIDADE", medium, 300),
        ("OUTROS RESULTADOS", low, 200),
    ]:
        lines.append(f"\n--- {title} ({len(group)}) ---")
        for sc, dex, s in group[:limit]:
            lines.append(f"[{sc:03d}] [{dex}] {s}")

    # Bloco focado em possíveis comandos do protocolo.
    command_like = []
    patterns = (
        "cmd", "command", "key", "order", "setting", "switch",
        "bluetooth", "bt_", "_bt", "call", "audio", "power"
    )
    for sc, dex, s in ranked:
        low_s = s.lower()
        if any(p in low_s for p in patterns):
            if len(s) <= 180:
                command_like.append((sc, dex, s))

    lines.append("\n" + "=" * 78)
    lines.append(" CANDIDATOS A COMANDO/CHAVE")
    lines.append("=" * 78)
    for sc, dex, s in command_like[:500]:
        lines.append(f"[{sc:03d}] [{dex}] {s}")

    lines.append("\n" + "=" * 78)
    lines.append(" RESUMO")
    lines.append("=" * 78)
    lines.append(f"Strings relevantes unicas: {len(ranked)}")
    lines.append(f"Alta prioridade: {len(high)}")
    lines.append(f"Media prioridade: {len(medium)}")
    lines.append(f"Outros: {len(low)}")
    lines.append("")
    lines.append("Proximo passo: enviar este TXT para o ChatGPT analisar os candidatos.")
    lines.append("NAO testar valores/bytes no relogio antes dessa analise.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines[:35]))
    print("\n[...]")
    print(f"\n[OK] Relatorio completo salvo em:\n{OUT}")
    print("\nEnvie o arquivo pro10_apk_bluetooth_resultado.txt para continuarmos.")

if __name__ == "__main__":
    main()
