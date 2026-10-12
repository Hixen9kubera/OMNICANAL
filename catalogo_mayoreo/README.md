# Catálogo de mayoreo

El catálogo que ven las tiendas de mayoreo: qué hay, cuántas piezas y a qué precio, con un carrito
para pedir **lotes completos** (un producto con todas sus piezas, o una subcategoría entera). El
pedido se vuelve una orden de venta en Odoo, que es lo que aparta la mercancía, y se avisa en el
canal `#catalogo-mayoreo` de Slack.

Es un módulo **aparte** del panel, en su propio servicio de Railway (`catalogo-mayoreo`), y vive en
la rama `catalogo-mayoreo`: nunca se mezcla a `main`.

## Por qué vive aparte

Es lo único de Kubera pensado para que lo abra gente de fuera, así que no comparte nada con el panel:

- **No tiene base de datos.** Ni cadena de conexión, ni llaves de Supabase, ni sesión del panel.
- **Lo que sabe del inventario es un archivo** (`catalogo.json` + fotos) que se le sube ya armado.
  No trae costos, proveedor ni contenedor.
- **Lo único que puede escribir** es una orden de venta en Odoo, y solo con el código de una tienda
  autorizada.
- La página no lleva llaves: lee el catálogo, guarda el pedido en el navegador y lo manda a un endpoint
  que **vuelve a validar todo**. Del navegador solo se cree el SKU; precio y tamaño del lote salen
  del catálogo publicado.

## Qué hay

| Archivo | Qué hace |
|---|---|
| `web/index.html` | La página (diseño de Claude Design, 12-oct-2026). Un solo archivo, sin dependencias. |
| `app.py` | El servidor: página, catálogo, `POST /api/mayoreo/pedidos`, frenos y subida del catálogo. |
| `pedidos.py` | Las reglas de un pedido, sin red (se prueban solas). |
| `servicio.py` | Configuración y el camino de un pedido: Odoo → Slack → su archivo. |
| `odoo.py` | Lo único que habla con Odoo (XML-RPC). |
| `publicar.py` | Sube un catálogo nuevo al módulo, desde la máquina que lo generó. |
| `tiendas.py` | Da de alta una tienda: inventa su código y arma su renglón. |
| `tests/` | `python -m unittest discover -s tests` (Odoo es un doble en memoria; no usa red). |

## Las reglas del pedido

1. **No se venden piezas sueltas.** Cada renglón es el lote completo del catálogo.
2. **Se aparta lo que haya**: `min(lote del catálogo, libre en Odoo en ese momento)`, de TEXCO y
   TEXCO II (los mismos almacenes y el mismo orden que `odoo_ventas`; DROP OFF no surte órdenes).
   Una orden por almacén con piezas.
3. **Un SKU que no existe en Odoo no se rechaza** (Brandon, 12-oct-2026): queda «por confirmar» y
   en Slack sale la lista completa para apartarlo a mano.
4. **A Odoo se le relee.** Un renglón se reporta «apartado» solo si Odoo dejó la orden en `sale`.
   En borrador no aparta, y se dice «en revisión».
5. **Reenviar no duplica.** El folio sale de la tienda y de la llave del pedido; las órdenes llevan
   `client_order_ref = FOLIO#n`. Un reintento tras una caída crea solo lo que faltó.
6. Un pedido grande tarda minutos en Odoo: a los 25 s el servidor contesta «en proceso» y la página
   vuelve a preguntar con la misma llave.

## Variables (Railway › catalogo-mayoreo)

| Variable | Para qué |
|---|---|
| `MAYOREO_PEDIDOS` | `demo` (por omisión: la página simula la respuesta y no manda nada), `activo` o `pausa`. |
| `MAYOREO_CONFIRMAR` | `true` = la orden se confirma sola y aparta al instante. Sin ella queda en borrador (pasa a revisión). |
| `MAYOREO_VIGENCIA` | Texto de cuánto dura el apartado («48 horas»). Vacío = no se menciona. |
| `MAYOREO_TIENDAS` | Lista JSON de tiendas: `[{"nombre","hash","partner_id","activa"}]`. La arma `tiendas.py`. |
| `MAYOREO_PARTNER_ID` | Cliente de Odoo para las tiendas sin `partner_id` propio. |
| `ODOO_URL` `ODOO_DB` `ODOO_USER` `ODOO_PASSWORD` | Por referencia: `${{BackendOmnicanal.ODOO_URL}}`, etc. |
| `SLACK_WEBHOOK_CATALOGO` | Webhook entrante del canal `#catalogo-mayoreo`. |
| `MAYOREO_ADMIN_KEY` | Llave para subir el catálogo y ver los pedidos recibidos. |
| `MAYOREO_ALMACENES` | Por omisión `135:TEXCO,150:TEXCO II`. |

Pedir `activo` con algo de esto sin poner **deja el módulo en pausa** y lo dice en el log de arranque.

## Encender los pedidos

Es un flujo de negocio vivo (regla 3 del `CLAUDE.md`): lo decide Brandon. Al encenderlo:

- Cada pedido crea órdenes de venta en Odoo. Con `MAYOREO_CONFIRMAR=true` además **aparta**: el libre
  baja, `stock_watch` lo copia a WooCommerce y el fan-out lo lleva a Temu y TikTok. Una subcategoría
  completa puede dejar cientos de publicaciones en cero.
- El almacén recibe una entrega (picking) por cada orden confirmada.
- Nadie libera un apartado que no se paga: hoy eso es cancelar la orden a mano en Odoo.

Pasos: crear el webhook de Slack del canal, dar de alta las tiendas (`tiendas.py`), poner las
variables de Odoo por referencia y `MAYOREO_PEDIDOS=activo`.

## Actualizar el catálogo

```bash
# 1 · generar (rama `conocimiento`; solo lee: Odoo por XML-RPC y los archivos del inventario)
python conocimientoGeneral/CATALOGO_VIVO/scripts/catalogo_vivo.py mayoreo --salida <…/reportes/catalogo_vivo>

# 2 · subir y poner a la vista (lee MAYOREO_URL y MAYOREO_ADMIN_KEY del entorno o de catalogo_mayoreo/.env)
python catalogo_mayoreo/publicar.py --carpeta <…/reportes/catalogo_vivo/mayoreo>
```

El catálogo nuevo llega a una antesala y solo se publica si está completo; el anterior se conserva
una vez (`/data/anterior`). Las existencias son las de Odoo al momento de generar: hay que correrlo
cada vez que se quiera un corte nuevo.

## Lo que falta decidir

- ¿La orden se confirma sola o la aprueba una persona? (hoy: pasa a revisión)
- ¿Cuánto dura un apartado y quién lo libera?
- El precio: hoy es la media de Mercado Libre (con IVA), que en la mayoría es la de la categoría.
- ¿A qué cliente de Odoo va cada tienda?
- ¿El catálogo se ve sin código, como ahora, o se pide el código para entrar?
- Fotos más grandes para la ventana de la foto (hoy 256 px).
