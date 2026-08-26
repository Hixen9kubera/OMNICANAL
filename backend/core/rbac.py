"""
rbac.py — Quién puede hacer qué. Responde la pregunta III.2 de Temu.

    "¿Hay control de acceso por rol, con 'need-to-know' y 'mínimo privilegio',
     de modo que cada usuario acceda solo a los datos y funciones que su puesto
     necesita?"

Este archivo ES la respuesta: una tabla declarativa, legible de un vistazo, que
se puede adjuntar como evidencia. Los tres roles son los que ya define
`core.usuarios` con un CHECK, así que la base de datos y el código no pueden
desincronizarse.

    admin     Brandon, Eduardo y José. Todo, incluida la INFRAESTRUCTURA:
              migración, webhooks, fan-out de stock, sincronización masiva
              Odoo→Woo y la bitácora.
    operador  El rol que el equipo llama **KAM** (la tabla `core.usuarios`
              tiene un CHECK que solo admite tres nombres, así que "KAM" se
              guarda como `operador` y el panel lo rotula como KAM). Hace su
              trabajo comercial COMPLETO: catálogo, contenido, imágenes,
              publicar a marketplaces, costos y competencia.
    lectura   Solo mira ventas e inventario. SIN costos ni márgenes.

DÓNDE ESTÁ EL LÍMITE (esto es lo que responde el "need-to-know")
----------------------------------------------------------------
El corte NO es "mirar vs escribir" — un KAM publica y cambia precios, porque
ese ES su trabajo (decisión de Brandon, 4-ago). El corte es entre el TRABAJO
COMERCIAL y la INFRAESTRUCTURA que lo sostiene. Un KAM no puede:

  · apagar la captura de ventas   (POST /api/webhooks/pausar)
  · correr backfills de migración (POST /api/migracion/backfill/*)
  · barrer stock y precios de TODO el catálogo (POST /api/sync/woo)
  · empujar inventario a los canales (POST /api/fanout/*)
  · leer la bitácora de auditoría  (GET  /api/auditoria)

Un error ahí no daña un producto: daña el sistema entero o borra el rastro de
quién hizo qué. Por eso vive en otro rol y no en el que usan ocho personas.

Las pestañas del panel corresponden 1:1 con esta tabla — Operaciones,
Migración y Facturas son de admin; las otras seis son del KAM.

DOS DECISIONES QUE IMPORTAN
---------------------------
1. **Lo que no está listado exige `admin`.** Un endpoint nuevo nace CERRADO.
   Si mañana alguien agrega una ruta y olvida clasificarla, el peor caso es que
   un KAM reciba un 403 y lo reporte — no que quede abierta sin que nadie se
   entere. Lo mismo con los métodos: solo se listan los que la app usa, así que
   un PUT o un DELETE nuevo también nace cerrado.

2. **Los GET son de `lectura` salvo excepción.** Leer el catálogo no es
   sensible. Costos y márgenes sí, y por eso `/api/crear/costos` y
   `/api/fulfillment` (que devuelve `costo` y `margen_pct`) piden `operador`
   aunque sean de solo lectura.

La autoridad vive AQUÍ, en el backend. El frontend esconde pestañas según el
rol, pero eso es cosmética: quien llame la API directo se topa con esta tabla.
"""
from __future__ import annotations

# Jerarquía: un admin puede lo de un operador, y un operador lo de lectura.
_NIVEL = {"lectura": 1, "operador": 2, "admin": 3}


def _n(rol: str) -> int:
    return _NIVEL.get(rol, 0)


# (método, prefijo de ruta) -> rol mínimo. Se evalúa por prefijo MÁS LARGO
# primero, así que una regla específica gana sobre una general.
REGLAS: tuple[tuple[str, str, str], ...] = (
    # ══ ADMIN — pestañas Operaciones, Migración y Facturas ═══════════════════
    # Todo lo de aquí abajo mueve la INFRAESTRUCTURA, no un producto.
    ("POST", "/api/sync/woo", "admin"),                  # barre stock y precios de TODO
    ("POST", "/api/sync/leer", "admin"),
    ("POST", "/api/fanout", "admin"),                    # empuja inventario a los canales
    ("GET", "/api/fanout", "admin"),                     # expone flags de producción
    ("POST", "/api/migracion", "admin"),                 # backfills y reproceso de errores
    ("GET", "/api/migracion", "admin"),
    ("POST", "/api/webhooks/pausar", "admin"),           # apaga la captura de ventas
    ("POST", "/api/webhooks/reanudar", "admin"),
    ("GET", "/api/webhooks/pausar", "admin"),
    ("GET", "/api/webhooks/reanudar", "admin"),
    ("GET", "/api/webhooks/estado", "admin"),
    ("GET", "/api/webhooks/ml/log", "admin"),
    ("GET", "/api/auditoria", "admin"),                  # la bitácora misma
    ("POST", "/api/crear/destrabar", "admin"),           # fuerza un lote atorado

    # ══ OPERADOR (KAM) — sus seis pestañas, completas ════════════════════════
    # Análisis · trae `costo` y `margen_pct`, por eso no baja a lectura.
    ("GET", "/api/fulfillment", "operador"),
    # Productos · contenido, título, descripción, imágenes
    ("POST", "/api/productos", "operador"),
    ("POST", "/api/imagenes", "operador"),
    # Omnicanal · refrescar el estado de un SKU en un canal
    ("POST", "/api/canales", "operador"),
    # Crear Productos · alta, categorías, GTIN, IA, y PUBLICAR de verdad
    ("POST", "/api/crear", "operador"),
    ("POST", "/api/ia", "operador"),                     # gasta créditos de IA
    ("POST", "/api/sync/catalogo", "operador"),          # refresca índices, barato
    ("POST", "/api/publicar/preview", "operador"),       # ver qué se mandaría
    ("POST", "/api/publicar/amazon", "operador"),        # tipo de producto
    ("POST", "/api/publicar/confirmar", "operador"),     # crea/actualiza en ML y Amazon
    # Costos · ver el P&L y recalcular precios, incluido el masivo
    ("GET", "/api/crear/costos", "operador"),
    ("POST", "/api/crear/costos", "operador"),           # /preview, /recalcular y /bulk
    # Costos · "Resolver": compara un packing list contra costos_validados.
    ("GET", "/api/resolver", "operador"),
    ("POST", "/api/resolver", "operador"),
    ("PATCH", "/api/resolver", "operador"),
    # Análisis · stock, ventas y MARGEN con los cobros del canal. Va en
    # `operador` y no en `lectura` por el mismo criterio que /api/fulfillment:
    # expone costo y margen, que no son para el nivel de solo-lectura.
    #
    # ⚠️ FALTABA POR COMPLETO. `/api/analisis` no estaba listado, así que TODO
    # lo que cuelga de ahí caía al default de admin (decisión 1: lo no listado
    # nace cerrado). El síntoma fue un KAM viendo "Métricas" en el menú, entrando
    # y topándose con un 403 — el backend bloqueaba y el frontend no escondía.
    # Medido el 26-ago al encender RBAC_ENFORCED: 3 rechazos, todos de
    # `GET /api/analisis/metricas`, todos del mismo usuario.
    ("GET", "/api/analisis", "operador"),
    # Competencia · sembrar SKUs, correr el rastreo, ajustar términos
    ("GET", "/api/competencia", "operador"),
    ("POST", "/api/competencia", "operador"),
    ("PATCH", "/api/competencia", "operador"),

    # ══ LECTURA — mirar ventas e inventario, sin costos ══════════════════════
    ("GET", "/api/productos", "lectura"),
    ("GET", "/api/canales", "lectura"),
    ("GET", "/api/ventas", "lectura"),
    ("GET", "/api/crear", "lectura"),
    ("GET", "/api/imagenes", "lectura"),
    ("GET", "/api/publicar", "lectura"),
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
