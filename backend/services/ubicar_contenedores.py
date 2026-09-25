"""
ubicar_contenedores.py — En qué contenedor(es) de Kubera llegó cada SKU: la
lógica PURA de la carga de `costing.sku_contenedor` (migración 0060).

Por qué existe: `costing.costos_validados.contenedor` guarda UNA sola N por SKU
y ≥140 SKUs llegaron legítimamente en dos contenedores (reórdenes, «-EST»);
además esa columna la lee Costos y la escribe «Validar publicados», así que no
se toca. La tabla nueva guarda (sku, N) con nivel, fuentes y evidencia. Su
ÚNICO escritor es `scripts/ubicar_skus_contenedor.py`; aquí vive lo que decide,
sin BD ni red (lo fijan las pruebas de `tests/test_ubicar_contenedores.py`).

Reglas PORTADAS del análisis del 25-sep (construir / fusionar / finalizar /
odoo_analisis / odoo_oc), con las correcciones del crítico que aprobó Eduardo.

── FAMILIAS INDEPENDIENTES ───────────────────────────────────────────────────
  F  Ferraforme: el renglón de `costing.packing_ubicaciones`. La N sale del
     NOMBRE del archivo (`embarques.numero`). `alineado` = el renglón quedó
     cotejado 1:1 con el original: es la evidencia de más calidad.
  B  Linaje de Brandon: `costos_validados.contenedor` (+ `caja_compartida`).
     Es UN solo linaje: el Drive CONTENEDORES_ACTUALIZADOS es su origen, por
     eso no se lee Drive (en el análisis aportaba ~7 SKUs nuevos).
  O  Odoo: `product.template.container_numbers` con código Y número.
     EXCEPCIÓN DELIBERADA (del análisis): «código(serie_arrastrada)», un texto
     con código y número donde el número se arrastró en la hoja (el código trae
     ≥5 N distintas en Odoo). Ahí manda el código, como en el pelón, pero
     cuenta como O porque alguien SÍ escribió el embarque junto al SKU. Medido
     el 25-sep contra las otras familias del mismo SKU: la serie concuerda
     99.6 % con Ferraforme (246/247) y 100 % con costos (117/117), como el
     campo con número (97.8 % / 96.2 %); el pelón, 95.0 % y 51.4 %.
  —  El «pelón» (el mismo campo con SOLO código) es evidencia DÉBIL: su N sale
     de un mapa código→N y se arrastra en series. No cuenta como familia.
  —  La OC recibida (renglón de purchase.order.line con qty_received > 0) NO es
     familia: su N casi siempre se infirió de F u O (mayoría de sus SKUs). Solo
     apoya, y respalda un «multi» cuando DOCUMENTA esa N para ese SKU
     (`oc_documenta`): su N no salió de la mayoría del campo de Odoo, el
     Ferraforme de esa N no la contradice y no es el mismo recibo que otra OC
     de otra N (la conversión «De consumibles a almacenables» vuelve a recibir
     lo mismo).

── NIVELES (por fila sku, N) ─────────────────────────────────────────────────
  A  Ferraforme alineado en esa N, o ≥2 familias coinciden en esa N.
  B  Una sola familia fuerte (F no alineado, B sola, O con número). Dentro de
     un multi, también la N cuyo único documento es una OC que la documenta
     (sin familia): el SKU tiene familia en otra de sus N.
  multi  El SKU nombra varias N y CADA una tiene documento primario de esa N
         (Ferraforme, costos u OC recibida que la documenta): se cargan todas
         con multi = true. Si ninguna N tiene familia (solo OC) es «debil».
  Toda fila cuya N tiene Ferraforme-documento que NO trae el SKU lleva
  `evidencia.contradicho_por` (costos sola se carga igual: el spec la tiene
  por fuerte; así se puede filtrar).

── EXCLUIDOS (no se cargan; van a la lista de Brandon) ─────────────────────
  conflicto          N distintas y alguna sin documento primario. Casos
                     conocidos: el lote del 12 capturado como 34 en Odoo,
                     «OOLU9155398 - cont 98» (es el 94).
  refutado           Odoo es la única familia, su N tiene Ferraforme y ese
                     Ferraforme no trae el SKU. Sin excepción por OC: la OC
                     de esa N tampoco la documenta (el Ferraforme la contradice).
  debil              Solo pelón y/o OC: ninguna familia fuerte.
  provisional        Identificador «NNNN-NNNN» (por homologar).
  padre_woo          Padre de variaciones en Woo sin evidencia propia.
  fuera_de_catalogo  El SKU no está en core.products.
  sin_evidencia      Nadie lo ubica.
"""
from __future__ import annotations

import collections
import json
import re
import unicodedata
from typing import Any, Iterable
from urllib.parse import urlsplit

from services import embarques as emb

# ── Constantes ────────────────────────────────────────────────────────────────

ORIGEN_CARGA_INICIAL = "carga_inicial_2026-09"

# Las únicas etiquetas que acepta el check de la 0060.
FUENTES_BD = ("ferraforme", "costos", "odoo_campo", "odoo_oc")

MOTIVOS = ("conflicto", "refutado", "debil", "provisional", "padre_woo",
           "fuera_de_catalogo", "sin_evidencia")

# 6,252 en prod (25-sep). Mismo patrón que el análisis: 3 a 5 dígitos por lado
# y un sufijo opcional («0031-0001», «4814-0001-A»).
RE_PROVISIONAL = re.compile(r"^\d{3,5}-\d{3,5}(-.+)?$")

# Un Ferraforme «existe» para refutar cuando trae al menos tantos SKUs (el
# umbral del análisis: por debajo es un índice parcial, no el documento).
MIN_SKUS_DOCUMENTO = 20

# Fuentes internas (más finas que las de la BD).
FERRA_ALINEADO = "ferra_al"
FERRA_NO_ALINEADO = "ferra_na"
COSTOS = "costos"
CAJA = "caja"
ODOO_CAMPO = "odoo_campo"
ODOO_PELON = "odoo_pelon"
ODOO_AMBIGUO = "odoo_ambiguo"
OC_RECIBIDA = "oc_rec"

ORDEN_FUENTES = (FERRA_ALINEADO, FERRA_NO_ALINEADO, COSTOS, CAJA, ODOO_CAMPO,
                 ODOO_PELON, ODOO_AMBIGUO, OC_RECIBIDA)

FAMILIA = {FERRA_ALINEADO: "F", FERRA_NO_ALINEADO: "F", COSTOS: "B", CAJA: "B",
           ODOO_CAMPO: "O"}

# Documentos «de esa N»: los que admiten que un SKU esté en varias. La caja
# compartida es del linaje de Brandon (familia B) pero NO es documento: solo
# registra con quién comparte caja (en el análisis era evidencia débil). La OC
# recibida solo cuenta cuando `oc_documenta` lo dice.
PRIMARIAS = frozenset({FERRA_ALINEADO, FERRA_NO_ALINEADO, COSTOS, OC_RECIBIDA})

A_BD = {FERRA_ALINEADO: "ferraforme", FERRA_NO_ALINEADO: "ferraforme",
        COSTOS: "costos", CAJA: "costos", ODOO_CAMPO: "odoo_campo",
        OC_RECIBIDA: "odoo_oc"}

# Llave de la evidencia (jsonb) por fuente interna.
LLAVE_EVIDENCIA = {FERRA_ALINEADO: "ferraforme", FERRA_NO_ALINEADO: "ferraforme",
                   COSTOS: "costos", CAJA: "caja_compartida", ODOO_CAMPO: "odoo_campo",
                   ODOO_PELON: "odoo_pelon", ODOO_AMBIGUO: "odoo_ambiguo",
                   OC_RECIBIDA: "odoo_oc"}

VIA_OC_HEREDADA_DE_ODOO = "mayoria_campo_odoo"
# Vías cuya N sale (también) de la mayoría del campo de Odoo de los SKUs de la
# OC: no documentan a ningún SKU en particular (`indep_del_campo` del análisis).
VIAS_OC_DEL_CAMPO = (VIA_OC_HEREDADA_DE_ODOO, "mayoria_ferraforme+campo")
MAX_REFS = 5          # referencias por fuente en la evidencia (hay SKUs con decenas)

REF_SANDBOX = "yvootpbz"
REF_PRODUCCION = "tukwcvsi"

# El cargador solo reemplaza orígenes suyos: «manual» (y cualquier otro que no
# empiece así) es de personas y no se borra nunca.
PREFIJO_ORIGEN = "carga_"


# ── Utilidades ────────────────────────────────────────────────────────────────

def clave(sku: Any) -> str:
    """La llave de un SKU: citext compara sin mayúsculas, aquí igual."""
    return str(sku or "").strip().upper()


def es_provisional(sku: Any) -> bool:
    return bool(RE_PROVISIONAL.match(clave(sku)))


def _id_odoo(valor: Any) -> Any:
    """Odoo devuelve many2one como [id, nombre] y vacío como False."""
    if isinstance(valor, (list, tuple)):
        return valor[0] if valor else None
    return valor or None


def _txt(valor: Any) -> str:
    """Texto de Odoo/BD: False y None son vacío."""
    if valor is None or valor is False:
        return ""
    return str(valor)


def _limpio(texto: Any) -> str:
    return " ".join(_txt(texto).replace("\xa0", " ").split())


def es_alineado(valor: Any) -> bool:
    return valor is True or str(valor).strip().lower() in ("true", "t", "1")


def _n_valido(n: Any) -> int | None:
    """Una N de contenedor sirve si es > 0. «… - 0» o «Contenedor 0» es texto
    malo: se trata como «sin N» (el check de la 0060 tumbaría TODA la carga)."""
    if n is None:
        return None
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


_RE_USUARIO_REF = re.compile(r"postgres\.([a-z0-9]+)")
_RE_HOST_REF = re.compile(r"db\.([a-z0-9]+)\.supabase\.co")


def ref_de(dsn: str) -> str:
    """
    La ref del proyecto de Supabase SEGÚN EL USUARIO («postgres.<ref>», el del
    pooler) o el HOST directo («db.<ref>.supabase.co»); '' si no la trae. No
    busca la ref en cualquier parte del DSN: una contraseña o un parámetro
    podrían traerla sin que el destino sea ese proyecto.
    """
    dsn = (dsn or "").strip()
    usuario = host = ""
    if "://" in dsn:
        try:
            partes = urlsplit(dsn)
            usuario, host = partes.username or "", partes.hostname or ""
        except ValueError:
            return ""
    else:                                   # «host=… user=… password=…»
        kv = {k: v.strip("'") for k, v in re.findall(r"(\w+)\s*=\s*('[^']*'|\S+)", dsn)}
        usuario, host = kv.get("user", ""), kv.get("host", "")
    m = _RE_USUARIO_REF.fullmatch(usuario.lower())
    if m:
        return m.group(1)
    m = _RE_HOST_REF.fullmatch(host.lower())
    return m.group(1) if m else ""


def validar_destino(dsn: str) -> str:
    """
    Candado de escritura: SOLO el sandbox. Producción queda bloqueada en el
    código (se habilitará en otro cambio, con el doble candado). La ref se lee
    del usuario o del host (`ref_de`), no de cualquier parte del DSN; y si
    «tukwcvsi» aparece en CUALQUIER parte, se rechaza igual. Devuelve la ref del
    destino; nunca el DSN.
    """
    dsn = dsn or ""
    if REF_PRODUCCION in dsn:
        raise PermissionError("destino = PRODUCCIÓN (tukwcvsi): este cargador no escribe ahí")
    if not ref_de(dsn).startswith(REF_SANDBOX):
        raise PermissionError("el destino no es el sandbox (yvootpbz)")
    return REF_SANDBOX


def validar_origen(origen: Any) -> str:
    """El cargador solo borra y escribe orígenes «carga_…»: «manual» y los
    demás son de personas y no se tocan."""
    o = _txt(origen).strip()
    if not o.startswith(PREFIJO_ORIGEN) or len(o) == len(PREFIJO_ORIGEN):
        raise ValueError(f"origen {o!r} no es de este cargador: debe empezar con «{PREFIJO_ORIGEN}» "
                         "(«manual» y los demás son de personas)")
    return o


def _palabras(texto: Any) -> set[str]:
    t = unicodedata.normalize("NFKD", _txt(texto).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {p for p in re.findall(r"[a-z0-9]+", t) if len(p) >= 3}


def palabras_en_comun(a: Any, b: Any) -> int:
    """Palabras (≥3 letras, sin acentos) que comparten dos nombres. 0 con dos
    nombres no vacíos es la señal de una colisión de SKU en Odoo («Monitor
    curvo gamer» contra «Motor»); con traducciones da falsos positivos, por eso
    solo se reporta."""
    return len(_palabras(a) & _palabras(b))


# ── Archivos y códigos ────────────────────────────────────────────────────────

def nombres_por_huella(archivos: Iterable[dict]) -> dict[tuple[str, str], tuple[str, str | None]]:
    """(tipo, sha256) → (nombre, miembro). La misma huella puede estar en varias
    filas (otra versión, otro archivo de Drive): gana la de mayor id."""
    salida: dict[tuple[str, str], tuple[str, str | None]] = {}
    for a in sorted(archivos, key=lambda a: int(a.get("id") or 0)):
        salida[(a.get("tipo") or "", a.get("sha256") or "")] = (a.get("nombre") or "",
                                                               a.get("miembro") or None)
    return salida


def numero_de_archivo(nombre: Any, miembro: Any = None) -> int | None:
    """La N del NOMBRE del archivo; si no la trae, la del miembro del zip."""
    n = _n_valido(emb.numero(_txt(nombre)))
    return n if n is not None else _n_valido(emb.numero(_txt(miembro)))


def fuentes_de_embarques(ubicaciones: Iterable[dict], archivos: Iterable[dict],
                         costos: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """(ferraformes, valores) para `embarques.agrupar`, armados desde filas
    (lo mismo que `embarques.leer_fuentes` hace con SQL)."""
    archivos = list(archivos)
    nombres = nombres_por_huella(archivos)
    por_ferra: dict[str, dict] = collections.defaultdict(lambda: {"skus": set(), "orig": set()})
    for u in ubicaciones:
        g = por_ferra[u.get("ferraforme_sha256") or ""]
        g["skus"].add(clave(u.get("sku")))
        if u.get("original_sha256"):
            g["orig"].add(u["original_sha256"])
    ferraformes = []
    for sha, g in por_ferra.items():
        nombre, miembro = nombres.get(("ferraforme", sha), ("", None))
        originales = [" ".join(x for x in nombres.get(("original", o), ("", None)) if x)
                      for o in sorted(g["orig"])]
        ferraformes.append({"sha": sha, "nombre": nombre, "miembro": miembro,
                            "originales": originales, "skus": len(g["skus"])})
    cuenta = collections.Counter(c.get("contenedor") for c in costos
                                 if _txt(c.get("contenedor")).strip())
    valores = [{"valor": v, "skus": n} for v, n in cuenta.items()]
    return ferraformes, valores


def mapa_codigos(grupos: Iterable[dict], archivos: Iterable[dict]) -> dict[str, set[int]]:
    """código → {N}: de los embarques con N (Ferraforme + costos) y de cualquier
    archivo de packing que traiga la N en el nombre."""
    mapa: dict[str, set[int]] = collections.defaultdict(set)
    for g in grupos:
        if _n_valido(g.get("numero")) is not None:
            for c in g.get("codigos") or ():
                mapa[c].add(int(g["numero"]))
    for a in archivos:
        n = numero_de_archivo(a.get("nombre"), a.get("miembro"))
        if n is not None:
            for c in emb.codigos(f"{_txt(a.get('nombre'))} {_txt(a.get('miembro'))}"):
                mapa[c].add(n)
    return dict(mapa)


def n_por_codigos(texto: Any, mapa: dict[str, set[int]]) -> tuple[int | None, str]:
    """La N ÚNICA a la que apuntan los códigos del texto; (n, método)."""
    cs = emb.codigos(_txt(texto))
    ns: set[int] = set()
    for c in cs:
        ns |= mapa.get(c, set())
    conocidos = [c for c in cs if c in mapa]
    if len(ns) == 1:
        return next(iter(ns)), "codigo:" + ",".join(conocidos)
    if len(ns) > 1:
        return None, "ambiguo:" + ",".join(f"{c}->{sorted(mapa[c])}" for c in conocidos)
    return None, "sin_codigo_conocido:" + ",".join(cs)


def numero_por_valor(grupos: Iterable[dict]) -> dict[str, int]:
    """Valor crudo de costos → N de su embarque (la herencia de PRY25-543 → 75)."""
    salida = {}
    for g in grupos:
        if _n_valido(g.get("numero")) is not None:
            for v in g.get("valores_costos") or ():
                salida[v] = int(g["numero"])
    return salida


def codigo_por_numero(grupos: Iterable[dict]) -> dict[int, str]:
    """N → código principal (el primero del embarque: ISO válido y más pesado)."""
    return {int(g["numero"]): g["codigos"][0] for g in grupos
            if _n_valido(g.get("numero")) is not None and g.get("codigos")}


# ── Evidencia de kubera ───────────────────────────────────────────────────────

def _ev(sku: Any, fuente: str, numero: int, ref: dict) -> dict:
    return {"sku": _txt(sku).strip(), "fuente": fuente, "numero": int(numero), "ref": ref}


def evidencia_ferraforme(ubicaciones: Iterable[dict], archivos: Iterable[dict],
                         mapa: dict[str, set[int]]) -> tuple[list[dict], list[dict]]:
    """Un renglón de Ferraforme → (sku, N del NOMBRE del archivo). Si el nombre
    no trae N, la de sus códigos (en prod, 25-sep: los 75 la traen)."""
    nombres = nombres_por_huella(archivos)
    n_de_sha: dict[str, tuple[int | None, str, str]] = {}
    salida, sin_n = [], []
    for u in ubicaciones:
        sha = u.get("ferraforme_sha256") or ""
        if sha not in n_de_sha:
            nombre, miembro = nombres.get(("ferraforme", sha), ("", None))
            n, via = numero_de_archivo(nombre, miembro), "nombre"
            if n is None:
                n, via = n_por_codigos(f"{nombre} {miembro or ''}", mapa)
            n_de_sha[sha] = (n, via, miembro or nombre)
        n, via, archivo = n_de_sha[sha]
        fuente = FERRA_ALINEADO if es_alineado(u.get("alineado")) else FERRA_NO_ALINEADO
        if n is None:
            sin_n.append({"sku": u.get("sku"), "fuente": fuente, "archivo": archivo, "via": via})
            continue
        ref = {"archivo": archivo, "fila": u.get("ferraforme_fila"),
               "alineado": fuente == FERRA_ALINEADO}
        if via != "nombre":
            ref["via"] = via
        salida.append(_ev(u.get("sku"), fuente, n, ref))
    return salida, sin_n


def es_basura_costos(valor: Any) -> bool:
    """«INHERIT(ACC-0703-CAF)»: texto de herencia de WooCommerce, no un embarque."""
    return "INHERIT(" in _txt(valor)


def evidencia_costos(costos: Iterable[dict], por_valor: dict[str, int],
                     mapa: dict[str, set[int]]) -> tuple[list[dict], list[dict]]:
    """`costos_validados.contenedor` → N: el sufijo « - N»; si no, la N que el
    embarque heredó de su Ferraforme; si no, la de sus códigos."""
    salida, sin_n = [], []
    for c in costos:
        crudo = c.get("contenedor")
        v = _txt(crudo).strip()
        if not v:
            continue
        if es_basura_costos(v):
            sin_n.append({"sku": c.get("sku"), "valor": v, "via": "basura_inherit"})
            continue
        base, n = emb.separar_valor_costos(v)
        n, via = _n_valido(n), "sufijo"
        if n is None:
            n, via = por_valor.get(crudo), "heredado_ferraforme"
        if n is None:
            n, via = n_por_codigos(base, mapa)
        if n is None:
            sin_n.append({"sku": c.get("sku"), "valor": v, "via": via})
            continue
        salida.append(_ev(c.get("sku"), COSTOS, n, {"valor": v, "via": via.split(":")[0]}))
    return salida, sin_n


def evidencia_caja(caja: Iterable[dict], mapa: dict[str, set[int]]) -> tuple[list[dict], list[dict]]:
    """`caja_compartida` → N del nombre del archivo, o de sus códigos."""
    salida, sin_n = [], []
    for c in caja:
        texto = f"{_txt(c.get('contenedor_base'))} {_txt(c.get('contenedor'))} {_txt(c.get('archivo'))}"
        n, via = _n_valido(emb.numero(_txt(c.get("archivo")))), "nombre_archivo"
        if n is None:
            n, via = n_por_codigos(texto, mapa)
        if n is None:
            sin_n.append({"sku": c.get("sku"), "texto": texto.strip(), "via": via})
            continue
        salida.append(_ev(c.get("sku"), CAJA, n,
                          {"contenedor": _txt(c.get("contenedor_base")) or _txt(c.get("contenedor")),
                           "archivo": _txt(c.get("archivo"))}))
    return salida, sin_n


# ── Odoo: el campo container_numbers ─────────────────────────────────────────

def ampliar_mapa_odoo(mapa: dict[str, set[int]], archivos: Iterable[dict],
                      textos_odoo: Iterable[Any]) -> tuple[dict[str, set[int]], set[str], dict[str, str]]:
    """
    El mapa código→N para leer Odoo: el de kubera, más
      · códigos que comparten nombre de archivo con uno ya mapeado
        («255835801=CI&PL MRSU7175563» → la N de MRSU7175563);
      · lo que Odoo mismo enseña: un código que en TODO Odoo aparece con UNA
        sola N explícita.
    `serie`: códigos que Odoo trae con ≥5 N distintas (texto arrastrado en una
    hoja); con esos, la N del texto no vale y manda el código.
    """
    archivos = list(archivos)
    m = {c: set(v) for c, v in mapa.items()}
    via: dict[str, str] = {}
    for _ in range(2):
        for a in archivos:
            cs = emb.codigos(f"{_txt(a.get('nombre'))} {_txt(a.get('miembro'))}")
            ns = set().union(*[m.get(c, set()) for c in cs]) if cs else set()
            if len(ns) == 1:
                for c in cs:
                    if c not in m:
                        m[c] = set(ns)
                        via[c] = "nombre_archivo"
    por_codigo: dict[str, set[int]] = collections.defaultdict(set)
    for t in textos_odoo:
        for parte in _txt(t).replace("\xa0", " ").split(","):
            n = _n_valido(emb.numero(parte))
            if n is None:
                continue
            for c in emb.codigos(parte):
                por_codigo[c].add(n)
    serie = {c for c, ns in por_codigo.items() if len(ns) >= 5}
    for c, ns in por_codigo.items():
        if len(ns) == 1 and c not in serie and c not in m:
            m[c] = set(ns)
            via[c] = "texto_odoo"
    return m, serie, via


def interpretar_odoo(valor: Any, mapa: dict[str, set[int]], serie: set[str],
                     ns_conocidos: set[int] | None = None) -> list[dict]:
    """
    El texto de `container_numbers` → una lectura por parte (separadas por coma):
    {numero, via, fuente, n_texto, n_codigo, codigos}. `fuente`:
      odoo_campo    trae número (con o sin código que lo confirme);
      odoo_pelon    SOLO código: la N sale del mapa → evidencia débil;
      odoo_ambiguo  el código dice una N y el texto otra («OOLU9155398 - cont 98»:
                    el código es del 94): se guardan las dos → conflicto;
      None          sin N (texto sin mapa o código ambiguo).
    """
    ns_conocidos = ns_conocidos or set()
    salida = []
    for parte in (p.strip() for p in _limpio(valor).split(",")):
        if not parte:
            continue
        n_txt = _n_valido(emb.numero(parte))
        if n_txt is None:
            n_txt = _n_valido(emb.separar_valor_costos(parte)[1])
        cods = emb.codigos(parte)
        n_cod = set().union(*[mapa.get(c, set()) for c in cods]) if cods else set()
        lec = {"numero": None, "via": "", "fuente": None, "n_texto": n_txt,
               "n_codigo": None, "codigos": cods, "texto": parte}
        if len(n_cod) == 1:
            nc = next(iter(n_cod))
            lec["n_codigo"] = nc
            if n_txt is None:
                lec.update(numero=nc, via="codigo", fuente=ODOO_PELON)
            elif n_txt == nc:
                lec.update(numero=nc, via="codigo+numero", fuente=ODOO_CAMPO)
            elif any(c in serie for c in cods):
                # Excepción deliberada: cuenta como O, no como pelón (ver el docstring del módulo).
                lec.update(numero=nc, via="codigo(serie_arrastrada)", fuente=ODOO_CAMPO)
            else:
                lec.update(via="conflicto", fuente=ODOO_AMBIGUO)
        elif len(n_cod) > 1:
            if n_txt in n_cod:
                lec.update(numero=n_txt, via="codigo_ambiguo+numero", fuente=ODOO_CAMPO)
            else:
                lec.update(via="codigo_ambiguo")
        elif n_txt is not None:
            lec.update(numero=n_txt, fuente=ODOO_CAMPO,
                       via="solo_numero" + ("" if n_txt in ns_conocidos else "(N_nuevo)"))
        else:
            lec.update(via="sin_mapa")
        salida.append(lec)
    return salida


def productos_odoo_por_sku(productos: Iterable[dict], texto_por_plantilla: dict[Any, Any]) -> dict[str, dict]:
    """
    SKU → {sku, activo, texto, nombre, productos}. El campo vive en la
    plantilla; el SKU en la variante. Si dos productos comparten SKU gana el
    activo con texto, y `productos` dice cuántos lo comparten (una colisión de
    SKU en Odoo le daría a este SKU el contenedor de OTRO producto).
    """
    info: dict[str, dict] = {}
    cuantos: collections.Counter = collections.Counter()
    for p in productos:
        sku = _txt(p.get("default_code")).strip()
        k = clave(sku)
        if not k:
            continue
        cuantos[k] += 1
        texto = _txt(texto_por_plantilla.get(_id_odoo(p.get("product_tmpl_id"))))
        activo = bool(p.get("active"))
        prev = info.get(k)
        if prev is not None and (prev["activo"], bool(prev["texto"])) >= (activo, bool(texto)):
            continue
        info[k] = {"sku": sku, "activo": activo, "texto": texto, "nombre": _limpio(p.get("name"))[:120]}
    for k, i in info.items():
        i["productos"] = cuantos[k]
    return info


def evidencia_odoo_campo(info_por_sku: dict[str, dict], mapa: dict[str, set[int]], serie: set[str],
                         ns_conocidos: set[int] | None = None) -> tuple[list[dict], dict[str, set[int]], collections.Counter]:
    """(evidencia, N que Odoo da a cada SKU —campo y pelón, para la mayoría de
    las OC—, conteo por vía)."""
    salida: list[dict] = []
    ns_por_sku: dict[str, set[int]] = {}
    vias: collections.Counter = collections.Counter()
    cache: dict[str, list[dict]] = {}
    for k, i in info_por_sku.items():
        if not i["texto"]:
            continue
        if i["texto"] not in cache:
            cache[i["texto"]] = interpretar_odoo(i["texto"], mapa, serie, ns_conocidos)
        texto = _limpio(i["texto"])[:120]
        ns: set[int] = set()
        base_ref = {"texto": texto, "activo": i["activo"]}
        if i.get("nombre"):
            base_ref["nombre_odoo"] = i["nombre"]      # para auditar colisiones de SKU
        if (i.get("productos") or 1) > 1:
            base_ref["productos_con_este_sku"] = i["productos"]
        for lec in cache[i["texto"]]:
            vias[lec["via"]] += 1
            ref = dict(base_ref, via=lec["via"])
            if lec["fuente"] == ODOO_AMBIGUO:
                for n in sorted({lec["n_codigo"], lec["n_texto"]} - {None}):
                    salida.append(_ev(i["sku"], ODOO_AMBIGUO, n,
                                      dict(ref, codigo_dice=lec["n_codigo"], texto_dice=lec["n_texto"])))
                continue
            if lec["numero"] is None:
                continue
            ns.add(lec["numero"])
            salida.append(_ev(i["sku"], lec["fuente"], lec["numero"], ref))
        if ns:
            ns_por_sku[k] = ns
    return salida, ns_por_sku, vias


# ── Odoo: órdenes de compra ───────────────────────────────────────────────────

RE_OC = re.compile(r"P0\d{4}")


def ocs_por_archivo(archivos: Iterable[dict]) -> dict[str, int]:
    """OC nombrada en un Ferraforme «Cont 95 TLLU8977270-P03087.xlsx» → 95."""
    salida = {}
    for a in archivos:
        if (a.get("tipo") or "") != "ferraforme":
            continue
        texto = f"{_txt(a.get('nombre'))} {_txt(a.get('miembro'))}"
        n = numero_de_archivo(a.get("nombre"), a.get("miembro"))
        if n is None:
            continue
        for po in RE_OC.findall(texto.upper()):
            salida[po] = n
    return salida


def n_de_texto_oc(texto: Any, mapa: dict[str, set[int]]) -> tuple[int | None, str]:
    """N de un texto de OC (referencia, origen, notas): «Contenedor 80»; si no,
    un sufijo sobre código reconocido; si no, un código con UNA N en el mapa."""
    t = _limpio(texto)
    if not t:
        return None, ""
    n = _n_valido(emb.numero(t))
    if n is not None:
        return n, "numero"
    for parte in re.split(r"[:,/]", t):
        base, ns = emb.separar_valor_costos(parte.strip())
        ns = _n_valido(ns)
        if ns is not None and emb.es_codigo_reconocido(base):
            return ns, "sufijo"
    for c in emb.codigos(t):
        if len(mapa.get(c, ())) == 1:
            return next(iter(mapa[c])), f"codigo:{c}"
    return None, ""


def _mayoria(cuenta: collections.Counter) -> tuple[int | None, int, float]:
    total = sum(cuenta.values())
    if not total:
        return None, 0, 0.0
    n, k = cuenta.most_common(1)[0]
    return n, k, k / total


def numeros_de_oc(ordenes: Iterable[dict], lineas: Iterable[dict], sku_por_producto: dict[Any, str],
                  ns_ferra_por_sku: dict[str, set[int]], ns_odoo_por_sku: dict[str, set[int]],
                  mapa: dict[str, set[int]], oc_archivo: dict[str, int]) -> dict[Any, dict]:
    """
    OC → N del contenedor (odoo_oc.py). Por orden de preferencia:
      1. el Ferraforme que nombra la OC («…-P03087» → 95);
      2. el texto: referencia/origen, «Contenedor: …» y «Complementa al
         contenedor: …» de las notas, o el arranque de las notas;
      3. la mayoría de sus SKUs según Ferraforme y/o según el campo de Odoo
         (≥ max(3, 30 %) de los SKUs y ≥60 % de los votos);
      4. «complementa» a otra OC que ya tiene N.
    Las OC duplicadas (misma N, mismos SKUs) se quedan con la que recibió más.
    """
    ordenes = sorted(ordenes, key=lambda p: int(p.get("id") or 0))
    por_nombre = {p.get("name"): p for p in ordenes}
    lin_por_oc: dict[Any, list[dict]] = collections.defaultdict(list)
    for l in lineas:
        if _id_odoo(l.get("product_id")) is not None:
            lin_por_oc[_id_odoo(l.get("order_id"))].append(l)
    res: dict[Any, dict] = {}
    for p in ordenes:
        pid = p.get("id")
        notas = " ".join(re.sub(r"<[^>]+>", " ", _txt(p.get("notes"))).split())
        encabezado = f"{_txt(p.get('partner_ref'))} | {_txt(p.get('origin'))}"
        m = re.search(r"Contenedor:\s*(.*?)\s*(?:SKUs|Complementa|$)", notas)
        notas_cont = m.group(1) if m else ""
        m2 = re.search(r"Complementa al contenedor:\s*(.*?)\s*SKUs", notas)
        notas_comp = m2.group(1) if m2 else ""
        hdr, via = None, ""
        if p.get("name") in oc_archivo:
            hdr, via = oc_archivo[p["name"]], "nombre_ferraforme"
        for texto, etiqueta in ((encabezado, "encabezado"), (notas_cont, "notas"),
                                (notas_comp, "notas_complemento"), (notas[:200], "notas")):
            if hdr is None:
                n, v = n_de_texto_oc(texto, mapa)
                if n is not None:
                    hdr, via = n, f"{etiqueta}:{v}"
        skus = sorted({sku_por_producto.get(_id_odoo(l.get("product_id"))) for l in lin_por_oc.get(pid, [])}
                      - {"", None})
        fv = collections.Counter(n for s in skus for n in sorted(ns_ferra_por_sku.get(s, ())))
        ov = collections.Counter(n for s in skus for n in sorted(ns_odoo_por_sku.get(s, ())))
        fn, fk, fs = _mayoria(fv)
        on, ok, os_ = _mayoria(ov)
        minimo = max(3, 0.3 * len(skus))
        f_ok = fn is not None and fk >= minimo and fs >= 0.6
        o_ok = on is not None and ok >= minimo and os_ >= 0.6
        dec, dvia = None, ""
        if hdr is not None:
            dec, dvia = hdr, via
        elif f_ok and o_ok and fn == on:
            dec, dvia = fn, f"mayoria_ferraforme+campo {fk}+{ok}/{len(skus)}"
        elif f_ok and (not o_ok or fk >= ok):
            dec, dvia = fn, f"mayoria_ferraforme {fk}/{len(skus)}" + (f" (campo_odoo_dice_{on})" if o_ok else "")
        elif o_ok:
            dec, dvia = on, f"{VIA_OC_HEREDADA_DE_ODOO} {ok}/{len(skus)}" + (f" (ferraforme_dice_{fn})" if f_ok else "")
        if dec is None:
            refs = RE_OC.findall(f"{encabezado} {notas}")
            ns = {res.get(por_nombre[r].get("id"), {}).get("n") for r in refs if r in por_nombre} - {None}
            if len(ns) == 1:
                dec, dvia = ns.pop(), "complementa:" + ",".join(refs)
        res[pid] = {"po": p.get("name"), "n": dec, "via": dvia, "estado": p.get("state"),
                    "skus": len(skus)}
    # Complementos que dependían de una OC posterior.
    for p in ordenes:
        x = res[p.get("id")]
        if x["n"] is None:
            notas = re.sub(r"<[^>]+>", " ", _txt(p.get("notes")))
            refs = RE_OC.findall(f"{_txt(p.get('origin'))} {notas}")
            ns = {res[por_nombre[r].get("id")]["n"] for r in refs if r in por_nombre} - {None}
            if len(ns) == 1:
                x["n"], x["via"] = ns.pop(), "complementa:" + ",".join(refs)
    # Duplicadas exactas: misma N y mismos SKUs → gana la que recibió más.
    firma: dict[tuple, tuple[Any, float]] = {}
    for p in ordenes:
        pid = p.get("id")
        x = res[pid]
        if x["n"] is None or x["estado"] not in ("purchase", "done"):
            continue
        ls = lin_por_oc.get(pid, [])
        llave = (x["n"], tuple(sorted({sku_por_producto.get(_id_odoo(l.get("product_id"))) or "" for l in ls})))
        rec = sum(float(l.get("qty_received") or 0) for l in ls)
        if llave in firma:
            otro, orec = firma[llave]
            perdedor, ganador = (pid, otro) if rec <= orec else (otro, pid)
            res[perdedor]["duplicada_de"] = res[ganador]["po"]
            if rec > orec:
                firma[llave] = (pid, rec)
        else:
            firma[llave] = (pid, rec)
    return res


def evidencia_oc(numeros: dict[Any, dict], lineas: Iterable[dict],
                 sku_por_producto: dict[Any, str]) -> tuple[list[dict], dict[str, int]]:
    """Renglones RECIBIDOS (qty_received > 0) de las OC usables → (sku, N)."""
    filas: dict[tuple, dict] = collections.defaultdict(lambda: {"pedido": 0.0, "recibido": 0.0})
    for l in lineas:
        pid = _id_odoo(l.get("order_id"))
        x = numeros.get(pid)
        if not x or x["n"] is None or x["estado"] not in ("purchase", "done") or "duplicada_de" in x:
            continue
        sku = sku_por_producto.get(_id_odoo(l.get("product_id")))
        if not sku:
            continue
        f = filas[(sku, x["n"], x["po"])]
        f["pedido"] += float(l.get("product_qty") or 0)
        f["recibido"] += float(l.get("qty_received") or 0)
        f["via"] = x["via"]
    salida, cuenta = [], collections.Counter()
    for (sku, n, po), f in sorted(filas.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")):
        if f["recibido"] > 0:
            cuenta["recibidos"] += 1
            salida.append(_ev(sku, OC_RECIBIDA, n, {"oc": po, "via": f["via"],
                                                     "pedido": f["pedido"], "recibido": f["recibido"]}))
        else:
            cuenta["sin_recibir"] += 1
    return salida, dict(cuenta)


# ── Armado de toda la evidencia ───────────────────────────────────────────────

def armar_evidencia(*, ubicaciones: list[dict], archivos: list[dict], costos: list[dict],
                    caja: list[dict], odoo: dict | None = None) -> tuple[list[dict], dict]:
    """
    Todas las fuentes → (evidencias, contexto). `odoo` (opcional) =
    {plantillas: [{id, container_numbers}], productos: [{id, default_code,
    product_tmpl_id, active}], ordenes: [...], lineas: [...]} tal como las da
    search_read. `contexto` trae lo que la clasificación y los reportes
    necesitan (grupos, N con Ferraforme, N de cada OC, conteos).
    """
    ferraformes, valores = fuentes_de_embarques(ubicaciones, archivos, costos)
    grupos = emb.agrupar(ferraformes, valores)
    mapa = mapa_codigos(grupos, archivos)
    ev_f, sin_f = evidencia_ferraforme(ubicaciones, archivos, mapa)
    ev_c, sin_c = evidencia_costos(costos, numero_por_valor(grupos), mapa)
    ev_k, sin_k = evidencia_caja(caja, mapa)
    evidencias = ev_f + ev_c + ev_k
    ctx: dict[str, Any] = {
        "grupos": grupos,
        "mapa_codigos": mapa,
        "codigo_por_numero": codigo_por_numero(grupos),
        "ns_con_ferraforme": numeros_con_ferraforme(ev_f),
        "sin_numero": {"ferraforme": sin_f, "costos": sin_c, "caja": sin_k},
        "conteos": {"ferraforme": len(ev_f), "costos": len(ev_c), "caja": len(ev_k)},
        "numeros_oc": {}, "vias_odoo": {},
    }
    if odoo:
        ns_conocidos = {int(g["numero"]) for g in grupos if _n_valido(g.get("numero")) is not None}
        textos = {_id_odoo(t.get("id")): t.get("container_numbers") for t in odoo.get("plantillas") or []}
        mapa_o, serie, aprendidos = ampliar_mapa_odoo(mapa, archivos, textos.values())
        info = productos_odoo_por_sku(odoo.get("productos") or [], textos)
        ev_o, ns_odoo, vias = evidencia_odoo_campo(info, mapa_o, serie, ns_conocidos)
        ns_ferra: dict[str, set[int]] = collections.defaultdict(set)
        for e in ev_f:
            ns_ferra[clave(e["sku"])].add(e["numero"])
        sku_por_producto = {p.get("id"): clave(p.get("default_code")) for p in odoo.get("productos") or []}
        numeros = numeros_de_oc(odoo.get("ordenes") or [], odoo.get("lineas") or [], sku_por_producto,
                                ns_ferra, ns_odoo, mapa_o, ocs_por_archivo(archivos))
        ev_r, cuenta_oc = evidencia_oc(numeros, odoo.get("lineas") or [], sku_por_producto)
        evidencias += ev_o + ev_r
        ctx.update(numeros_oc=numeros, vias_odoo=dict(vias), serie_odoo=sorted(serie),
                   codigos_aprendidos=aprendidos, oc_renglones=cuenta_oc)
        ctx["conteos"].update(odoo=len(ev_o), oc_recibida=len(ev_r))
    return evidencias, ctx


def numeros_con_ferraforme(ev_ferraforme: Iterable[dict], minimo: int = MIN_SKUS_DOCUMENTO) -> set[int]:
    """N cuyo Ferraforme existe como documento (≥ `minimo` SKUs indexados)."""
    por_n: dict[int, set[str]] = collections.defaultdict(set)
    for e in ev_ferraforme:
        por_n[e["numero"]].add(clave(e["sku"]))
    return {n for n, s in por_n.items() if len(s) >= minimo}


# ── Clasificación ─────────────────────────────────────────────────────────────

def familias(fuentes: dict[str, dict[int, list]], n: int) -> set[str]:
    """Familias independientes que dan la N (el pelón y la OC no cuentan)."""
    return {FAMILIA[f] for f, ns in fuentes.items() if f in FAMILIA and n in ns}


def contradicha(fuentes: dict[str, dict[int, list]], n: int, ns_con_ferraforme: set[int]) -> bool:
    """¿El Ferraforme de N existe como documento (≥ MIN_SKUS_DOCUMENTO) y NO
    trae el SKU? Es la misma evidencia negativa que refuta a Odoo sola."""
    return (n in ns_con_ferraforme and n not in fuentes.get(FERRA_ALINEADO, {})
            and n not in fuentes.get(FERRA_NO_ALINEADO, {}))


def _recibido(r: dict) -> float:
    try:
        return float(r.get("recibido") or 0)
    except (TypeError, ValueError):
        return 0.0


def oc_documenta(fuentes: dict[str, dict[int, list]], n: int,
                 ns_con_ferraforme: set[int]) -> tuple[bool, list[str]]:
    """
    ¿Alguna OC recibida de N documenta que ESTE SKU llegó en N? → (sí/no, por
    qué no cada OC). La OC no es familia: su N casi siempre se infirió de los
    demás SKUs. Documenta solo si:
      · su N no salió de la mayoría del campo de Odoo (`VIAS_OC_DEL_CAMPO`):
        esa N es de los OTROS SKUs de la orden, no de este;
      · el Ferraforme de N (documento) no la contradice: si no trae el SKU, la
        OC no lo pone ahí (igual que con Odoo sola, el «refutado»);
      · no es el MISMO recibo que otra OC de otra N (misma cantidad recibida):
        la conversión «De consumibles a almacenables» o una OC correctora
        vuelven a recibir lo que ya llegó en otro contenedor.
    """
    refs = fuentes.get(OC_RECIBIDA, {}).get(n) or []
    if not refs:
        return False, []
    if contradicha(fuentes, n, ns_con_ferraforme):
        return False, [f"el Ferraforme del {n} no lo trae"]
    otras = [(m, r) for m, rs in fuentes.get(OC_RECIBIDA, {}).items() if m != n for r in rs]
    motivos = []
    for r in refs:
        oc, via = _txt(r.get("oc")) or "la OC", _txt(r.get("via"))
        if via.startswith(VIAS_OC_DEL_CAMPO):
            motivos.append(f"{oc}: su N es la mayoría de los otros SKUs según el campo de Odoo")
            continue
        rec = _recibido(r)
        gemela = next(((m, o) for m, o in otras
                       if rec > 0 and _recibido(o) == rec and _txt(o.get("oc")) != _txt(r.get("oc"))), None)
        if gemela:
            motivos.append(f"{oc}: recibió lo mismo ({rec:g}) que {_txt(gemela[1].get('oc'))} (del {gemela[0]})")
            continue
        return True, []
    return False, motivos


def documentos(fuentes: dict[str, dict[int, list]], n: int, ns_con_ferraforme: set[int]) -> set[str]:
    """Documentos primarios de N para este SKU: Ferraforme, costos, y la OC
    recibida solo si `oc_documenta`."""
    docs = {f for f in (FERRA_ALINEADO, FERRA_NO_ALINEADO, COSTOS) if n in fuentes.get(f, {})}
    if oc_documenta(fuentes, n, ns_con_ferraforme)[0]:
        docs.add(OC_RECIBIDA)
    return docs


def n_por_fuente(fuentes: dict[str, dict[int, list]]) -> str:
    """«ferra_al:12 | costos:12 | odoo_campo:34» (para reportes)."""
    return " | ".join(f"{f}:{','.join(str(n) for n in sorted(fuentes[f]))}"
                      for f in ORDEN_FUENTES if fuentes.get(f))


def _evidencia_de(fuentes: dict[str, dict[int, list]], n: int, fams: set[str],
                  ns_con_ferraforme: set[int] = frozenset()) -> dict:
    ev: dict[str, Any] = {"familias": sorted(fams)}
    for f in ORDEN_FUENTES:
        refs = fuentes.get(f, {}).get(n)
        if not refs:
            continue
        llave = LLAVE_EVIDENCIA[f]
        lista = ev.setdefault(llave, [])
        for r in refs:
            if len(lista) >= MAX_REFS:
                break
            if r not in lista:
                lista.append(r)
        if f == ODOO_PELON:
            ev["odoo_pelon_nota"] = "solo código: débil, no cuenta como familia"
    if contradicha(fuentes, n, ns_con_ferraforme):
        # Se carga (costos sola es fuerte según el spec), pero se puede filtrar.
        ev["contradicho_por"] = f"Ferraforme del {n}"
    return ev


def clasificar_sku(sku: str, fuentes: dict[str, dict[int, list]], *, en_catalogo: bool,
                   es_padre_woo: bool, ns_con_ferraforme: set[int]) -> dict:
    """
    Un SKU → {estado: cargar|excluir, motivo, multi, ns, asignaciones}. Cada
    asignación: {numero, nivel, fuentes (etiquetas de la BD), familias,
    evidencia}. `fuentes` = {fuente interna: {N: [ref, …]}}. Un conflicto trae
    `sin_documento` y, si alguna de esas N tenía OC que no la documenta,
    `oc_no_documenta` = {N: [por qué]}.
    """
    fuentes = {f: v for f, v in (fuentes or {}).items() if v}
    ns = sorted(set().union(*[set(v) for v in fuentes.values()])) if fuentes else []
    base = {"sku": sku, "ns": ns, "n_por_fuente": n_por_fuente(fuentes), "multi": False,
            "asignaciones": []}

    def excluir(motivo: str, **extra) -> dict:
        return dict(base, estado="excluir", motivo=motivo, **extra)

    if es_provisional(sku):
        return excluir("provisional")
    if not en_catalogo:
        return excluir("fuera_de_catalogo")
    if es_padre_woo and not fuentes:
        return excluir("padre_woo")
    if not ns:
        return excluir("sin_evidencia")

    multi = len(ns) > 1
    if multi:
        sin_doc = [n for n in ns if not documentos(fuentes, n, ns_con_ferraforme)]
        if sin_doc:
            extra: dict[str, Any] = {"sin_documento": sin_doc}
            oc_no = {n: oc_documenta(fuentes, n, ns_con_ferraforme)[1] for n in sin_doc
                     if fuentes.get(OC_RECIBIDA, {}).get(n)}
            if oc_no:
                extra["oc_no_documenta"] = oc_no
            return excluir("conflicto", **extra)
        if not any(familias(fuentes, n) for n in ns):
            # Solo OC en todas sus N: igual que una OC sola, ninguna familia.
            return excluir("debil")

    asignaciones = []
    for n in ns:
        fams = familias(fuentes, n)
        if n in fuentes.get(FERRA_ALINEADO, {}) or len(fams) >= 2:
            nivel = "A"
        elif fams or multi:
            # Una familia; o, dentro de un multi (cada N ya tiene documento),
            # una N cuyo único documento es una OC que la documenta.
            nivel = "B"
        else:
            return excluir("debil")
        if not multi and fams == {"O"} and contradicha(fuentes, n, ns_con_ferraforme):
            # Sin excepción por OC: en una N contradicha ninguna OC documenta.
            return excluir("refutado", refuta=f"Ferraforme del {n}")
        etiquetas = sorted({A_BD[f] for f, v in fuentes.items() if f in A_BD and n in v},
                           key=FUENTES_BD.index)
        asignaciones.append({"numero": n, "nivel": nivel, "fuentes": etiquetas, "familias": sorted(fams),
                             "evidencia": _evidencia_de(fuentes, n, fams, ns_con_ferraforme)})
    return dict(base, estado="cargar", motivo="", multi=multi, asignaciones=asignaciones)


def agrupar_evidencia(evidencias: Iterable[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """[evidencia] → ({clave: {fuente: {N: [ref]}}}, {clave: sku como vino})."""
    por_sku: dict[str, dict] = collections.defaultdict(lambda: collections.defaultdict(dict))
    forma: dict[str, str] = {}
    for e in evidencias:
        k = clave(e["sku"])
        if not k or _n_valido(e.get("numero")) is None:
            continue                            # sin SKU, o N <= 0 (ya contada en sin_numero)
        forma.setdefault(k, _txt(e["sku"]).strip())
        por_sku[k][e["fuente"]].setdefault(int(e["numero"]), []).append(e.get("ref") or {})
    return {k: dict(v) for k, v in por_sku.items()}, forma


def ubicar(evidencias: Iterable[dict], catalogo: dict[str, str], padres: set[str],
           ns_con_ferraforme: set[int], codigo_por_n: dict[int, str] | None = None,
           origen: str = ORIGEN_CARGA_INICIAL) -> dict:
    """
    Todo el universo (catálogo ∪ SKUs con evidencia) → {asignaciones, excluidos,
    por_sku, resumen}. `catalogo` = {clave: sku como está en core.products};
    `padres` = claves de los padres de Woo.
    """
    codigo_por_n = codigo_por_n or {}
    por_sku, forma = agrupar_evidencia(evidencias)
    asignaciones, excluidos, filas_sku = [], [], []
    for k in sorted(set(catalogo) | set(por_sku)):
        sku = catalogo.get(k) or forma.get(k) or k
        r = clasificar_sku(sku, por_sku.get(k, {}), en_catalogo=k in catalogo,
                           es_padre_woo=k in padres, ns_con_ferraforme=ns_con_ferraforme)
        filas_sku.append(r)
        if r["estado"] == "cargar":
            for a in r["asignaciones"]:
                asignaciones.append({"sku": sku, "numero": a["numero"], "codigo": codigo_por_n.get(a["numero"]),
                                     "nivel": a["nivel"], "fuentes": a["fuentes"], "multi": r["multi"],
                                     "evidencia": a["evidencia"], "origen": origen})
        else:
            excluidos.append({"sku": sku, "motivo": r["motivo"], "ns": r["ns"],
                              "n_por_fuente": r["n_por_fuente"],
                              "evidencia": {f: {str(n): refs[:MAX_REFS] for n, refs in sorted(v.items())}
                                            for f, v in por_sku.get(k, {}).items()},
                              **{x: r[x] for x in ("sin_documento", "refuta", "oc_no_documenta") if x in r}})
    return {"asignaciones": asignaciones, "excluidos": excluidos, "por_sku": filas_sku,
            "resumen": resumir(filas_sku, asignaciones)}


def resumir(filas_sku: list[dict], asignaciones: list[dict]) -> dict:
    cargar = [r for r in filas_sku if r["estado"] == "cargar"]
    nivel_sku = collections.Counter("A" if any(a["nivel"] == "A" for a in r["asignaciones"]) else "B"
                                    for r in cargar)
    return {
        "universo": len(filas_sku),
        "skus_cargables": len(cargar),
        "filas": len(asignaciones),
        "filas_por_nivel": dict(sorted(collections.Counter(a["nivel"] for a in asignaciones).items())),
        "skus_por_nivel": dict(sorted(nivel_sku.items())),
        "skus_nivel_A_estricto": sum(1 for r in cargar if all(a["nivel"] == "A" for a in r["asignaciones"])),
        "skus_multi": sum(1 for r in cargar if r["multi"]),
        "filas_multi": sum(1 for a in asignaciones if a["multi"]),
        "excluidos_por_motivo": {m: sum(1 for r in filas_sku if r.get("motivo") == m) for m in MOTIVOS},
        "filas_por_fuente": dict(collections.Counter(f for a in asignaciones for f in a["fuentes"])),
        # Filas cuya N tiene Ferraforme-documento que no trae el SKU (se cargan: costos sola es fuerte).
        "filas_contradichas_por_ferraforme": dict(sorted(collections.Counter(
            a["nivel"] for a in asignaciones if a["evidencia"].get("contradicho_por")).items())),
        "filas_sin_familia": sum(1 for a in asignaciones if not a["evidencia"].get("familias")),
        "conflictos_con_oc_que_no_documenta": sum(1 for r in filas_sku if r.get("oc_no_documenta")),
    }


def fila_bd(a: dict) -> tuple:
    """Una asignación → la tupla de INSERT de costing.sku_contenedor."""
    return (a["sku"], a["numero"], a.get("codigo"), a["nivel"], list(a["fuentes"]), bool(a["multi"]),
            json.dumps(a["evidencia"], ensure_ascii=False, default=str), a["origen"])
