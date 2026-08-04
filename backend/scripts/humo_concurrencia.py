"""
humo_concurrencia.py — ¿Aguanta el panel con TODO el equipo conectado a la vez?

POR QUÉ EXISTE
--------------
La autenticación se probó con un usuario. Con once pasan dos cosas que con uno
no se ven nunca, y las dos degradan a todo el mundo (no solo a quien entra):

  1. **La estampida.** Abrir el panel dispara ~8 llamadas a la API en paralelo.
     Sin candado, las 8 verifican el MISMO token contra Supabase y consultan 8
     veces `core.usuarios`. Once personas entrando juntas = ~88 verificaciones
     simultáneas contra un pool de 6 conexiones que además es `blocking=True`.

  2. **El event loop congelado.** `core.usuarios` se lee con psycopg2, que es
     SÍNCRONO. Llamarlo desde una corrutina detiene el servidor ENTERO mientras
     dura la consulta: ni el panel de los demás, ni el webhook de ML, ni el
     healthcheck de Railway son atendidos. Con once consultas encoladas contra
     un pool de 6, ese congelamiento se vuelve segundos.

Esta prueba no simula "carga": reproduce esos dos escenarios exactos y mide.
No toca la red ni la base — Supabase y `core.usuarios` se sustituyen por dobles
que CUENTAN cuántas veces se les llamó.

    python -m scripts.humo_concurrencia          # desde backend/
    exit 0 = aguanta · exit 1 = NO desplegar
"""
from __future__ import annotations

import asyncio
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from core import identidad

# Cuánto "tarda" la consulta a core.usuarios. Es BLOQUEANTE a propósito: así es
# psycopg2 de verdad. Si el código la llamara desde el event loop, este número
# se multiplicaría por la cantidad de usuarios.
TARDANZA_BASE = 0.25
EQUIPO = 11          # 3 admin + 8 KAM
LLAMADAS_POR_PANTALLA = 8

llamadas_http: list[str] = []
consultas_base: list[str] = []

fallos: list[str] = []
pasadas = 0


def revisar(nombre: str, obtenido, esperado, pista: str = "") -> None:
    global pasadas
    if obtenido == esperado:
        pasadas += 1
        print(f"  OK   {nombre}  ({obtenido})")
    else:
        fallos.append(f"{nombre}: esperado {esperado}, obtenido {obtenido}. {pista}")
        print(f"  FALLA {nombre}  esperado={esperado} obtenido={obtenido}")


def revisar_menor(nombre: str, obtenido: float, tope: float, pista: str = "") -> None:
    global pasadas
    if obtenido < tope:
        pasadas += 1
        print(f"  OK   {nombre}  ({obtenido:.2f} < {tope:.2f})")
    else:
        fallos.append(f"{nombre}: {obtenido:.2f} debía ser menor que {tope:.2f}. {pista}")
        print(f"  FALLA {nombre}  {obtenido:.2f} >= {tope:.2f}")


# ── Dobles de Supabase ──────────────────────────────────────────────────────
class _Respuesta:
    def __init__(self, status: int, datos: dict) -> None:
        self.status_code = status
        self._datos = datos

    def json(self) -> dict:
        return self._datos


class _ClienteFalso:
    """Reemplaza httpx.AsyncClient. Cuenta llamadas y simula latencia de red."""

    def __init__(self, *_a, **_k) -> None:
        pass

    async def __aenter__(self) -> "_ClienteFalso":
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def get(self, _url: str, headers: dict | None = None) -> _Respuesta:
        token = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        llamadas_http.append(token)
        await asyncio.sleep(0.05)                      # latencia de red
        if token.startswith("vencido"):
            return _Respuesta(401, {})
        return _Respuesta(200, {"id": f"uuid-{token}", "email": f"{token}@kubera.mx"})


def _perfil_falso(uid: str, _correo: str) -> tuple[str, bool]:
    """Doble de core.usuarios. BLOQUEANTE, como psycopg2."""
    time.sleep(TARDANZA_BASE)
    consultas_base.append(uid)
    return ("operador", True)


def _instalar_dobles() -> None:
    identidad.httpx = type("_httpx", (), {"AsyncClient": _ClienteFalso})  # type: ignore[assignment]
    identidad._perfil_en_kubera = _perfil_falso                            # type: ignore[assignment]
    identidad.settings.supabase_url = "https://prueba.supabase.co"
    identidad.settings.supabase_anon_key = "anon-de-prueba"


def _limpiar() -> None:
    identidad._cache.clear()
    identidad._candados.clear()
    llamadas_http.clear()
    consultas_base.clear()


async def _latido(parar: asyncio.Event, cuenta: list[int]) -> None:
    """
    Tictac cada 10 ms. Es el testigo de que el servidor SIGUE ATENDIENDO: si la
    consulta a la base bloqueara el event loop, este contador se quedaría quieto.
    """
    while not parar.is_set():
        cuenta[0] += 1
        await asyncio.sleep(0.01)


async def main() -> int:
    _instalar_dobles()

    print("=" * 74)
    print("HUMO DE CONCURRENCIA — el equipo completo entrando al mismo tiempo")
    print("=" * 74)

    # 1 ────────────────────────────────────────────────────────────────────
    print(f"\n1) UNA PERSONA ABRE EL PANEL ({LLAMADAS_POR_PANTALLA} llamadas en paralelo)")
    _limpiar()
    res = await asyncio.gather(*[identidad._por_token("ana")
                                 for _ in range(LLAMADAS_POR_PANTALLA)])
    revisar("verificaciones contra Supabase", len(llamadas_http), 1,
            "Sin candado por token, cada llamada de la pantalla verifica por su cuenta.")
    revisar("consultas a core.usuarios", len(consultas_base), 1,
            "El pool de Supabase es de 6 conexiones; no se puede gastar una por llamada.")
    revisar("todas recibieron identidad", all(r is not None for r in res), True)
    revisar("y todas la MISMA", len({r.actor for r in res if r}), 1)
    revisar("con su rol resuelto", res[0].rol if res[0] else None, "operador")

    # 2 ────────────────────────────────────────────────────────────────────
    print(f"\n2) LAS {EQUIPO} PERSONAS ENTRAN A LA VEZ "
          f"({EQUIPO * LLAMADAS_POR_PANTALLA} peticiones simultáneas)")
    _limpiar()
    parar = asyncio.Event()
    cuenta = [0]
    testigo = asyncio.create_task(_latido(parar, cuenta))

    arranque = time.perf_counter()
    tareas = [identidad._por_token(f"persona{i}")
              for i in range(EQUIPO) for _ in range(LLAMADAS_POR_PANTALLA)]
    todas = await asyncio.gather(*tareas)
    duracion = time.perf_counter() - arranque

    parar.set()
    await testigo

    revisar("verificaciones contra Supabase", len(llamadas_http), EQUIPO,
            f"Debe ser UNA por persona, no {EQUIPO * LLAMADAS_POR_PANTALLA}.")
    revisar("consultas a core.usuarios", len(consultas_base), EQUIPO)
    revisar("todas entraron", all(r is not None for r in todas), True)
    revisar("cada quien es quien dice", len({r.actor for r in todas if r}), EQUIPO)

    # Si la consulta corriera en el event loop, las 11 se harían EN FILA:
    # 11 × 0.25 s = 2.75 s. En un hilo se solapan y bajan a poco más de una.
    en_fila = EQUIPO * TARDANZA_BASE
    print(f"     (en fila serían {en_fila:.2f}s; medido {duracion:.2f}s)")
    revisar_menor("el equipo completo entra en menos de", duracion, en_fila / 2,
                  "La consulta a la base está bloqueando el event loop.")

    # El testigo es la prueba de que el servidor siguió atendiendo mientras tanto.
    minimo = int(duracion / 0.02)
    print(f"     (el servidor latió {cuenta[0]} veces durante la espera)")
    revisar("el servidor NUNCA se congeló", cuenta[0] > minimo, True,
            "El event loop se detuvo: el webhook de ML y el healthcheck también.")

    # 3 ────────────────────────────────────────────────────────────────────
    print("\n3) YA ADENTRO, NADIE VUELVE A MOLESTAR A SUPABASE")
    http_antes, base_antes = len(llamadas_http), len(consultas_base)
    await asyncio.gather(*[identidad._por_token(f"persona{i}")
                           for i in range(EQUIPO) for _ in range(4)])
    revisar("verificaciones nuevas", len(llamadas_http) - http_antes, 0,
            "El caché de 5 minutos no está sirviendo.")
    revisar("consultas nuevas a la base", len(consultas_base) - base_antes, 0)

    # 4 ────────────────────────────────────────────────────────────────────
    print("\n4) UNA SESIÓN VENCIDA NO SE CONVIERTE EN UNA TORMENTA")
    _limpiar()
    malas = await asyncio.gather(*[identidad._por_token("vencido-de-nancy")
                                   for _ in range(12)])
    revisar("las 12 peticiones se rechazan", all(r is None for r in malas), True,
            "Un token inválido JAMÁS puede resolver una identidad.")
    revisar("pero Supabase se consulta una vez", len(llamadas_http), 1)
    revisar("y la base NUNCA", len(consultas_base), 0,
            "Si el token no vale, no hay por qué preguntar su rol.")
    await asyncio.gather(*[identidad._por_token("vencido-de-nancy") for _ in range(6)])
    revisar("y al reintentar tampoco", len(llamadas_http), 1,
            "El caché negativo no está funcionando.")

    # 5 ────────────────────────────────────────────────────────────────────
    print("\n5) AL CERRAR SESIÓN, EL TOKEN DEJA DE VALER DE INMEDIATO")
    _limpiar()
    await identidad._por_token("haim")
    revisar("entró", len(llamadas_http), 1)
    identidad.olvidar("haim")
    revisar("el token salió del caché", "haim" in identidad._cache, False)
    await identidad._por_token("haim")
    revisar("se vuelve a verificar desde cero", len(llamadas_http), 2,
            "Cerrar sesión no estaría surtiendo efecto hasta 5 minutos después.")

    # 6 ────────────────────────────────────────────────────────────────────
    print("\n6) NO SE ACUMULA BASURA EN MEMORIA")
    _limpiar()
    await asyncio.gather(*[identidad._por_token(f"u{i}") for i in range(210)])
    print(f"     (tras 210 sesiones: {len(identidad._cache)} en caché, "
          f"{len(identidad._candados)} candados)")
    revisar("los candados no superan al caché",
            len(identidad._candados) <= len(identidad._cache) + 1, True,
            "Se está fugando un candado por cada sesión que pasa.")

    print("\n" + "=" * 74)
    if fallos:
        print(f"FALLARON {len(fallos)} DE {len(fallos) + pasadas} — NO DESPLEGAR")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print(f"LAS {pasadas} PRUEBAS PASARON — el equipo completo cabe")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
