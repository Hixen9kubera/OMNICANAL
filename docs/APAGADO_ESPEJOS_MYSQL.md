# Apagado de los espejos a MySQL — procedimiento y contexto

> Para todo el equipo. Escrito el **12-ago-2026**, antes de apagar.
> Si estás leyendo esto porque algo se ve raro, salta a **"Cómo se revierte"**:
> son 30 segundos y no requiere deploy.

## Qué se apaga

Tres variables en Railway (BackendOmnicanal, production):

| Variable | Qué deja de pasar |
|---|---|
| `COSTING_ESPEJO_INVERSO` | los costos dejan de copiarse a `costos_validados` / `costos_finales` / `costos_logs` de MySQL |
| `CHANNEL_ESPEJO_INVERSO` | el inventario deja de copiarse a `canal_inventario` |
| `ORDERS_ESPEJO_INVERSO` | los pedidos dejan de copiarse a `pedidos_ml` |

**No se apaga ningún flujo de negocio.** Las ventas siguen entrando, el stock
sigue empujándose, los costos se siguen calculando. Lo único que se detiene es
la COPIA hacia la base vieja `u531713409_kubera_ml`, que ya no manda desde el
corte del 7 y 10 de agosto.

## Por qué ahora

Se intentó el 11 y 12 de agosto y **se revirtió**: congelar `pedidos_ml` dejó
ciegas a tres consultas del flujo de alta y nacieron **964 pedidos fantasma en
4 h 17 min ($409,741)**. La lección quedó escrita en CLAUDE.md y vale repetirla:

> **Congelar una tabla es cambiar el contrato de LECTURA, no solo el de
> escritura.** Un arnés de paridad mide si los datos coinciden, no si alguien
> toma decisiones con ellos. Y un `None` de una tabla detenida no significa
> "no existe": significa **"ya no sé"**.

Desde entonces se hizo el paso que faltaba: buscar **quién LEE** esas tablas
para decidir algo, y repuntarlo a kubera. Doce sitios, cada uno con su medición
de paridad previa (v0.118.0 a v0.129.0). El barrido incluyó los ETLs de las
06:15, que hoy ya no abren MySQL, y se verificó que la base vieja no tiene
vistas, disparadores, rutinas ni llaves foráneas sobre esas cinco tablas.

## Antes de apagar

```bash
backend/.venv/Scripts/python.exe backend/scripts/vigilar_congelacion.py
```

Guarda la salida. Es la línea base. La del 12-ago 04:18 UTC:

```
  [OK  ] turno_sync   hace   226.1 h (tope 240 h) · 79 de 6637 tocadas en 1 h
  [OK  ] pedidos      hace     0.0 h (tope 6 h)
  [OK  ] costos       hace    56.2 h (tope 72 h)
  [OK  ] padron       hace    22.0 h (tope 30 h)

    costos_finales       hace 56.2 h  → congelada
    pedidos_ml           hace  0.0 h  → AÚN ESCRIBIENDO
    canal_inventario     hace  0.0 h  → AÚN ESCRIBIENDO
```

## Después de apagar

Correr lo mismo. Lo que debe pasar:

- Las tres tablas del espejo pasan a **`congelada`** (tarda ~1 h en notarse).
  Si alguna sigue diciendo `AÚN ESCRIBIENDO` pasada una hora, **la variable no
  se aplicó** — en Railway las variables quedan *staged* hasta el `accept-deploy`.
- Los cuatro latidos siguen en `OK`. Son de kubera, no del espejo: si uno se
  apaga, algo dejó de moverse y hay que mirarlo.

Vale la pena correrlo a las pocas horas y otra vez al día siguiente.

## Cómo se revierte

Poner la variable de vuelta en `true` y aplicar. **No requiere deploy de código
ni tocar el repo.** El espejo vuelve a escribir en la siguiente operación.

Se puede revertir **un dominio solo**: si lo que se ve raro son los costos, no
hace falta tocar pedidos ni inventario.

## Lo que NO hay que hacer mientras tanto

**No aprietes el botón de sincronizar stock con la tienda**
(`POST /api/sync/woo`). Eso es independiente del apagado y ya estaba roto
antes: ese barrido toma el stock de **Odoo** y lo escribe en WooCommerce, pero
Odoo no sabe nada del stock de drop. Medido el 12-ago: **pondría en CERO a 8,120
SKUs (62% del catálogo), 1.1 millones de piezas**, y el fan-out empujaría esos
ceros a Mercado Libre y Amazon. Ninguno subiría.

El vigilante de Odoo de cada 30 minutos **no** hace eso: solo toca la campana
(`ODOO_WATCH_AUTO_PUSH` viene apagado). El peligro es únicamente ese botón.

Tampoco corras a mano los scripts de mantenimiento que todavía leen MySQL —
`alinear_ml_drop`, `alinear_amazon_drop`, `marcar_amazon_muertas`,
`corregir_status_publicados`, `corregir_stock_woo_full`,
`sincronizar_ml_huerfanas`, `publicar_walmart`, `sync_odoo_woo_seguro`—
sin repuntarlos antes: después del apagado leerían una foto detenida.

## Un problema PREVIO que salió al medir (no lo causa el apagado)

El barrido de 15 minutos no le da la vuelta al catálogo: toca ~77
publicaciones por hora y **2,890 publicaciones vivas llevan más de 7 días sin
revisarse** (1,653 de ML y 1,237 de Amazon, el 64% de las vivas).

No bloquea el apagado y conviene entender por qué: **el mismo barrido escribe
las dos tablas**, la de kubera y el espejo, así que las dos están igual de
viejas. Apagar la copia no cambia la frescura de nada.

Nadie lo había visto porque todos miraban la revisión **más reciente**, que
siempre se ve bien. El detector mira la **más vieja**. Queda como tarea aparte:
revisar `SYNC_BATCH` y el orden del turno en `inventario.py`.

## Dónde está el detalle

- `CLAUDE.md`, sección **MIGRACIÓN A LA BD KUBERA** — estado por dominio y la
  tabla de los doce lectores repuntados con la versión en que cayó cada uno.
- `README.md`, versiones **v0.118.0 a v0.130.0** — cada repunte con su medición.
- `backend/scripts/vigilar_congelacion.py` — el detector, comentado.
