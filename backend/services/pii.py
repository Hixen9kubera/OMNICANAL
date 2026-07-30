"""
pii.py — Cifrado del único dato personal que guardamos: el nombre del comprador.

POR QUÉ EXISTE
--------------
El cuestionario de cumplimiento de Temu rechazó la solicitud (29-jul) con:

    "For storage of personally identifiable information (PII) such as names,
     phone numbers, addresses, and emails, encryption is required. In this
     context, the term 'user' refers specifically to the recipient."

Auditamos qué guardamos de verdad del comprador:

    nombre    → SÍ (6,592 pedidos, en texto plano)
    correo    → 0
    teléfono  → 0
    dirección → 0

O sea: el nombre es el ÚNICO dato personal en toda la base. Y no se usa en
ningún lado — el panel nunca lo lee (ni Ventas, ni las tarjetas, ni los
reportes); solo se escribía porque WooCommerce pide un `billing` al crear el
pedido. Por eso cifrarlo no rompe nada del flujo.

CÓMO FUNCIONA
-------------
Fernet (AES-128-CBC + HMAC-SHA256, de la librería `cryptography`, que ya estaba
instalada). La llave vive SOLO en la variable de entorno `PII_KEY` de Railway —
nunca en el repositorio.

    cifrar("Juan Pérez")  ->  "enc:gAAAAABo…"

El prefijo `enc:` hace la operación IDEMPOTENTE: un valor ya cifrado se
reconoce y no se vuelve a cifrar (clave para que el barrido de históricos se
pueda repetir sin dañar nada).

SIN LLAVE NO SE ROMPE NADA: si `PII_KEY` no está definida, `cifrar()` devuelve
un marcador genérico ("Comprador") en vez del nombre real. Preferimos perder el
dato a guardarlo en claro creyendo que va cifrado — un fallo silencioso aquí
sería exactamente lo que el cuestionario castiga.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("omnicanal.pii")

PREFIJO = "enc:"
_GENERICO = "Comprador"
_fernet = None
_intentado = False


def _motor():
    """Fernet listo, o None si no hay llave configurada."""
    global _fernet, _intentado
    if _intentado:
        return _fernet
    _intentado = True
    llave = (os.getenv("PII_KEY") or "").strip()
    if not llave:
        log.warning("PII_KEY no está definida: los nombres de comprador se "
                    "guardarán como genéricos (nunca en claro).")
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(llave.encode())
    except Exception as exc:  # noqa: BLE001
        log.error("PII_KEY inválida (%s): se usarán nombres genéricos.", exc)
        _fernet = None
    return _fernet


def habilitado() -> bool:
    return _motor() is not None


def esta_cifrado(valor: str | None) -> bool:
    return bool(valor) and str(valor).startswith(PREFIJO)


def cifrar(valor: str | None) -> str:
    """
    Texto plano → `enc:…`. Idempotente y a prueba de fallos.

    Nunca devuelve el valor original en claro: si no hay llave, devuelve el
    marcador genérico.
    """
    v = (valor or "").strip()
    if not v:
        return ""
    if esta_cifrado(v):
        return v                      # ya cifrado: no se re-cifra
    f = _motor()
    if f is None:
        return _GENERICO
    try:
        return PREFIJO + f.encrypt(v.encode()).decode()
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo cifrar un nombre: %s", exc)
        return _GENERICO


def descifrar(valor: str | None) -> str | None:
    """
    `enc:…` → texto plano. None si no se puede (sin llave o valor corrupto).

    Solo para soporte puntual: el panel NO descifra nada en su operación normal.
    """
    v = (valor or "").strip()
    if not esta_cifrado(v):
        return v or None
    f = _motor()
    if f is None:
        return None
    try:
        return f.decrypt(v[len(PREFIJO):].encode()).decode()
    except Exception:  # noqa: BLE001
        return None


def generar_llave() -> str:
    """Genera una llave nueva. Se corre UNA vez y se guarda en Railway."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()
