"""
rbac.py — Quién puede hacer qué. Responde la pregunta III.2 de Temu.

    "¿Hay control de acceso por rol, con 'need-to-know' y 'mínimo privilegio',
     de modo que cada usuario acceda solo a los datos y funciones que su puesto
     necesita?"

Este archivo ES la respuesta: una tabla declarativa, legible de un vistazo, que
se puede adjuntar como evidencia. Los tres roles son los que ya define
`core.usuarios` con un CHECK, así que la base de datos y el código no pueden
desincronizarse.

    admin     manda todo: publicar, precios masivos, configuración, bitácora,
              y lo único que toca datos personales de compradores.
    operador  el trabajo diario: contenido, imágenes, categorías, vistas
              previas. NO publica a marketplaces, NO mueve precios en masa,
              NO ve datos de compradores. Ese "no" es literalmente el
              need-to-know que pregunta el cuestionario.
    lectura   solo mira: reportes, ventas, inventario.

DOS DECISIONES QUE IMPORTAN
---------------------------
1. **Lo que no está listado exige `admin`.** Un endpoint nuevo nace CERRADO.
   Si mañana alguien agrega una ruta y olvida clasificarla, el peor caso es que
   un operador reciba un 403 y lo reporte — no que quede abierta sin que nadie
   se entere.

2. **Los GET son de `lectura` salvo excepción.** Leer el catálogo no es
   sensible. Leer costos, márgenes o la bitácora sí, y por eso están escritos
   uno por uno más abajo.

La autoridad vive AQUÍ, en el backend. El frontend esconde botones según el rol,
pero eso es cosmética: quien llame la API directo se topa con esta tabla.
"""
from __future__ import annotations

# Jerarquía: un admin puede lo de un operador, y un operador lo de lectura.
_NIVEL = {"lectura": 1, "operador": 2, "admin": 3}


def _n(rol: str) -> int:
    return _NIVEL.get(rol, 0)


# (método, prefijo de ruta) -> rol mínimo. Se evalúa por prefijo MÁS LARGO
# primero, así que una regla específica gana sobre una general.
REGLAS: tuple[tuple[str, str, str], ...] = (
    # --- ADMIN: mueve dinero, publica al mundo, o toca configuración ---
    ("POST", "/api/publicar/confirmar", "admin"),        # crea/actualiza en ML y Amazon
    ("POST", "/api/sync/woo", "admin"),                  # stock y precios de TODO el catálogo
    ("POST", "/api/crear/costos/bulk", "admin"),         # precios en masa
    ("POST", "/api/crear/costos", "admin"),              # recalcular precio de un SKU
    ("POST", "/api/fanout", "admin"),                    # empuja stock a los canales
    ("POST", "/api/migracion/backfill", "admin"),
    ("POST", "/api/migracion/errores", "admin"),
    ("GET", "/api/migracion", "admin"),
    ("POST", "/api/webhooks/pausar", "admin"),           # apaga la captura de ventas
    ("POST", "/api/webhooks/reanudar", "admin"),
    ("GET", "/api/webhooks/pausar", "admin"),
    ("GET", "/api/webhooks/reanudar", "admin"),
    ("GET", "/api/webhooks/estado", "admin"),            # expone flags de producción
    ("GET", "/api/webhooks/ml/log", "admin"),
    ("GET", "/api/auditoria", "admin"),                  # la bitácora misma
    # Costos y márgenes = el P&L del negocio. No es dato de operación diaria.
    ("GET", "/api/crear/costos", "admin"),
    ("GET", "/api/fulfillment", "admin"),

    # --- OPERADOR: el trabajo del día ---
    ("POST", "/api/productos", "operador"),              # contenido: título, descripción
    ("POST", "/api/imagenes", "operador"),
    ("POST", "/api/crear", "operador"),                  # alta de productos, categorías
    ("POST", "/api/publicar/preview", "operador"),       # ver qué se mandaría, sin mandarlo
    ("POST", "/api/publicar/amazon", "operador"),
    ("POST", "/api/ia", "operador"),                     # gasta créditos de IA
    ("POST", "/api/canales", "operador"),
    ("POST", "/api/sync/catalogo", "operador"),          # refresca índices, barato

    # --- LECTURA: mirar ---
    ("GET", "/api/productos", "lectura"),
    ("GET", "/api/canales", "lectura"),
    ("GET", "/api/ventas", "lectura"),
    ("GET", "/api/crear", "lectura"),
    ("GET", "/api/imagenes", "lectura"),
    ("GET", "/api/publicar", "lectura"),
    ("GET", "/api/fanout", "lectura"),
    ("GET", "/api/sync", "lectura"),
    ("GET", "/api/ia", "lectura"),
    ("GET", "/api/auth", "lectura"),
    ("GET", "/api/webhooks/notificaciones", "lectura"),  # la campana del panel
)

# Índice ordenado por prefijo más largo: la regla específica gana.
_ORDENADAS = tuple(sorted(REGLAS, key=lambda r: len(r[1]), reverse=True))

ROL_POR_DEFECTO = "admin"   # lo no listado nace cerrado — ver decisión 1


def rol_requerido(metodo: str, ruta: str) -> str:
    """Rol mínimo para ejecutar `metodo ruta`."""
    m = (metodo or "").upper()
    for regla_m, prefijo, rol in _ORDENADAS:
        if m == regla_m and ruta.startswith(prefijo):
            return rol
    return ROL_POR_DEFECTO


def permite(rol_usuario: str, metodo: str, ruta: str) -> bool:
    """True si ese rol alcanza para esa operación."""
    return _n(rol_usuario) >= _n(rol_requerido(metodo, ruta))
