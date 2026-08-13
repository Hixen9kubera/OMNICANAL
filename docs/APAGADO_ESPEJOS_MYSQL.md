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

## Corrección: lo que se dijo del barrido estaba MAL medido

La primera versión de este documento decía que el barrido de 15 minutos no le
daba la vuelta al catálogo y que 2,890 publicaciones llevaban 7+ días sin
revisarse. **Ese número no significaba eso.**

`channel.listings.updated_at` NO es "cuándo se revisó": es **"cuándo CAMBIÓ"**.
El upsert lleva `where … is distinct from …`, así que el UPDATE solo dispara si
el dato cambió, y el trigger solo entonces toca la fecha. Una publicación
pausada con precio y stock estables se visita cada 15 minutos y conserva su
fecha de hace diez días. Contarla como "sin revisar" es leer mal la columna.

**Nada de esto tiene que ver con el apagado de los espejos**, que sigue siendo
correcto y verificado.

**Pero al mirar el orden con la columna bien entendida, sí aparece un problema
real**, y es otro: el turno del barrido (`inventario._lote_desde_ml`) se ordena
por esa misma `updated_at`. Una publicación estable tiene la fecha más vieja →
sale elegida → se visita → **como no cambió, su fecha no se mueve** → vuelve a
salir elegida la ronda siguiente. La marca de "ya lo hice" no la pone el
hacerlo.

Arreglarlo pide una columna `last_seen_at` que se escriba en CADA visita, pase
lo que pase con el dato. Esa misma columna es la única forma de medir la
cobertura del barrido, que hoy **no es medible** desde la base.

## Dónde está el detalle

- `CLAUDE.md`, sección **MIGRACIÓN A LA BD KUBERA** — estado por dominio y la
  tabla de los doce lectores repuntados con la versión en que cayó cada uno.
- `README.md`, versiones **v0.118.0 a v0.130.0** — cada repunte con su medición.
- `backend/scripts/vigilar_congelacion.py` — el detector, comentado.
