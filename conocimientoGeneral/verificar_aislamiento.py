"""
verificar_aislamiento.py — El guardián de las reglas de esta carpeta.

POR QUÉ EXISTE

`LEEME.md` dice que nada de `conocimientoGeneral/` escribe en producción. Una
regla escrita se rompe el día que alguien tiene prisa, y nadie se entera hasta
que un script "de prueba" cambió 300 precios. Esto la vuelve comprobable.

Revisa CINCO cosas y sale con código 1 si alguna falla:

  1. Que ningún archivo de aquí ESCRIBA en Woo, kubera, Odoo o un marketplace.
  2. Que no haya secretos escritos (un secreto en git es un secreto para siempre,
     aunque el repositorio sea privado: queda en el historial).
  3. Que producción no importe nada de esta carpeta.
  4. Que estés parado en la rama `conocimiento`, no en `main`.
  5. Que esta rama NO haya modificado ni un archivo fuera de
     `conocimientoGeneral/` respecto a `main`.

    python conocimientoGeneral/verificar_aislamiento.py

LA QUINTA ES LA MÁS IMPORTANTE Y LA MENOS OBVIA. Esta rama salió de `main`, así
que **todo el código de producción está aquí**: `backend/`, `frontend/`, todo. Es
a propósito —hay que poder LEERLO para extraer conocimiento— pero significa que
alguien puede editar `backend/services/costos.py` en esta rama sin darse cuenta.

Hoy eso no llega a producción, porque Railway despliega solo desde `main`. Pero
el día que alguien mezcle esta rama —o simplemente copie un archivo de vuelta—
ese cambio entra sin haber pasado por ninguna revisión. La comprobación 5 lo caza
antes de que exista: **en esta rama, el código de producción se lee, no se
toca.**

LO QUE **NO** PUEDE VER, y hay que saberlo para no confiarse de más:

  · texto armado en pedazos (`m = "PO" + "ST"`), o un método en una variable;
  · una llamada indirecta a través de una librería que no reconozca;
  · un `subprocess` que corra otra cosa.

Es una red de seguridad contra el descuido, **no contra alguien decidido**. La
protección de verdad sigue siendo la rama: Railway despliega solo desde `main`.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

AQUI = pathlib.Path(__file__).resolve().parent
RAIZ = AQUI.parent

# ── 1 · Escrituras ───────────────────────────────────────────────────────────
#
# Se busca el VERBO junto a un destino, no el verbo suelto: `POST` a la API de
# la IA es legítimo y necesario (es la mitad del trabajo de esta carpeta), y
# marcarlo sería el camino más corto a que nadie vuelva a correr esto.
_ESCRITURAS = [
    (re.compile(r"\.(put|patch|delete)\s*\(", re.I),
     "escritura HTTP (PUT/PATCH/DELETE)"),
    (re.compile(r"\.post\s*\([^)]*(woocommerce|wp-json|/products|/orders|"
                r"mercadolibre|/items|sp-api|amazon|tiktokglobalshop|temu|walmart)",
                re.I | re.S),
     "POST a un marketplace o a WooCommerce"),
    (re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
                r"UPSERT|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)\b", re.I),
     "escritura SQL"),
    (re.compile(r"\bcommit\s*\(\s*\)", re.I), "commit de base de datos"),
    (re.compile(r"set_session\s*\([^)]*readonly", re.I),
     "🔴 marca la sesión read-only — ENVENENA EL POOL COMPARTIDO de kubera "
     "(regla 13 de CLAUDE.md); usa BEGIN; SET TRANSACTION READ ONLY; ...; ROLLBACK;"),
]

# ── 2 · Secretos ─────────────────────────────────────────────────────────────
#
# Se buscan valores con FORMA de secreto, no la palabra "password": un
# `PASSWORD = os.environ[...]` es correcto y no debe dar alarma.
_SECRETOS = [
    (re.compile(r"postgres(?:ql)?://[^\s\"']*:[^\s\"'@]+@", re.I),
     "DSN de Postgres con contraseña"),
    (re.compile(r"mysql://[^\s\"']*:[^\s\"'@]+@", re.I), "DSN de MySQL con contraseña"),
    (re.compile(r"\b(ck|cs)_[0-9a-f]{32,}\b"), "llave de WooCommerce"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "llave estilo OpenAI/DeepSeek"),
    (re.compile(r"\bAPP_USR-[0-9A-Za-z-]{20,}\b"), "token de Mercado Libre"),
    (re.compile(r"\bey[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
     "JWT (¿llave de Supabase?)"),
    (re.compile(r"""(?ix)\b(api[_-]?key|secret|token|password|passwd|client_secret)\b"""
                r"""\s*[:=]\s*["'][^"'\s]{16,}["']"""),
     "credencial escrita en claro"),
]

# `.env.ejemplo` existe justamente para llevar nombres de variables vacíos.
_EXENTOS_SECRETO = {".env.ejemplo", ".env.example"}
# Este archivo contiene los patrones, así que se buscaría a sí mismo.
_EXENTOS_SIEMPRE = {"verificar_aislamiento.py"}

_EXT = {".py", ".js", ".ts", ".sh", ".ps1", ".sql", ".md", ".json", ".yaml", ".yml"}
_SALTAR_DIR = {"salidas", "__pycache__", ".git", "node_modules", ".venv", "venv"}


def _archivos():
    for p in AQUI.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in _EXT:
            continue
        if any(parte in _SALTAR_DIR for parte in p.parts):
            continue
        if p.name in _EXENTOS_SIEMPRE:
            continue
        yield p


def _lineas_con(p: pathlib.Path, patron: re.Pattern) -> list[tuple[int, str]]:
    try:
        texto = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    salida = []
    for m in patron.finditer(texto):
        n = texto.count("\n", 0, m.start()) + 1
        linea = texto.splitlines()[n - 1] if n - 1 < len(texto.splitlines()) else ""
        salida.append((n, linea.strip()[:120]))
    return salida


def revisar_escrituras() -> list[str]:
    fallos = []
    for p in _archivos():
        # En los .md se documenta lo que HACE producción: citar un PUT ahí es
        # describir, no ejecutar. Solo se revisa el código ejecutable.
        if p.suffix.lower() in {".md", ".json"}:
            continue
        for patron, que in _ESCRITURAS:
            for n, linea in _lineas_con(p, patron):
                fallos.append(f"{p.relative_to(RAIZ)}:{n}  {que}\n      {linea}")
    return fallos


def revisar_secretos() -> list[str]:
    fallos = []
    for p in _archivos():
        if p.name in _EXENTOS_SECRETO:
            continue
        for patron, que in _SECRETOS:
            for n, _ in _lineas_con(p, patron):
                # El valor NO se imprime: el reporte podría acabar pegado en un
                # chat o en un log, y sería filtrarlo otra vez.
                fallos.append(f"{p.relative_to(RAIZ)}:{n}  {que} (valor omitido a propósito)")
    return fallos


def revisar_importaciones() -> list[str]:
    """¿Producción importa algo de aquí? Sería el fin de la separación."""
    fallos = []
    for carpeta in ("backend", "frontend"):
        d = RAIZ / carpeta
        if not d.exists():
            continue
        try:
            r = subprocess.run(
                ["git", "grep", "-n", "-I", "conocimientoGeneral", "--", carpeta],
                cwd=RAIZ, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            fallos.append(f"no pude revisar {carpeta}/ ({exc}) — revísalo a mano")
            continue
        for linea in (r.stdout or "").splitlines():
            fallos.append(f"producción menciona esta carpeta → {linea.strip()[:160]}")
    return fallos


def revisar_rama() -> list[str]:
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           cwd=RAIZ, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"no pude leer la rama ({exc})"]
    rama = (r.stdout or "").strip()
    if rama != "conocimiento":
        return [f"estás en la rama '{rama}', NO en 'conocimiento'. "
                f"Esta carpeta no debe existir fuera de su rama: si estás en main, "
                f"algo se mezcló y hay que deshacerlo ANTES de seguir."]
    return []


def revisar_produccion_intacta() -> list[str]:
    """¿Esta rama cambió algo FUERA de conocimientoGeneral/ respecto a main?

    Se compara contra `origin/main` si existe (es la referencia compartida) y si
    no, contra el `main` local. Si no hay ninguno de los dos —un clon de una
    sola rama, que es justo como entra alguien de fuera— NO se puede comprobar,
    y eso se dice en voz alta en vez de callarlo: un verificador que guarda
    silencio cuando no sabe es peor que no tenerlo.
    """
    def _existe(ref: str) -> bool:
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref],
                           cwd=RAIZ, capture_output=True, text=True)
        return r.returncode == 0

    base = next((r for r in ("origin/main", "main") if _existe(r)), None)
    if base is None:
        return ["no tengo `main` local ni `origin/main`, así que NO PUEDE "
                "comprobarse que la rama no tocó producción. Trae la referencia "
                "con: git fetch origin main:refs/remotes/origin/main"]
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD",
             "--", ".", ":(exclude)conocimientoGeneral"],
            cwd=RAIZ, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"no pude comparar contra {base} ({exc}) — revísalo a mano"]
    if r.returncode != 0:
        return [f"git diff contra {base} falló: {(r.stderr or '').strip()[:200]}"]

    tocados = {x.strip() for x in (r.stdout or "").splitlines() if x.strip()}

    # Y lo que TODAVÍA NO ES COMMIT. `git diff base...HEAD` compara commits, así
    # que un archivo de producción editado y sin guardar le pasa por debajo —
    # exactamente el estado en el que alguien está a punto de romper la regla, y
    # el momento en que avisar todavía sirve de algo. Se descubrió probando el
    # verificador contra una trampa sembrada: no la cazó.
    try:
        s = subprocess.run(
            ["git", "status", "--porcelain",
             "--", ".", ":(exclude)conocimientoGeneral"],
            cwd=RAIZ, capture_output=True, text=True, timeout=120)
        for linea in (s.stdout or "").splitlines():
            ruta = linea[3:].strip().strip('"')
            if " -> " in ruta:            # renombrado: interesa el destino
                ruta = ruta.split(" -> ", 1)[1]
            if ruta:
                tocados.add(ruta + "  (sin guardar)")
    except (OSError, subprocess.SubprocessError) as exc:
        tocados.add(f"no pude revisar los cambios sin guardar ({exc})")

    if not tocados:
        return []
    orden = sorted(tocados)
    fallos = [f"esta rama toca {len(orden)} archivo(s) de PRODUCCIÓN. "
              f"Aquí el código de producción SE LEE, NO SE TOCA:"]
    fallos += [f"  → {t}" for t in orden[:20]]
    if len(orden) > 20:
        fallos.append(f"  … y {len(orden) - 20} más")
    fallos.append("Si el cambio es bueno, va a main por el camino normal, no por aquí.")
    return fallos


def main() -> int:
    bloques = [
        ("RAMA", revisar_rama()),
        ("PRODUCCIÓN INTACTA EN ESTA RAMA", revisar_produccion_intacta()),
        ("ESCRITURAS A PRODUCCIÓN", revisar_escrituras()),
        ("SECRETOS", revisar_secretos()),
        ("PRODUCCIÓN IMPORTANDO ESTA CARPETA", revisar_importaciones()),
    ]
    total = sum(len(f) for _, f in bloques)

    for titulo, fallos in bloques:
        marca = "OK  " if not fallos else "FALLA"
        print(f"[{marca}] {titulo}" + (f" — {len(fallos)}" if fallos else ""))
        for f in fallos:
            print(f"        {f}")

    print()
    if total:
        print(f"{total} problema(s). Esta carpeta NO cumple sus propias reglas.")
        print("Arréglalo antes de hacer commit — o si es un falso positivo, "
              "explícalo en el archivo y en el commit.")
        return 1
    print("Aislamiento correcto: nada de aquí escribe en producción.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
