"""Abre el perfil DEDICADO del raspador para iniciar sesión en Mercado Libre.

POR QUÉ HACE FALTA
------------------
ML empezó a exigir sesión tras ~50 consultas seguidas: sirve "¡Hola! Para
continuar, ingresa a tu cuenta" en lugar de los resultados. Con una sesión abierta
en el navegador, el muro deja de aparecer tan pronto.

QUÉ CUENTA USAR
---------------
Una cuenta de Mercado Libre creada SOLO para esto. **No** BEKURA ni SANCORFASHION:
si ML marca la cuenta por navegación automatizada, la que se pierde debe ser
desechable, nunca una que venda. Tampoco el perfil personal de Chrome de nadie —
por eso esto usa un directorio aparte (`~/.competencia-chrome`) y no toca el Chrome
del usuario, que además ni siquiera hay que cerrar.

CÓMO
----
    python scripts/competencia_login.py

Abre una ventana en la página de ingreso. Inicia sesión a mano —incluido el código
que ML mande al correo— y cuando ya veas tu cuenta dentro, vuelve a la terminal y
presiona ENTER. La sesión queda guardada en el perfil y el raspador la reusa.

Para comprobar que quedó: `python scripts/competencia_login.py --verificar`
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_mas_vendidos as mv  # noqa: E402

URL_LOGIN = "https://www.mercadolibre.com.mx/login"
URL_PRUEBA = "https://listado.mercadolibre.com.mx/colchon-memory-foam"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verificar", action="store_true",
                    help="No pide ingresar: solo comprueba si la sesión sirve")
    args = ap.parse_args()

    mv.RUTA_PERFIL.mkdir(parents=True, exist_ok=True)
    print(f"perfil dedicado: {mv.RUTA_PERFIL}")
    print("(NO es tu Chrome; no hace falta cerrarlo)\n")

    d = mv._navegador(visible=True)
    try:
        if not args.verificar:
            d.get(URL_LOGIN)
            print("Se abrió la ventana de ingreso de Mercado Libre.")
            print("Inicia sesión a mano con la cuenta DEDICADA (no la de la empresa).")
            input("Cuando ya estés dentro, vuelve aquí y presiona ENTER… ")

        # La prueba real no es "¿hay sesión?" sino "¿me deja ver resultados?".
        d.get(URL_PRUEBA)
        time.sleep(6)
        html, url = d.page_source, d.current_url
        if mv._bloqueado(html, url):
            print("\n✗ Sigue bloqueado. La página que devolvió no trae resultados.")
            print(f"  url: {url[:100]}")
            return 1
        from bs4 import BeautifulSoup
        n = len(BeautifulSoup(html, "lxml").select("div.poly-card"))
        if n:
            print(f"\n✓ Listo: la búsqueda devolvió {n} tarjetas. El raspador ya "
                  f"puede usar este perfil.")
            return 0
        print("\n? La página cargó sin bloqueo pero sin tarjetas. Revisa a mano.")
        return 1
    finally:
        d.quit()


if __name__ == "__main__":
    raise SystemExit(main())
