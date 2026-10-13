# Catálogo de mayoreo

El catálogo que ven las tiendas de mayoreo: qué hay, cuántas piezas y a qué precio, con un carrito
de **lotes completos** (un producto con todas sus piezas, o una subcategoría entera). Quien lo usa
dice **cuánto ofrece por todo** y lo manda: la oferta se avisa en el canal `#catalogo-mayoreo` de
Slack y alguien la revisa y le responde.

Es un módulo **aparte** del panel, en su propio servicio de Railway (`catalogo-mayoreo`), y vive en
la rama `catalogo-mayoreo`: nunca se mezcla a `main`.

## Por qué vive aparte

Es lo único de Kubera pensado para que lo abra gente de fuera, así que no comparte nada con el panel:

- **No tiene base de datos.** Ni cadena de conexión, ni llaves de Supabase, ni sesión del panel.
- **Lo que sabe del inventario es un archivo** (`catalogo.json` + fotos) que se le sube ya armado.
  No trae costos, proveedor ni contenedor.
- La página no lleva llaves: lee el catálogo, guarda lo elegido en el navegador y lo manda a un
  endpoint que **vuelve a validar todo**. Del navegador solo se cree qué SKUs eligió y cuánto ofrece;
  precio y tamaño del lote salen del catálogo publicado.

## Qué hay

| Archivo | Qué hace |
|---|---|
| `web/index.html` | La página (diseño de Claude Design, 12-oct-2026). Un solo archivo, sin dependencias. |
| `app.py` | El servidor: página, catálogo, `POST /api/mayoreo/ofertas`, frenos y subida del catálogo. |
| `ofertas.py` | Las reglas de una oferta, sin red: validarla, medir qué subcategorías van enteras, el aviso. |
| `pedidos.py` | Lo común (catálogo, renglones, lotes completos) y las reglas del pedido que aparta en Odoo. |
| `servicio.py` | Configuración, el camino de una oferta (Slack → su archivo) y el del pedido (Odoo). |
| `odoo.py` | Lo único que habla con Odoo (XML-RPC). Hoy no se usa: ver «Pedidos que apartan». |
| `publicar.py` | Sube un catálogo nuevo al módulo, desde la máquina que lo generó. |
| `tiendas.py` | Da de alta una tienda con su código (para cuando vuelvan los códigos). |
| `tests/` | `python -m unittest discover -s tests` (Odoo es un doble en memoria; no usa red). |

## Cómo funciona una oferta

1. La tienda elige en el catálogo. **No se venden piezas sueltas:** cada renglón es el lote completo.
2. En «Tu pedido» (y otra vez al revisar) escribe **cuánto ofrece por todo**, su nombre y, si quiere,
   un teléfono y notas. **No se pide código de tienda** (Brandon, 13-oct-2026).
3. Todo lo que eligió y escribió se guarda en su navegador: recargar la página no lo borra. Se
   limpia cuando la oferta se envía (el nombre y el teléfono se quedan para la siguiente).
4. Al enviar, el servidor valida contra el catálogo publicado, **avisa en Slack** y guarda la oferta
   en su volumen (`/data/ofertas/OFE-….json`). **No toca Odoo ni aparta nada.**
5. Si Slack no recibe el aviso, la oferta **no** se da por recibida: la página pide reintentar.
6. Reenviar la misma oferta no la duplica (el folio sale de su llave) ni vuelve a sonar en Slack.

El aviso dice quién, cuánto ofrece, por cuántos productos y piezas, el precio de catálogo y qué
porcentaje es la oferta; las subcategorías que van enteras (medido contra el catálogo, no se le cree
al navegador) y, uno por uno, los productos sueltos (hasta 60). Los textos que escribe la tienda van
escapados: un `<!channel>` en el nombre no llama a nadie.

Como no hay código, cualquiera con la liga puede mandar una oferta. Los frenos: 6 seguidas por IP y
luego una cada 2 minutos; entre todos, 40 seguidas y luego 60 por hora.

## Variables (Railway › catalogo-mayoreo)

| Variable | Para qué |
|---|---|
| `SLACK_WEBHOOK_CATALOGO` | Webhook entrante del canal `#catalogo-mayoreo`. **Con ella las ofertas quedan activas**; sin ella la página está en demostración (arma la oferta y enseña la respuesta, sin mandar nada). |
| `MAYOREO_OFERTAS` | Opcional: `pausa` deja de recibir ofertas; `demo` fuerza la demostración. Vacía = lo normal. |
| `MAYOREO_ADMIN_KEY` | Llave para subir el catálogo y ver las ofertas recibidas. |
| `MAYOREO_DATOS` | Dónde vive el volumen (`/data`). |

Ver las ofertas recibidas: `GET /api/mayoreo/admin/ofertas` con la cabecera `X-Admin-Key`
(`?folio=OFE-…` trae una completa).

## Actualizar el catálogo

```bash
# 1 · generar (rama `conocimiento`; solo lee: Odoo por XML-RPC y los archivos del inventario)
python conocimientoGeneral/CATALOGO_VIVO/scripts/catalogo_vivo.py mayoreo --salida <…/reportes/catalogo_vivo>

# 2 · subir y poner a la vista (lee MAYOREO_URL y MAYOREO_ADMIN_KEY del entorno o de catalogo_mayoreo/.env)
python catalogo_mayoreo/publicar.py --carpeta <…/reportes/catalogo_vivo/mayoreo>
```

El catálogo nuevo llega a una antesala y solo se publica si está completo; el anterior se conserva
una vez (`/data/anterior`). Las existencias son las de Odoo al momento de generar: hay que correrlo
cada vez que se quiera un corte nuevo. Los precios salen de la página del inventario y, donde lo
haya, del paquete «precios de Mercado Libre v3» de Competencia (sin marcas de renombre).

## Pedidos que apartan en Odoo (apagado)

La primera versión pedía un **código de tienda** y convertía el pedido en órdenes de venta de Odoo
(una por almacén, TEXCO y TEXCO II), que es lo que aparta la mercancía. Ese camino sigue en el código
con sus pruebas —`POST /api/mayoreo/pedidos`, `servicio.procesar`, `odoo.py`, `tiendas.py`— para cuando
se quiera de vuelta, pero **la página no lo llama** y solo responde con `MAYOREO_PEDIDOS=activo` y todo
configurado (`ODOO_*`, `MAYOREO_TIENDAS`, cliente de Odoo, Slack). Sus reglas:

- Se aparta `min(lote del catálogo, libre en Odoo en ese momento)`; un SKU que no existe en Odoo no
  se rechaza: queda «por confirmar» y Slack lleva la lista para apartarlo a mano.
- A Odoo se le relee: «apartado» solo si la orden quedó en `sale`; en borrador no aparta.
- Reenviar no duplica (`client_order_ref = FOLIO#n`) y un reintento crea solo lo que faltó.

Encenderlo es un flujo de negocio vivo (regla 3 del `CLAUDE.md`): lo decide Brandon. Confirmar una
orden baja el libre en Odoo, y eso llega a WooCommerce, Temu y TikTok.

## Lo que falta decidir

- Qué pasa cuando se **acepta** una oferta: hoy es a mano (alguien crea la orden en Odoo).
- El precio de catálogo: parte del de mercado en Mercado Libre, con IVA, y la etapa `mayoreo` le aplica
  un ajuste interno (un archivo fuera de git) antes de publicarlo. La página no dice de dónde sale.
- ¿El catálogo se ve sin código, como ahora, o se pide algo para entrar?
- Fotos más grandes para la ventana de la foto (hoy 256 px).
