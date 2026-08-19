# PRO10 - Extrair chave de chamadas Bluetooth do APK Olywear
# SOMENTE LEITURA. Nao conecta nem envia nada ao relogio.
#
# Requisito:
#   python -m pip install androguard
#
# Uso:
#   python .\pro10_extrair_chave_chamada.py

from pathlib import Path
import sys
import re

APK = Path.home() / "Downloads" / "olywear-base.apk"
OUT = Path.home() / "Documents" / "PRO10-Open" / "pro10_chave_chamada_resultado.txt"

TARGETS = [
    "KEY_SETTING_SWITCH_ANDROD_CALL",
    "SUCCESS_KEY_SETTING_SWITCH_ANDROD_CALL",
]

def main():
    lines = []
    lines.append("=" * 78)
    lines.append(" PRO10 - EXTRACAO DA CHAVE DE CHAMADAS BLUETOOTH")
    lines.append(" SOMENTE LEITURA - nenhum comando sera enviado ao relogio")
    lines.append("=" * 78)

    if not APK.exists():
        lines.append(f"\n[ERRO] APK nao encontrado: {APK}")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        return

    try:
        from androguard.misc import AnalyzeAPK
    except Exception as e:
        print("ERRO: androguard nao esta instalado.")
        print("Execute primeiro:")
        print("  python -m pip install androguard")
        print("\nDepois rode este script novamente.")
        return

    lines.append(f"\n[APK] {APK}")
    lines.append("[INFO] Abrindo APK com Androguard. Isso pode levar alguns segundos...")

    a, dex_list, dx = AnalyzeAPK(str(APK))

    # 1) procurar campos exatos e valores iniciais
    found_fields = []

    for vm in dex_list:
        for cls in vm.get_classes():
            cname = cls.get_name()
            for field in cls.get_fields():
                fname = field.get_name()
                if fname in TARGETS:
                    init = None
                    try:
                        init = field.get_init_value()
                    except Exception:
                        pass

                    entry = {
                        "class": cname,
                        "name": fname,
                        "descriptor": field.get_descriptor(),
                        "access": field.get_access_flags_string(),
                        "init": init,
                    }
                    found_fields.append(entry)

    lines.append("\n" + "=" * 78)
    lines.append(" CAMPOS ENCONTRADOS")
    lines.append("=" * 78)

    if not found_fields:
        lines.append("Nenhum campo exato encontrado.")
    else:
        for f in found_fields:
            lines.append(f"\nClasse: {f['class']}")
            lines.append(f"Campo : {f['name']}")
            lines.append(f"Tipo  : {f['descriptor']}")
            lines.append(f"Acesso: {f['access']}")
            lines.append(f"Valor inicial bruto: {repr(f['init'])}")

            # tentar extrair valor numérico de EncodedValue
            try:
                v = f["init"].get_value()
                lines.append(f"Valor inicial: {v!r}")
                if isinstance(v, int):
                    lines.append(f"HEX: 0x{v & 0xFFFFFFFF:X}")
            except Exception:
                pass

    # 2) procurar metodos que referenciam os campos
    lines.append("\n" + "=" * 78)
    lines.append(" METODOS QUE REFERENCIAM AS CHAVES")
    lines.append("=" * 78)

    refs_found = 0

    target_field_sigs = set()
    for f in found_fields:
        target_field_sigs.add((f["class"], f["name"], f["descriptor"]))

    for vm in dex_list:
        for cls in vm.get_classes():
            for method in cls.get_methods():
                code = method.get_code()
                if code is None:
                    continue

                instructions = list(method.get_instructions())
                hit_indexes = []

                for i, ins in enumerate(instructions):
                    out = ins.get_output() or ""
                    if any(t in out for t in TARGETS):
                        hit_indexes.append(i)

                if not hit_indexes:
                    continue

                refs_found += 1
                lines.append("\n" + "-" * 78)
                lines.append(
                    f"{cls.get_name()}->{method.get_name()}{method.get_descriptor()}"
                )

                for idx in hit_indexes:
                    start = max(0, idx - 10)
                    end = min(len(instructions), idx + 16)
                    lines.append(f"\n  [Referencia na instrucao {idx}]")
                    for j in range(start, end):
                        marker = ">>" if j == idx else "  "
                        ins = instructions[j]
                        lines.append(
                            f"{marker} {j:04d}: {ins.get_name():20s} {ins.get_output()}"
                        )

    if refs_found == 0:
        lines.append("Nenhum metodo com referencia textual direta encontrado.")

    # 3) procurar constantes/numeros proximos a palavras CALL / SWITCH
    lines.append("\n" + "=" * 78)
    lines.append(" OUTRAS PISTAS RELACIONADAS A CALL / SWITCH")
    lines.append("=" * 78)

    extra = set()
    for vm in dex_list:
        for cls in vm.get_classes():
            cname = cls.get_name()
            if "lianhezhuli" not in cname.lower() and "lhzl" not in cname.lower():
                continue
            for field in cls.get_fields():
                n = field.get_name()
                low = n.lower()
                if ("call" in low and "switch" in low) or ("android_call" in low):
                    key = (cname, n, field.get_descriptor())
                    if key in extra:
                        continue
                    extra.add(key)
                    try:
                        iv = field.get_init_value()
                    except Exception:
                        iv = None
                    lines.append(f"{cname} -> {n} {field.get_descriptor()} init={iv!r}")
                    try:
                        v = iv.get_value()
                        lines.append(f"    valor={v!r}" + (f" hex=0x{v:X}" if isinstance(v, int) else ""))
                    except Exception:
                        pass

    lines.append("\n" + "=" * 78)
    lines.append(" CONCLUSAO AUTOMATICA")
    lines.append("=" * 78)
    if found_fields:
        lines.append("A chave foi localizada no bytecode.")
        lines.append("Envie este TXT ao ChatGPT para identificar com seguranca o valor e o pacote.")
    else:
        lines.append("A chave apareceu como string no DEX, mas nao como campo simples.")
        lines.append("O relatorio de metodos ainda pode revelar onde ela e usada.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines[:55]))
    print("\n[...]")
    print(f"\n[OK] Relatorio completo salvo em:\n{OUT}")

if __name__ == "__main__":
    main()
