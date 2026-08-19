# PRO10 - Extrair como o Olywear envia o switch de chamadas Bluetooth
# SOMENTE LEITURA. Nao conecta nem envia nada ao relogio.
#
# Requisito:
#   python -m pip install androguard
#
# Uso:
#   python .\pro10_extrair_metodo_chamada.py

from pathlib import Path

APK = Path.home() / "Downloads" / "olywear-base.apk"
OUT = Path.home() / "Documents" / "PRO10-Open" / "pro10_metodo_chamada_resultado.txt"

TARGET_CLASS = "Lcom/lianhezhuli/olywear/ble/SettingIssuedUtils;"
TARGET_FIELD = "KEY_SETTING_SWITCH_ANDROD_CALL"

def main():
    lines = []
    lines.append("=" * 84)
    lines.append(" PRO10 - EXTRACAO DO METODO DE SWITCH DE CHAMADAS BLUETOOTH")
    lines.append(" SOMENTE LEITURA - nenhum comando sera enviado ao relogio")
    lines.append("=" * 84)

    if not APK.exists():
        lines.append(f"\n[ERRO] APK nao encontrado: {APK}")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        return

    try:
        from androguard.misc import AnalyzeAPK
    except Exception:
        print("ERRO: androguard nao esta instalado.")
        print("Execute:")
        print("  python -m pip install androguard")
        return

    lines.append(f"\n[APK] {APK}")
    lines.append("[INFO] Analisando somente os pontos de uso da chave 0x11...")

    a, dex_list, dx = AnalyzeAPK(str(APK))

    target_methods = []

    for vm in dex_list:
        for cls in vm.get_classes():
            if cls.get_name() != TARGET_CLASS:
                continue

            lines.append("\n" + "=" * 84)
            lines.append(f" CLASSE ALVO: {TARGET_CLASS}")
            lines.append("=" * 84)

            # campos
            lines.append("\n--- CAMPOS ---")
            for f in cls.get_fields():
                name = f.get_name()
                try:
                    iv = f.get_init_value()
                except Exception:
                    iv = None
                if "CALL" in name or "SWITCH" in name or "SETTING" in name:
                    valtxt = repr(iv)
                    try:
                        v = iv.get_value()
                        valtxt += f" value={v!r}"
                        if isinstance(v, int):
                            valtxt += f" hex=0x{v & 0xffffffff:X}"
                    except Exception:
                        pass
                    lines.append(f"{name} {f.get_descriptor()} {valtxt}")

            # metodos
            lines.append("\n--- METODOS COM REFERENCIA A CHAMADA / SWITCH ---")
            for m in cls.get_methods():
                code = m.get_code()
                if code is None:
                    continue

                ins = list(m.get_instructions())
                outputs = [(i.get_name(), i.get_output() or "") for i in ins]
                blob = "\n".join(o for _, o in outputs)

                # filtra por campo alvo ou termos suspeitos
                if (TARGET_FIELD not in blob and
                    "ANDROD_CALL" not in blob and
                    "CALL" not in m.get_name().upper() and
                    "SWITCH" not in m.get_name().upper()):
                    continue

                target_methods.append((cls.get_name(), m.get_name(), m.get_descriptor(), ins))
                lines.append("\n" + "-" * 84)
                lines.append(f"{cls.get_name()}->{m.get_name()}{m.get_descriptor()}")

                for idx, inst in enumerate(ins):
                    out = inst.get_output() or ""
                    marker = ">>" if (
                        TARGET_FIELD in out or
                        "ANDROD_CALL" in out or
                        "CALL" in out.upper() or
                        "SWITCH" in out.upper()
                    ) else "  "
                    lines.append(f"{marker} {idx:04d}: {inst.get_name():22s} {out}")

    # busca global por metodos que usem a chave, caso o campo seja inlined
    lines.append("\n" + "=" * 84)
    lines.append(" BUSCA GLOBAL POR CONSTANTE 0x11 EM CONTEXTO DE SETTING/CALL")
    lines.append("=" * 84)

    global_hits = 0
    for vm in dex_list:
        for cls in vm.get_classes():
            cname = cls.get_name()
            if "lianhezhuli" not in cname.lower() and "lhzl" not in cname.lower():
                continue

            for m in cls.get_methods():
                code = m.get_code()
                if code is None:
                    continue

                ins = list(m.get_instructions())
                text = "\n".join((i.get_output() or "") for i in ins)
                name_upper = m.get_name().upper()

                # heuristica: metodo relacionado a setting/call, e const 17/0x11
                related = (
                    "SETTING" in cname.upper() or
                    "SETTING" in name_upper or
                    "CALL" in cname.upper() or
                    "CALL" in name_upper or
                    "ANDROD_CALL" in text
                )
                if not related:
                    continue

                hit_indices = []
                for idx, inst in enumerate(ins):
                    out = inst.get_output() or ""
                    low = out.lower()
                    if ("0x11" in low or
                        ", 17" in low or
                        low.strip().endswith("17") or
                        TARGET_FIELD in out or
                        "ANDROD_CALL" in out):
                        hit_indices.append(idx)

                if not hit_indices:
                    continue

                global_hits += 1
                lines.append("\n" + "-" * 84)
                lines.append(f"{cname}->{m.get_name()}{m.get_descriptor()}")

                shown = set()
                for idx in hit_indices:
                    start = max(0, idx - 14)
                    end = min(len(ins), idx + 24)
                    for j in range(start, end):
                        if j in shown:
                            continue
                        shown.add(j)
                        inst = ins[j]
                        marker = ">>" if j == idx else "  "
                        lines.append(f"{marker} {j:04d}: {inst.get_name():22s} {inst.get_output() or ''}")

    lines.append("\n" + "=" * 84)
    lines.append(" RESUMO")
    lines.append("=" * 84)
    lines.append(f"Metodos encontrados na classe alvo: {len(target_methods)}")
    lines.append(f"Metodos globais candidatos: {global_hits}")
    lines.append("")
    lines.append("Envie este TXT ao ChatGPT.")
    lines.append("Nao teste nenhum pacote no relogio antes da analise.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines[:60]))
    print("\n[...]")
    print(f"\n[OK] Relatorio salvo em:\n{OUT}")

if __name__ == "__main__":
    main()
