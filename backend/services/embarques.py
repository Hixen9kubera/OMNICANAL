"""
embarques.py — Los contenedores del filtro de Costos, armados desde los packing
lists (costing.packing_*) y desde `costos_validados.contenedor`.

Por qué existe: el filtro «Todos los contenedores» de /costos listaba solo
`distinct costos_validados.contenedor` (103 valores, nada nuevo desde junio) y
no veía los packing lists que ya viven en Supabase (0055/0057) ni los SKUs sin
fila de costo. Aquí se junta todo en EMBARQUES: un embarque es un contenedor de
Kubera («contenedor 80») aunque cada fuente lo llame distinto.

── LO QUE SE MIDIÓ (prod, 25-sep, solo lectura) ─────────────────────────────
  - El MISMO embarque trae DOS códigos: costos usa el del ORIGINAL (guía, BL,
    SZLS, 027F, PRY25-543) y el packing el ISO del Ferraforme. «256059868 - 1»
    (578 SKUs) es el Ferraforme «256059868 TRHU6215242 contenedor 1». La llave
    que los une es el número de Kubera (N): vive en el NOMBRE del Ferraforme y
    en el sufijo « - N» de costos (98.7 %).
  - N no es llave perfecta (el BL 149504230930 tiene Ferraforme «contenedor 64»
    y «Contenedor 68»; el 54 lo reclaman TCNU3635850 y una basura INHERIT(...)).
    Se acepta agrupar por N y enseñar TODOS los códigos del grupo.
  - `packing_archivos.contenedor_base` está MAL en varios (regla del «match más
    largo» de copiar_packing_lists.py): NO se usa; los códigos se recalculan
    aquí, al leer, desde `nombre`/`miembro`.

Todo lo de arriba del bloque «Lectura» son funciones PURAS (las fijan las
pruebas con nombres reales). La lectura son tres consultas chicas y la
agrupación se guarda 30 s en el proceso.
"""
from __future__ import annotations

import re
import string
import threading
import time
from dataclasses import dataclass, field

from services import supabase_db as sdb

# ── Extracción de códigos (puro) ──────────────────────────────────────────────

# ISO 6346 sin límite izquierdo: atrapa TGBU7274837 dentro de «TGBU7274837LISTA»
# y EITU1380423 en «EITU1380423-LISTAEMPAQUE». El `(?!\d)` es el que impide
# sacar un ISO de dentro de COSU6422667920 (ahí siguen más dígitos).
_RE_ISO = re.compile(r"[A-Z]{3}[UJZ]\d{7}(?!\d)")
_RE_ISO_EXACTO = re.compile(r"[A-Z]{3}[UJZ]\d{7}")

# Referencias que no son ISO: el código que usa el ORIGINAL (y por eso costos).
_REFERENCIAS = (
    re.compile(r"(?<!\d)\d{3}F\d{6}(?!\d)"),         # 027F655823
    re.compile(r"SZ(?:LS|PM)\d{8}(?!\d)"),            # SZLS50216600
    re.compile(r"ONE[UY][A-Z0-9]{8,12}"),             # ONEYNB5BEK841700
    re.compile(r"COSU\d{10}(?!\d)"),                  # COSU6422667920
    re.compile(r"(?<![A-Z])[A-Z]{3}\d{2}-\d{3}(?!\d)"),  # PRY25-543, LEX25-510
    re.compile(r"(?<![A-Z0-9])\d{9,12}(?!\d)"),       # guías: 256059868, 149504230930
)

# «Código reconocido» para aceptar un sufijo «-N» PEGADO en costos
# (COSU6422667920-29 sí es base+N; PRY25-543 NO: es una referencia entera).
_RECONOCIDOS = (
    _RE_ISO_EXACTO,
    re.compile(r"\d{9,12}"),
    re.compile(r"\d{3}F\d{6}"),
    re.compile(r"SZ(?:LS|PM)\d{8}"),
    re.compile(r"ONE[UY][A-Z0-9]{8,12}"),
    re.compile(r"COSU\d{10}"),
)

_RE_NUMERO = re.compile(r"(?:CONTENEDOR|CONT\.?)\s*(\d{1,4})(?!\d)")
_RE_SUFIJO_ESPACIADO = re.compile(r"^(.*?)\s+-\s+(\d{1,4})$")
_RE_SUFIJO_PEGADO = re.compile(r"^(.*?)-(\d{1,4})$")


def _valores_letra() -> dict[str, int]:
    # A=10, B=12, … Z=38: se saltan los múltiplos de 11 (11, 22, 33).
    salida, v = {}, 10
    for letra in string.ascii_uppercase:
        if v % 11 == 0:
            v += 1
        salida[letra] = v
        v += 1
    return salida


_VALOR_LETRA = _valores_letra()


def normalizar(texto: str | None) -> str:
    """Mayúsculas y `_` → espacio (los nombres de Drive traen de los dos)."""
    return (texto or "").upper().replace("_", " ")


def _compacto(texto: str | None) -> str:
    return re.sub(r"\s+", "", normalizar(texto))


def _codigo_limpio(texto: str | None) -> str:
    return " ".join(normalizar(texto).split())


def iso_valido(codigo: str) -> bool:
    """Dígito verificador ISO 6346: suma valor·2^i (i=0..9), mod 11 mod 10."""
    codigo = (codigo or "").upper()
    if not _RE_ISO_EXACTO.fullmatch(codigo):
        return False
    suma = sum((_VALOR_LETRA[c] if c.isalpha() else int(c)) * (2 ** i)
               for i, c in enumerate(codigo[:10]))
    return suma % 11 % 10 == int(codigo[10])


def _iso_de(texto: str, m: re.Match) -> tuple[str, bool]:
    """
    El ISO que de verdad dice un match de 4 letras, y si valida.

    Si la letra de antes también es letra, el prefijo trae 5 (OOULU9155398,
    PHPCU4654366: un dedazo en el nombre del archivo). Se prueba quitando UNA de
    las 4 primeras hasta que valide; quitar la primera es el match mismo.
    """
    iso = m.group(0)
    if iso_valido(iso):
        return iso, True
    ini = m.start()
    if ini > 0 and "A" <= texto[ini - 1] <= "Z":
        cinco = texto[ini - 1:m.end()]
        for i in range(1, 4):
            candidato = cinco[:i] + cinco[i + 1:]
            if iso_valido(candidato):
                return candidato, True
    return iso, False


def codigos(texto: str | None) -> list[str]:
    """
    Los códigos de contenedor de un texto: ISO válidos primero, luego ISO que
    no validan, luego referencias (guía, BL, 027F, SZLS, ONE*, COSU, PRY25-543);
    sin duplicados y en orden de aparición dentro de cada bloque.
    """
    t = normalizar(texto)
    validos: list[str] = []
    invalidos: list[str] = []
    for m in _RE_ISO.finditer(t):
        iso, ok = _iso_de(t, m)
        (validos if ok else invalidos).append(iso)
    refs = sorted(((m.start(), m.group(0)) for rx in _REFERENCIAS for m in rx.finditer(t)),
                  key=lambda x: x[0])
    salida: list[str] = []
    for c in validos + invalidos + [r for _, r in refs]:
        if c not in salida:
            salida.append(c)
    return salida


def numero(texto: str | None) -> int | None:
    """El número de contenedor de Kubera: «… contenedor 80.xlsx», «Cont 95 …»."""
    m = _RE_NUMERO.search(normalizar(texto))
    return int(m.group(1)) if m else None


def es_codigo_reconocido(texto: str | None) -> bool:
    t = _codigo_limpio(texto)
    return any(rx.fullmatch(t) for rx in _RECONOCIDOS)


def separar_valor_costos(valor: str | None) -> tuple[str, int | None]:
    """
    `costos_validados.contenedor` → (base, N).

    « - N» con espacios siempre es sufijo. «-N» pegado solo si la base es un
    código reconocido: COSU6422667920-29 → (COSU6422667920, 29), pero
    PRY25-543 y LEX25-510 son referencias enteras (su N sale del Ferraforme).
    """
    v = (valor or "").strip()
    m = _RE_SUFIJO_ESPACIADO.match(v)
    if m:
        return m.group(1).strip(), int(m.group(2))
    m = _RE_SUFIJO_PEGADO.match(v)
    if m and es_codigo_reconocido(m.group(1)):
        return m.group(1).strip(), int(m.group(2))
    return v, None


# ── Agrupación en embarques (puro) ────────────────────────────────────────────

@dataclass
class _Acumulado:
    clave: str
    numero: int | None
    # código → [iso válido, peso (nº de SKUs de la fuente), orden de aparición]
    codigos: dict[str, list] = field(default_factory=dict)
    valores_costos: list[str] = field(default_factory=list)
    shas: list[str] = field(default_factory=list)

    def sumar_codigo(self, codigo: str, peso: int, orden: int) -> None:
        if not codigo:
            return
        if codigo in self.codigos:
            self.codigos[codigo][1] += peso
        else:
            self.codigos[codigo] = [iso_valido(codigo), peso, orden]


def _heredar_numero(base: str, fichas: list[dict]) -> int | None:
    """
    N de un valor de costos que no lo trae (PRY25-543 → 75), sacado de los
    Ferraforme que lo mencionan: el texto compacto lo CONTIENE (solo con 8+
    caracteres, para no pegarle a cualquier número corto) o sus códigos lo
    incluyen. Solo si TODOS dan la misma N; si discrepan, no se adivina.

    Caso conocido que NO hereda: «ONEU10522791» (29 SKUs en prod) parece el
    nombre del original «ONEU1052279发票(1).xlsx» sin lo que no es letra ni
    dígito, pero el texto compacto conserva «发票(» y la base no es código del
    Ferraforme «… contenedor 35»; ninguno de esos SKUs está en el packing.
    Queda como opción aparte (c:ONEU10522791) hasta que Eduardo decida.
    """
    compacta = re.sub(r"\s+", "", base)
    ns: set[int | None] = set()
    for fi in fichas:
        if (len(compacta) >= 8 and compacta in fi["compacto"]) or base in fi["codigos"]:
            ns.add(fi["numero"])
    if len(ns) == 1:
        return next(iter(ns))
    return None


def agrupar(ferraformes: list[dict], valores: list[dict]) -> list[dict]:
    """
    Junta las dos fuentes en embarques.

    `ferraformes`: [{sha, nombre, miembro, originales: [texto], skus}] — cada
    Ferraforme presente en packing_ubicaciones, con los nombres de sus
    originales emparejados.
    `valores`: [{valor, skus}] — cada `costos_validados.contenedor` distinto.

    Clave: `n:{N}` si hay N; si no, `c:{código}` (costos: su base; Ferraforme
    sin N: su primer código). Orden: con N de MAYOR a menor (lo más nuevo
    arriba), luego los sin N en orden alfabético.
    """
    grupos: dict[str, _Acumulado] = {}
    orden = 0

    def _grupo(clave: str, n: int | None) -> _Acumulado:
        if clave not in grupos:
            grupos[clave] = _Acumulado(clave, n)
        return grupos[clave]

    fichas: list[dict] = []
    for f in sorted(ferraformes, key=lambda x: (x.get("nombre") or "", x.get("sha") or "")):
        nombre, miembro = f.get("nombre") or "", f.get("miembro") or ""
        originales = [o for o in (f.get("originales") or []) if o]
        n = numero(nombre)
        if n is None:
            n = numero(miembro)
        cods = codigos(f"{nombre} {miembro}")
        for o in originales:
            cods += [c for c in codigos(o) if c not in cods]
        if n is not None:
            clave = f"n:{n}"
        elif cods:
            clave = f"c:{cods[0]}"
        else:
            # Ni N ni código: el nombre del archivo es lo único que lo nombra.
            clave = f"c:{_codigo_limpio(nombre) or (f.get('sha') or '')[:12]}"
        g = _grupo(clave, n)
        if f.get("sha") and f["sha"] not in g.shas:
            g.shas.append(f["sha"])
        for c in cods:
            orden += 1
            g.sumar_codigo(c, int(f.get("skus") or 0), orden)
        if not cods and n is None:
            orden += 1
            g.sumar_codigo(clave[2:], int(f.get("skus") or 0), orden)
        fichas.append({"numero": n, "codigos": set(cods),
                       "compacto": _compacto(" ".join([nombre, miembro, *originales]))})

    for v in sorted(valores, key=lambda x: x.get("valor") or ""):
        crudo = v.get("valor") or ""
        if not crudo.strip():
            continue
        base, n = separar_valor_costos(crudo)
        base = _codigo_limpio(base)
        if n is None and base:
            n = _heredar_numero(base, fichas)
        clave = f"n:{n}" if n is not None else f"c:{base}"
        g = _grupo(clave, n)
        if crudo not in g.valores_costos:
            g.valores_costos.append(crudo)
        # La base entra como código solo si ES un código; si no, lo que traiga
        # dentro (OOULU9155398 → OOLU9155398). Basura como
        # «INHERIT(ACC-0703-CAF)» no aporta ninguno: se queda en
        # `valores_costos` (el filtro la necesita) pero no en la etiqueta.
        for c in ([base] if es_codigo_reconocido(base) else codigos(base)):
            orden += 1
            g.sumar_codigo(c, int(v.get("skus") or 0), orden)

    salida = []
    for g in grupos.values():
        cods = sorted(g.codigos, key=lambda c: (not g.codigos[c][0], -g.codigos[c][1],
                                                g.codigos[c][2]))
        partes = ([str(g.numero)] if g.numero is not None else []) + cods[:2]
        fuentes = (["costos"] if g.valores_costos else []) + (["packing"] if g.shas else [])
        salida.append({
            "clave": g.clave,
            "etiqueta": " · ".join(partes) or g.clave[2:],
            "numero": g.numero,
            "codigos": cods,
            "valores_costos": sorted(g.valores_costos),
            "shas": sorted(g.shas),
            "fuentes": fuentes,
        })
    con_n = sorted((s for s in salida if s["numero"] is not None),
                   key=lambda s: -s["numero"])
    sin_n = sorted((s for s in salida if s["numero"] is None),
                   key=lambda s: s["clave"].lower())
    return con_n + sin_n


def etiqueta_tabla(numero: int, codigo: str | None) -> str:
    """Etiqueta de una N que solo conoce costing.sku_contenedor: «80 · TGHU6894814»."""
    return " · ".join([str(numero)] + ([codigo] if codigo else []))


def sumar_tabla(grupos: list[dict], numeros: dict[int, str | None]) -> list[dict]:
    """
    Los grupos de `agrupar` con costing.sku_contenedor (0060) como TERCERA fuente
    (flag LEER_SKU_CONTENEDOR). `numeros` = ``{N: código principal}`` de la tabla.

      - Un grupo `n:N` cuya N está en la tabla gana "tabla" en `fuentes`.
      - Una N de la tabla sin grupo (ningún packing list ni costos la nombra)
        crea su opción, con el código de la tabla.

    No toca los grupos recibidos (vienen de la caché): devuelve copias. Mismo
    orden que `agrupar`: con N de mayor a menor, luego los sin N.
    """
    if not numeros:
        return grupos
    salida, vistos = [], set()
    for g in grupos:
        n = g.get("numero")
        if n is not None and n in numeros:
            g = {**g, "fuentes": [*g.get("fuentes", []), "tabla"]}
            vistos.add(n)
        salida.append(g)
    for n, codigo in numeros.items():
        if n in vistos:
            continue
        salida.append({
            "clave": f"n:{n}",
            "etiqueta": etiqueta_tabla(n, codigo),
            "numero": n,
            "codigos": [codigo] if codigo else [],
            "valores_costos": [],
            "shas": [],
            "fuentes": ["tabla"],
        })
    con_n = sorted((s for s in salida if s["numero"] is not None), key=lambda s: -s["numero"])
    sin_n = [s for s in salida if s["numero"] is None]
    return con_n + sin_n


@dataclass(frozen=True)
class Agrupacion:
    grupos: list[dict]
    por_clave: dict[str, dict]
    por_valor: dict[str, str]      # contenedor crudo de costos → clave
    por_sha: dict[str, str]        # ferraforme_sha256 → clave
    generado: float                # time.monotonic() de cuando se armó


def indexar(grupos: list[dict], generado: float = 0.0) -> Agrupacion:
    por_valor, por_sha = {}, {}
    for g in grupos:
        for v in g["valores_costos"]:
            por_valor[v] = g["clave"]
        for s in g["shas"]:
            por_sha[s] = g["clave"]
    return Agrupacion(grupos, {g["clave"]: g for g in grupos}, por_valor, por_sha, generado)


# ── Lectura (kubera) ──────────────────────────────────────────────────────────

def leer_fuentes() -> tuple[list[dict], list[dict]]:
    """
    (ferraformes, valores) para `agrupar`. Tres consultas, ninguna por fila.

    Se cuentan TODAS las filas de packing_ubicaciones, alineadas o no: el
    Ferraforme afirma el contenedor aunque el renglón no se haya cotejado.
    """
    # `skus` es solo el PESO para ordenar códigos en la etiqueta. Se cuenta
    # sobre `lower(sku::text) collate "C"` —las mismas clases que citext— en vez
    # de `distinct u.sku`: el sort sobre citext compara con la colación ICU y a
    # volumen de prod (20k renglones) costaba ~270 ms contra ~48 ms.
    ubic = sdb.fetch_all(
        """select u.ferraforme_sha256 as sha,
                  count(distinct lower(u.sku::text) collate "C") as skus,
                  array_remove(array_agg(distinct u.original_sha256), null) as originales
             from costing.packing_ubicaciones u
            group by u.ferraforme_sha256""")
    shas = sorted({u["sha"] for u in ubic}
                  | {o for u in ubic for o in (u.get("originales") or []) if o})
    archivos = sdb.fetch_all(
        """select a.tipo, a.sha256 as sha, a.nombre, a.miembro
             from costing.packing_archivos a
            where a.sha256 = any(%s::text[])
            order by a.id""", (shas,)) if shas else []
    # La MISMA huella puede estar en varias filas (otra versión del mismo
    # contenido, otro archivo de Drive): gana la última copiada.
    nombres: dict[tuple[str, str], tuple[str, str | None]] = {}
    for a in archivos:
        nombres[(a["tipo"], a["sha"])] = (a.get("nombre") or "", a.get("miembro"))
    ferraformes = []
    for u in ubic:
        nombre, miembro = nombres.get(("ferraforme", u["sha"]), ("", None))
        originales = []
        for o in u.get("originales") or []:
            on, om = nombres.get(("original", o), ("", None))
            originales.append(" ".join(x for x in (on, om) if x))
        ferraformes.append({"sha": u["sha"], "nombre": nombre, "miembro": miembro,
                            "originales": originales, "skus": int(u.get("skus") or 0)})
    valores = [{"valor": r["valor"], "skus": int(r.get("skus") or 0)}
               for r in sdb.fetch_all(
                   """select contenedor as valor, count(*) as skus
                        from costing.costos_validados
                       where contenedor is not null and contenedor <> ''
                       group by contenedor""")]
    return ferraformes, valores


# Caché en proceso: el listado pide la agrupación en cada página y la
# agrupación no cambia de un clic a otro. 30 s y un candado (dos pedidos a la
# vez no la arman dos veces). `/costos/_embarques` siempre la rehace.
_TTL_S = 30.0
_candado = threading.Lock()
_cache: Agrupacion | None = None


def agrupacion(forzar: bool = False) -> Agrupacion:
    global _cache
    with _candado:
        ahora = time.monotonic()
        if not forzar and _cache is not None and ahora - _cache.generado < _TTL_S:
            return _cache
        ferraformes, valores = leer_fuentes()
        _cache = indexar(agrupar(ferraformes, valores), ahora)
        return _cache


def limpiar_cache() -> None:
    global _cache
    with _candado:
        _cache = None
