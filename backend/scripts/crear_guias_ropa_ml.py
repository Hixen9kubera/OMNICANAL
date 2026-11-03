"""
Guías de tallas de ROPA en Mercado Libre con la TABLA ESTÁNDAR POR GÉNERO.

POR QUÉ (9-oct-2026). En ropa, Mercado Libre solo acepta guías PROPIAS de la cuenta
(«SPECIFIC»): no existen guías estándar de ML como en calzado. Y no había ni una en
ninguna de las dos cuentas, así que toda la ropa rebotaba con «falta la guía de
tallas». Decisión de Brandon: **una tabla de medidas estándar por género —Hombre,
Mujer, Niño—**, la misma para todos los dominios; no una tabla por producto.

QUÉ HACE. Por cada cuenta y cada clave `DOMINIO:Género` crea UNA guía con esa tabla:
tallas XS–5XL en adulto (el unisex «Sin género» usa la de Hombre) y la estatura
(90–160 cm) en infantil. Le pide a ML la ficha del dominio y manda SOLO las medidas
que esa ficha admite (pecho/busto, cintura, cadera y, en infantil, estatura). La
equivalencia que ML exige por renglón (`FILTRABLE_SIZE`) son letras (CH, M, G…) en
partes de arriba, la cintura en pulgadas en partes de abajo y la edad en infantil.

QUÉ NO HACE
  · No toca publicaciones ni guías existentes. Una guía ya creada con el mismo
    nombre se reutiliza (se puede correr dos veces).
  · No registra los ids: al terminar imprime las líneas para
    `backend/vendor/ml_ready/size_chart_mapping.py`, que es donde el publicador
    busca la guía de cada cuenta. Sin ese registro la guía existe pero no se usa.
  · No cubre tallas numéricas de ropa (30, 32…), rangos ni «Unitalla»: el producto
    con esa talla sigue diciendo «la talla no está en la guía» hasta que se le
    agregue el renglón.
  · Nunca renueva tokens.

Uso (desde backend/):
    python -m scripts.crear_guias_ropa_ml                         # ensayo: no escribe
    python -m scripts.crear_guias_ropa_ml --aplicar               # crea las que falten
    python -m scripts.crear_guias_ropa_ml --aplicar --cuenta SANCORFASHION --clave PAJAMAS:Mujer
"""
from __future__ import annotations

import json
import sys
import time
import unicodedata
from typing import Any

import httpx

API = "https://api.mercadolibre.com"
MARCA = "Ferrahome"
CUENTAS = ("BEKURA", "SANCORFASHION")

# ── LA TABLA ESTÁNDAR (cm, desde–hasta): pecho o busto, cintura, cadera ───────
LETRAS = ("XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL")
TABLA: dict[str, dict[str, tuple[tuple[int, int], tuple[int, int], tuple[int, int]]]] = {
    "Mujer": {
        "XS": ((78, 82), (60, 64), (86, 90)),       "S": ((83, 87), (65, 69), (91, 95)),
        "M": ((88, 92), (70, 74), (96, 100)),       "L": ((93, 98), (75, 80), (101, 106)),
        "XL": ((99, 105), (81, 87), (107, 113)),    "2XL": ((106, 112), (88, 94), (114, 120)),
        "3XL": ((113, 119), (95, 101), (121, 127)), "4XL": ((120, 126), (102, 108), (128, 134)),
        "5XL": ((127, 133), (109, 115), (135, 141)),
    },
    "Hombre": {
        "XS": ((82, 87), (70, 75), (84, 89)),        "S": ((88, 95), (76, 82), (90, 96)),
        "M": ((96, 103), (83, 89), (97, 103)),       "L": ((104, 111), (90, 97), (104, 110)),
        "XL": ((112, 119), (98, 106), (111, 117)),   "2XL": ((120, 127), (107, 115), (118, 124)),
        "3XL": ((128, 135), (116, 124), (125, 131)), "4XL": ((136, 143), (125, 133), (132, 138)),
        "5XL": ((144, 151), (134, 142), (139, 145)),
    },
}
# Infantil: la talla es la ESTATURA. (talla, edad equivalente, estatura, pecho, cintura, cadera)
NINO = (
    ("90 cm", "2 años", (86, 94), (51, 53), (49, 51), (53, 55)),
    ("100 cm", "4 años", (95, 104), (54, 56), (51, 53), (56, 59)),
    ("110 cm", "5 años", (105, 114), (57, 59), (53, 55), (60, 62)),
    ("120 cm", "7 años", (115, 124), (60, 63), (55, 57), (63, 66)),
    ("130 cm", "9 años", (125, 134), (64, 67), (57, 59), (67, 71)),
    ("140 cm", "10 años", (135, 144), (68, 72), (60, 62), (72, 76)),
    ("150 cm", "12 años", (145, 154), (73, 77), (63, 65), (77, 82)),
    ("160 cm", "14 años", (155, 164), (78, 83), (66, 69), (83, 88)),
)
TABLA_DE = {"Mujer": "Mujer", "Hombre": "Hombre", "Sin género": "Hombre"}
INFANTIL = frozenset({"Niños", "Niñas", "Sin género infantil"})
EQ_LETRA = {"XS": "XCH", "S": "CH", "M": "M", "L": "G", "XL": "XG",
            "2XL": "2XG", "3XL": "3XG", "4XL": "4XG", "5XL": "5XG"}
EQ_CINTURA = {
    "Hombre": {"XS": "28", "S": "30", "M": "32", "L": "34", "XL": "36",
               "2XL": "38", "3XL": "40", "4XL": "42", "5XL": "44"},
    "Mujer": {"XS": "24", "S": "26", "M": "28", "L": "30", "XL": "32",
              "2XL": "34", "3XL": "36", "4XL": "38", "5XL": "40"},
}
MEDIDA = {"CHEST_CIRCUMFERENCE": 0, "BUST_CIRCUMFERENCE": 0,
          "WAIST_CIRCUMFERENCE": 1, "HIP_CIRCUMFERENCE": 2}

# Las claves con productos esperando guía (medido el 9-oct-2026 sobre WooCommerce).
CLAVES = (
    "JACKETS_AND_COATS:Hombre", "DRESSES:Mujer", "PANTS:Hombre", "SHIRTS:Hombre", "PANTS:Mujer",
    "SWEATSHIRTS_AND_HOODIES:Mujer", "BLOUSES:Mujer", "JACKETS_AND_COATS:Mujer", "LEGGINGS:Mujer",
    "SWEATSHIRTS_AND_HOODIES:Sin género", "PAJAMAS:Mujer", "TACTICAL_PANTS:Hombre",
    "SWEATERS_AND_CARDIGANS:Mujer", "SPORT_PANTS:Hombre", "SKIRTS:Mujer", "PAJAMAS:Sin género",
    "SPORT_SHORTS:Hombre", "PANTIES:Mujer", "SHIRTS:Niños", "UNDERPANTS:Hombre", "T_SHIRTS:Sin género",
    "SHORTS:Hombre", "T_SHIRTS:Mujer", "NIGHTGOWNS:Mujer", "PONCHOS:Mujer", "DRESSES:Niñas",
    "PAJAMAS:Sin género infantil", "SWEATERS_AND_CARDIGANS:Hombre", "SPORT_SHORTS:Mujer",
    "T_SHIRTS:Hombre", "UNDERPANTS:Niños", "THERMAL_SHIRTS:Mujer", "SPORT_SKIRTS:Mujer", "VESTS:Hombre",
    "SWEATSHIRTS_AND_HOODIES:Hombre", "FOOTBALL_SWEATSHIRTS_AND_HOODIES:Mujer", "BLOUSES:Niños",
    "DRESSES:Sin género infantil", "PANTS:Sin género", "SPORT_T_SHIRTS:Hombre",
    "SPORT_T_SHIRTS:Sin género", "VESTS:Mujer",
)


def nombre_de(dominio: str, genero: str) -> str:
    """Nombre de la guía: solo letras, números y espacios, máximo 60 (regla de ML)."""
    legible = " ".join(p.capitalize() for p in dominio.split("_"))
    texto = f"{MARCA} {genero} {legible}"
    plano = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return plano[:60]


def _cm(n: int) -> dict[str, Any]:
    return {"name": f"{n} cm", "struct": {"number": n, "unit": "cm"}}


def _atributos_de(ficha: Any) -> dict[str, dict[str, Any]]:
    """La ficha de ML viene anidada por componentes: se aplana a {id: atributo}."""
    salida: dict[str, dict[str, Any]] = {}

    def recorrer(nodo: Any) -> None:
        if isinstance(nodo, dict):
            for a in nodo.get("attributes") or []:
                if isinstance(a, dict) and a.get("id"):
                    salida.setdefault(a["id"], a)
            for v in nodo.values():
                recorrer(v)
        elif isinstance(nodo, list):
            for v in nodo:
                recorrer(v)

    recorrer(ficha)
    return salida


def cuerpo(dominio: str, genero: str, ficha: Any) -> tuple[dict[str, Any] | None, str]:
    """El cuerpo de `POST /catalog/charts` para esa clave, o (None, motivo). Sin red."""
    attrs = _atributos_de(ficha)
    if "SIZE" not in attrs:
        return None, "Mercado Libre no admite guía para ese dominio y género"
    gv = next((v for v in (attrs.get("GENDER", {}).get("values") or []) if v.get("name") == genero), None)
    if not gv:
        return None, "la ficha no trae ese género"
    equivalencias = {v["name"]: v["id"] for v in (attrs.get("FILTRABLE_SIZE", {}).get("values") or [])}
    exigidas = [a["id"] for a in attrs.values()
                if "BODY_MEASURE" in (a.get("tags") or []) and "required" in (a.get("tags") or [])]

    def medidas(valores: dict[str, tuple[int, int]]) -> list[dict[str, Any]]:
        fila = []
        for base, (desde, hasta) in valores.items():
            if base + "_FROM" in attrs:
                fila.append({"id": base + "_FROM", "values": [_cm(desde)]})
            if base + "_TO" in attrs:
                fila.append({"id": base + "_TO", "values": [_cm(hasta)]})
        return fila

    filas: list[dict[str, Any]] = []
    if genero in INFANTIL:
        for talla, edad, estatura, pecho, cintura, cadera in NINO:
            if edad not in equivalencias:
                continue
            filas.append({"attributes": [
                {"id": "SIZE", "values": [{"name": talla}]},
                {"id": "FILTRABLE_SIZE", "values": [{"id": equivalencias[edad], "name": edad}]},
                *medidas({"CHEST_CIRCUMFERENCE": pecho, "BUST_CIRCUMFERENCE": pecho,
                          "WAIST_CIRCUMFERENCE": cintura, "HIP_CIRCUMFERENCE": cadera,
                          "PERSON_HEIGHT": estatura}),
            ]})
    else:
        tabla = TABLA[TABLA_DE[genero]]
        por_cintura = "CH" not in equivalencias          # partes de abajo
        eq = EQ_CINTURA["Mujer" if genero == "Mujer" else "Hombre"] if por_cintura else EQ_LETRA
        for talla in LETRAS:
            if eq[talla] not in equivalencias:
                continue
            filas.append({"attributes": [
                {"id": "SIZE", "values": [{"name": talla}]},
                {"id": "FILTRABLE_SIZE", "values": [{"id": equivalencias[eq[talla]], "name": eq[talla]}]},
                *medidas({base: tabla[talla][i] for base, i in MEDIDA.items()}),
            ]})
    if not filas:
        return None, "ninguna talla de la tabla tiene equivalencia en la ficha de ML"
    puestas = {a["id"] for f in filas for a in f["attributes"]}
    faltan = [m for m in exigidas if m not in puestas]
    if faltan:
        return None, f"la ficha exige medidas que la tabla estándar no tiene: {faltan}"
    return {
        "names": {"MLM": nombre_de(dominio, genero)}, "domain_id": dominio, "site_id": "MLM",
        "measure_type": "BODY_MEASURE",
        "main_attribute": {"attributes": [{"site_id": "MLM", "id": "SIZE"}]},
        "attributes": [{"id": "GENDER", "values": [{"id": gv["id"], "name": gv["name"]}]}],
        "rows": filas,
    }, ""


def _ficha(cli: httpx.Client, cab: dict[str, str], dominio: str, genero: str) -> Any:
    r = cli.post(f"{API}/domains/MLM-{dominio}/technical_specs", params={"section": "grids"},
                 headers=cab, json={"attributes": [{"id": "GENDER", "value_name": genero}]})
    return r.json() if r.status_code == 200 else {}


def _ya_existe(cli: httpx.Client, cab: dict[str, str], vendedor: int, dominio: str,
               genero: str, nombre: str) -> str | None:
    r = cli.post(f"{API}/catalog/charts/search", headers=cab, json={
        "domain_id": dominio, "site_id": "MLM", "seller_id": vendedor,
        "attributes": [{"id": "GENDER", "values": [{"name": genero}]},
                       {"id": "BRAND", "values": [{"name": MARCA}]}]})
    if r.status_code != 200:
        return None
    for guia in r.json().get("charts") or []:
        nombres = guia.get("names")
        actual = nombres.get("MLM") if isinstance(nombres, dict) else nombres
        if guia.get("type") == "SPECIFIC" and str(actual or "").strip().lower() == nombre.lower():
            return str(guia["id"])
    return None


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    arg = lambda nombre: sys.argv[sys.argv.index(nombre) + 1] if nombre in sys.argv else None  # noqa: E731
    cuentas = [arg("--cuenta")] if arg("--cuenta") else list(CUENTAS)
    claves = [arg("--clave")] if arg("--clave") else list(CLAVES)

    from services import meli  # token ya vigente de la cuenta; aquí NO se renueva

    registro: dict[str, dict[str, str]] = {}
    with httpx.Client(timeout=60.0) as cli:
        for cuenta in cuentas:
            token = meli._access_token(cuenta)
            if not token:
                print(f"[{cuenta}] sin token: se omite"); continue
            cab = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            yo = cli.get(f"{API}/users/me", headers=cab)
            if yo.status_code != 200:
                print(f"[{cuenta}] el token no responde (HTTP {yo.status_code}): se omite"); continue
            vendedor = int(yo.json()["id"])
            for clave in claves:
                dominio, genero = clave.split(":", 1)
                carga, motivo = cuerpo(dominio, genero, _ficha(cli, cab, dominio, genero))
                if not carga:
                    print(f"[{cuenta}] {clave:42s} omitida: {motivo}"); continue
                nombre = carga["names"]["MLM"]
                previa = _ya_existe(cli, cab, vendedor, dominio, genero, nombre)
                if previa:
                    registro.setdefault(cuenta, {})[clave] = previa
                    print(f"[{cuenta}] {clave:42s} ya existía  {previa}"); continue
                if not aplicar:
                    tallas = [f["attributes"][0]["values"][0]["name"] for f in carga["rows"]]
                    print(f"[{cuenta}] {clave:42s} se crearía «{nombre}» con {tallas}"); continue
                r = cli.post(f"{API}/catalog/charts", headers=cab, json=carga)
                try:
                    j = r.json()
                except Exception:  # noqa: BLE001
                    j = {"texto": r.text[:300]}
                if r.status_code in (200, 201) and j.get("id"):
                    registro.setdefault(cuenta, {})[clave] = str(j["id"])
                    print(f"[{cuenta}] {clave:42s} CREADA      {j['id']}")
                else:
                    # Un 5xx pudo haberla creado: se vuelve a buscar antes de darla por fallida.
                    previa = _ya_existe(cli, cab, vendedor, dominio, genero, nombre) if r.status_code >= 500 else None
                    if previa:
                        registro.setdefault(cuenta, {})[clave] = previa
                        print(f"[{cuenta}] {clave:42s} CREADA      {previa} (tras HTTP {r.status_code})")
                    else:
                        print(f"[{cuenta}] {clave:42s} RECHAZADA   HTTP {r.status_code}: "
                              f"{json.dumps(j, ensure_ascii=False)[:400]}")
                time.sleep(0.4)

    if not aplicar:
        print("\n>>> ENSAYO. Nada escrito. Correr con --aplicar.")
    if registro:
        print("\n# Para backend/vendor/ml_ready/size_chart_mapping.py (CHARTS_BY_ACCOUNT):")
        for cuenta, guias in registro.items():
            print(f"    '{cuenta}': {{")
            for clave, guia in guias.items():
                print(f"        {clave!r}: {guia!r},")
            print("    },")
        print("\nJSON:", json.dumps(registro, ensure_ascii=False))


if __name__ == "__main__":
    main()
