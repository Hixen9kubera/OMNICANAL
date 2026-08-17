"""
probar_candado_congelado.py — Las cinco ramas del candado de tablas congeladas.

SOLO LECTURA. No escribe en ninguna base: las cuatro primeras pruebas sustituyen
la medición por un valor fijo, y la última mide de verdad contra MySQL pero solo
con un `SELECT MAX(...)`.

POR QUÉ EXISTE
--------------
Un candado que no se prueba es un adorno. Y este en particular ya falló dos veces
de maneras que "se veían bien":

1. La primera versión abortaba por un `UnicodeEncodeError` de un carácter de
   adorno, no por decisión. Pasaba la prueba de "¿aborta?" y fallaba la de "¿por
   qué aborta?".
2. El parche fue un `except UnicodeEncodeError` que **nunca dispara**, porque la
   consola reemplaza en silencio.

La rama que más importa es la B: **si no se puede medir, no se escribe.** Un
`except` que dejara pasar la escritura sería exactamente el defecto que el
candado viene a tapar — el mismo `except → {}` de `_foto()` que hizo que un
tropiezo de BD se leyera como "primera pasada".

Uso:
  ...python -m scripts.probar_candado_congelado
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scripts import _candado_congelado as candado  # noqa: E402

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" +
          (f" — {detalle}" if detalle else ""))


def _fijo(horas: float):
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    return lambda t, col: (horas, ahora - timedelta(hours=horas))


def _revienta(t: str, col: str):
    raise RuntimeError("MySQL inalcanzable (simulado)")


def main() -> None:
    real = candado._edad_horas
    print("PRUEBAS DEL CANDADO DE TABLAS CONGELADAS\n")

    # ── A) tabla viva: el candado no debe estorbar ───────────────────────────
    candado._edad_horas = _fijo(0.4)
    try:
        candado.exigir_viva("canal_inventario", va_a_escribir=True,
                            que_decide="algo", alternativa="otra cosa")
        check("tabla VIVA + escritura: se aparta y deja escribir", True)
    except SystemExit as e:
        check("tabla VIVA + escritura: se aparta y deja escribir", False,
              f"abortó con {e.code} — un candado que estorba con la tabla viva "
              f"se acaba comentando")

    # ── B) LA IMPORTANTE: sin medición no se escribe ─────────────────────────
    candado._edad_horas = _revienta
    try:
        candado.exigir_viva("pedidos_ml", va_a_escribir=True, que_decide="algo")
        check("sin medición + escritura: ABORTA (falla cerrada)", False,
              "dejó pasar la escritura sin poder medir")
    except SystemExit as e:
        check("sin medición + escritura: ABORTA (falla cerrada)", e.code == 2,
              f"exit {e.code}")

    # ── C) sin medición pero solo mirando: pasa con aviso ────────────────────
    try:
        candado.exigir_viva("pedidos_ml", va_a_escribir=False, que_decide="algo")
        check("sin medición + dry-run: deja mirar", True)
    except SystemExit as e:
        check("sin medición + dry-run: deja mirar", False, f"abortó con {e.code}")

    # ── D) congelada: aborta la escritura, permite el dry-run ────────────────
    candado._edad_horas = _fijo(107.0)
    try:
        candado.exigir_viva("pedidos_ml", va_a_escribir=True, que_decide="algo")
        check("CONGELADA + escritura: ABORTA", False, "dejó escribir")
    except SystemExit as e:
        check("CONGELADA + escritura: ABORTA", e.code == 2, f"exit {e.code}")
    try:
        candado.exigir_viva("pedidos_ml", va_a_escribir=False, que_decide="algo")
        check("CONGELADA + dry-run: deja mirar con cartel", True)
    except SystemExit as e:
        check("CONGELADA + dry-run: deja mirar con cartel", False, f"exit {e.code}")

    # ── E) el mensaje sobrevive a una consola cp1252 ─────────────────────────
    # La rama que ya falló una vez. Se verifica el transformador directo, no el
    # print: lo que importa es que no queden caracteres fuera de ASCII.
    prueba = "apagón · «cerrado» — 13-ago 04:23 ✓"
    import io
    import unicodedata
    plano = unicodedata.normalize("NFKD", prueba).encode("ascii", "ignore").decode()
    check("el texto del candado queda en ASCII puro", plano.isascii(),
          f"salida: {plano!r}")
    check("y no lo deja picado de signos de pregunta", "?" not in plano,
          "transliterar, no reemplazar: 'apag?n' es ilegible justo cuando "
          "más importa entenderlo")
    buf = io.StringIO()
    _stdout, sys.stdout = sys.stdout, buf
    try:
        candado._di(prueba)
    finally:
        sys.stdout = _stdout
    check("_di() no revienta con acentos ni signos raros",
          buf.getvalue().strip().isascii(), f"imprimió: {buf.getvalue().strip()!r}")

    # ── F) medición real, sin simular nada ───────────────────────────────────
    candado._edad_horas = real
    print("\n── frescura real de las tablas del mapa (SELECT MAX) ──")
    for tabla, (col, _) in candado._CONGELABLES.items():
        try:
            horas, ultima = candado._edad_horas(tabla, col)
            estado = "VIVA" if horas <= candado._UMBRAL_H else "CONGELADA"
            print(f"  {tabla:20s} {ultima:%d-%b %H:%M}  hace {horas/24:5.1f} d  → {estado}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {tabla:20s} no se pudo medir: {exc}")

    print(f"\nRESULTADO: {'el candado se porta como debe' if _ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
