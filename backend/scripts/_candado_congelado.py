"""
_candado_congelado.py — El candado de los scripts que le preguntan a una tabla
muerta.

EL PROBLEMA QUE RESUELVE
------------------------
El 13-ago se apagaron los espejos a MySQL. Varias tablas de `kubera_ml` quedaron
congeladas en ese instante. Y un puñado de scripts de mantenimiento —que se
corren A MANO, cuando alguien decide— siguen consultándolas para decidir qué
escribir en Woo, en ML o en Amazon.

**Una tabla congelada no contesta "no sé": contesta con seguridad lo que era
cierto el día que se detuvo.** Es el mecanismo exacto de los 964 pedidos
fantasma del 12-ago ($409,741 en 4 h 17 min). La única diferencia es que
aquellos los disparaba un webhook y estos los dispara una persona — el daño no
depende del calendario, depende de quién los invoque.

El plan original decía que estos scripts *"dejarán de funcionar el día del
retiro"*. Es al revés: **ya están rotos, y no fallan — contestan.**

QUÉ HACE ESTE CANDADO
---------------------
Mide la edad REAL de la tabla en el momento de correr, y:

  · tabla VIVA (escrita hace poco)  → se aparta en silencio, no molesta
  · tabla CONGELADA + dry-run       → deja pasar, con un cartel que dice qué
                                      conclusión NO hay que creerle
  · tabla CONGELADA + va a escribir → **ABORTA** (exit 2) con el motivo

Bloquea la ESCRITURA, no el diagnóstico. Un dry-run que solo imprime es
inofensivo y sirve para entender; lo que hace daño es aplicar.

POR QUÉ MIDE EN VEZ DE LLEVAR LA FECHA A MANO
---------------------------------------------
Si mañana alguien repunta el script o resucita la tabla, **el candado se quita
solo**. Una fecha hardcodeada habría que ir a borrarla, y lo que se olvida
borrar se convierte en un bloqueo sin dueño.

Ojo con la trampa que este proyecto ya pisó: un umbral absoluto sobre algo que
crece con el calendario mide el paso del tiempo, no la salud. Aquí no aplica, y
la razón importa: la pregunta no es *"¿está sana esta tabla?"* sino **"¿alguien
la sigue escribiendo?"**, y `MAX(fecha)` contesta exactamente eso. Las tres
tablas se escribían al menos cada hora cuando estaban vivas, así que 12 h de
silencio significa muerta, no tranquila.

FALLA CERRADA
-------------
Si no se puede medir (MySQL caído, columna que cambió), y el script iba a
escribir, **aborta igual**. Un `except` que deja pasar la escritura sería el
mismo defecto que el candado viene a tapar: `_foto()` devolviendo `{}` ante un
error de BD hizo que un tropiezo se leyera como "primera pasada".
"""
from __future__ import annotations

import sys
import unicodedata
from datetime import datetime

# Tabla → (columna de fecha, cuándo y por qué se congeló)
#
# SOLO van aquí tablas que, VIVAS, se escribían al menos cada hora. Ese es el
# requisito para que `MAX(fecha)` distinga "muerta" de "tranquila" — y es la
# razón por la que `costos_finales` NO está en esta lista, aunque también esté
# de hecho congelada: sus escrituras legítimas siempre fueron esporádicas (14
# filas el 5-ago, 2 el 10-ago, ninguna varios días), así que un umbral de
# frescura sobre ella mediría el paso del calendario, no si alguien la escribe.
# Es la trampa que este proyecto ya pisó cuatro veces. Si algún día hace falta
# trancar un script que la lea, el candado tendrá que ser de otra forma.
_CONGELABLES: dict[str, tuple[str, str]] = {
    "pedidos_ml": (
        "creado",
        "13-ago-2026, con el apagón de espejos (ORDERS_ESPEJO_INVERSO=false). "
        "Los pedidos viven en channel.orders."),
    "canal_inventario": (
        "updated_at",
        "13-ago-2026 04:23, con el apagón de espejos "
        "(CHANNEL_ESPEJO_INVERSO=false). El estado de los canales vive en "
        "channel.listings."),
}

# 12 h: las dos se escribían al menos cada hora cuando estaban vivas —
# `canal_inventario` cada 15 min por el sync, `pedidos_ml` por cada venta.
_UMBRAL_H = 12.0


def _di(texto: str) -> None:
    """
    Imprime el mensaje del candado en ASCII, SIEMPRE.

    Dos tropiezos, en este orden, y los dos vale conservar:

    1. La primera versión usaba `─` y un emoji de adorno. La consola de Windows
       abre en cp1252 y eso tiró un `UnicodeEncodeError` **dentro de la guarda**.
       Abortó igual, pero por el crash, no por decisión: una guarda que se cae
       por un carácter decorativo no es una guarda.
    2. El arreglo fue un `try/except UnicodeEncodeError`… que **nunca dispara**,
       porque la consola no levanta la excepción: reemplaza en silencio y el
       mensaje sale picado (`apag?n`) justo cuando más importa entenderlo.

    Moraleja, la de siempre en este proyecto: **no detectar lo que se puede
    evitar.** No se pregunta si la consola aguanta — se manda algo que aguanta
    cualquiera. Se transliteran los acentos (NFKD y se tira la tilde suelta), así
    "apagón" sale "apagon" y el archivo se sigue escribiendo en español.
    """
    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore")
    print(plano.decode("ascii"))


def _edad_horas(tabla: str, col: str) -> tuple[float, datetime]:
    """Horas desde la última escritura. Propaga si no se puede medir."""
    from services import db
    filas = db.fetch_all(f"SELECT MAX({col}) AS ultima FROM {tabla}")
    ultima = filas[0]["ultima"] if filas else None
    if ultima is None:
        raise RuntimeError(f"{tabla}.{col} no tiene ni una fila con fecha")
    if isinstance(ultima, str):
        ultima = datetime.fromisoformat(ultima)
    if ultima.tzinfo is not None:
        ultima = ultima.replace(tzinfo=None)
    return (datetime.utcnow() - ultima).total_seconds() / 3600.0, ultima


def exigir_viva(tabla: str, *, va_a_escribir: bool, que_decide: str,
                alternativa: str = "") -> None:
    """
    Candado para un script que consulta `tabla` de MySQL para decidir.

    `que_decide`  — en una frase, qué se decide mal si la tabla está detenida.
                    Es lo que verá quien choque con el candado, así que se
                    escribe pensando en esa persona.
    `alternativa` — qué hacer en su lugar (una bandera, otro script, la tabla
                    de kubera). Opcional pero muy recomendable: un muro sin
                    puerta invita a comentar el candado.
    """
    col, congelada_desde = _CONGELABLES.get(tabla, ("updated_at", "-"))
    try:
        horas, ultima = _edad_horas(tabla, col)
    except Exception as exc:  # noqa: BLE001
        # FALLA CERRADA: sin medición no se escribe.
        _di(f"\n  [CANDADO] no se pudo medir la frescura de `{tabla}`: {exc}")
        if va_a_escribir:
            _di("  Sin medicion NO se escribe. Es a proposito: dejar pasar una "
                "escritura porque fallo la comprobacion es el mismo defecto "
                "que este candado tapa.")
            sys.exit(2)
        _di("  (dry-run: sigue, pero el resultado no es confiable)")
        return

    if horas <= _UMBRAL_H:
        return  # viva: el candado se aparta solo

    dias = horas / 24.0
    raya = "-" * 72
    _di(f"\n{raya}")
    _di(f"  [!] `{tabla}` ESTA CONGELADA - ultima escritura hace "
        f"{dias:.1f} dias ({ultima:%d-%b %H:%M})")
    _di(raya)
    _di(f"  Se detuvo el {congelada_desde}")
    _di(f"\n  Este script la consulta para decidir: {que_decide}")
    _di("\n  Una tabla detenida no contesta \"no se\": contesta con seguridad lo")
    _di("  que era cierto el dia que se detuvo. Es el mecanismo de los 964")
    _di("  pedidos fantasma del 12-ago.")
    if alternativa:
        _di(f"\n  En su lugar: {alternativa}")

    if va_a_escribir:
        _di("\n  => NO SE ESCRIBE NADA. Abortando.")
        _di(raya + "\n")
        sys.exit(2)
    _di("\n  => Dry-run: se deja correr para que puedas verlo, pero NO le creas")
    _di(f"     al resultado - esta calculado con datos de hace {dias:.0f} dias.")
    _di(raya + "\n")
