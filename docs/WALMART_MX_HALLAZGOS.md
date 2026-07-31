# Walmart México — reconocimiento técnico (2026-07-31)

> Pruebas hechas contra la API REAL de producción y contra Seller Center con la
> sesión de Brandon. Nada se publicó: todo fue lectura y descarga de plantilla.

## 1. Las credenciales funcionan — y son de México

```
POST https://marketplace.walmartapis.com/v3/token
Authorization: Basic base64(clientId:clientSecret)
WM_MARKET: mx          <-- SIN ESTE HEADER DA 400
WM_SVC.NAME: Walmart Marketplace
WM_QOS.CORRELATION_ID: <uuid>
body: grant_type=client_credentials
```

El **mismo par** de credenciales devuelve `400 Incorrect Authorization header`
sin `WM_MARKET: mx`, y también con `ca`. Es una cuenta de Walmart México sobre el
host global. Token Bearer, **vive 15 minutos** (`expires_in: 900`) — hay que
renovarlo con candado, igual que se hizo con ML.

## 2. La cuenta está VACÍA

| Endpoint | Resultado |
|---|---|
| `GET /v3/items` | 404 "No Items found" |
| `GET /v3/feeds` | `totalResults: 0` |
| `GET /v3/orders` | `totalCount: 0` |
| `GET /v3/utilities/taxonomy` | ✅ 79 categorías raíz |

Cero artículos, cero cargas, cero ventas. Lienzo limpio para probar.

## 3. Lo que la API de MX NO tiene

Endpoints que existen en la API de EE.UU. y aquí devuelven 404:
`/v3/items/spec`, `/v3/price`, `/v3/settings/lagtime`, `/v3/fulfillment/centers`,
`/v3/promo/sku`, `/v3/getReport`.

**Consecuencia**: el esquema de campos NO se puede descubrir por API. Sale de la
plantilla XLSX de Seller Center (sección 4). Ese es el motivo por el que el
reconocimiento tuvo que pasar por el navegador.

`POST /v3/mx/associations` (plantillas de envío por SKU) existe pero devuelve 520
sin items dados de alta — se reprueba cuando haya el primero.

## 4. El esquema real de publicación

Plantilla descargada: `omniintl-marketplace-es_mx-external.xlsx`
(`Version=3.24, marketplace, tools, es_mx, external, Herramientas`).

**200 atributos para UNA categoría: 52 obligatorios, 147 recomendados.**
La hoja `Hidden_tools` trae el mapeo exacto: nombre visible → **nombre XML** →
nivel de requerimiento → tipo de dato. Ese nombre XML es el que consume la API.

### Obligatorios que Kubera YA tiene

`sku` · `productName` · `mainImageUrl` · `keyFeatures` · `shortDescription`
· `price` · `condition`

### Obligatorios que Kubera NO tiene — los bloqueadores

| Campo XML | Tipo | Problema |
|---|---|---|
| `productId` + `productIdType` | String | **GTIN.** Solo 2 SKUs de 7,151 lo tienen |
| `ProductTaxCode` | Integer (8) | **Clave SAT.** No existe en el catálogo. Walmart trae buscador propio |
| `msiEligible` | Boolean | Meses sin intereses — decisión comercial, no dato |
| `hasNomCertification` | Boolean | **Certificación NOM** — específico de México |
| `hazardousMaterialsInd` | Boolean | Material peligroso |
| `countryOfOriginAssembly` | String | País de origen |
| `brand` / `manufacturer` | String | Marca y fabricante — por verificar si existen en Woo |
| `sellerWarranty` + `Condition` + `Period` | String/Int | Garantía del vendedor |
| `itemsIncluded` | String | Qué incluye la caja |
| `countPerPack` · `material` · `colorCategory` | — | Atributos de producto |
| `assembledProduct` L/W/H/Weight | Decimal | Dimensiones del producto ARMADO |

### ⚠️ El campo que ya sabemos que viene mal

`ShippingDimensionsWidth/Height/Depth` + `ShippingWeight` son obligatorios, en
**cm y kg**, y la plantilla los define como *"producto CON EMPAQUE"* — la pieza,
no la caja master.

Es exactamente el bug de `piezas_por_caja` que infla el flete de 13,046 SKUs. Y
aquí pega más fuerte, porque Walmart cobra por **peso volumétrico**:

```
peso_volumétrico = (largo × ancho × alto en cm) / 5000
se cobra el MAYOR entre ése y el peso real
```

Publicar con dimensiones de caja master = flete inflado hasta 3× y precio fuera
de mercado. **Arreglar `piezas_por_caja` es prerrequisito, no mejora.**

## 5. Dos taxonomías que no coinciden

- **API** (`/v3/utilities/taxonomy`): 79 categorías raíz en **inglés**
  (`tools`, `home_other`, `electronics_accessories`, `clothing_other`…)
- **Seller Center**: 74 categorías en **español**, más granulares
  (`Herramientas`, `Cocina, Decoración y Otros`, `Piezas y Accesorios de Autos`…)

Hay que construir el mapeo categoría-Kubera → categoría-Walmart, y cada categoría
tiene su propio juego de atributos obligatorios (la de Herramientas trae
`bladeDiameter`, `shankSize`, `chuckSize`… que no aplican a otras).

## 6. Límites de carga

- **10,000 artículos en máximo 7 categorías por archivo XLSX** (25 MB)
- Para más, varios archivos
- Modelo de entrega: **"Entregado por el vendedor"** = DROP, que es como opera
  Kubera. La alternativa es WFS (bodega de Walmart), fuera de alcance por ahora

## 6-bis. EL CONTRATO DE PUBLICACIÓN — verificado contra producción

Se logró que Walmart **procesara** un artículo real (`recibidos=1`). Todo el
payload validó; el único rechazo fue el GTIN. Esto es el contrato bueno:

```
POST https://marketplace.walmartapis.com/v3/feeds?feedType=MP_ITEM_INTL
  WM_SEC.ACCESS_TOKEN: <token>
  WM_MARKET: mx
  WM_SVC.NAME: Walmart Marketplace
  WM_QOS.CORRELATION_ID: <uuid>
  body: multipart/form-data, campo "file", JSON
```

```json
{
  "MPItemFeedHeader": {
    "subCategory": "tools",          // ID de la API, NO la etiqueta en español
    "sellingChannel": "marketplace",
    "processMode": "REPLACE",
    "mart": "WALMART_MEXICO",        // NO "WALMART_MX"
    "locale": "es",                  // NO "es_MX"
    "version": "3.11",               // NO "3.24" (esa es la de la PLANTILLA XLSX)
    "subset": "EXTERNAL"
  },
  "MPItem": [{ "Orderable": {...}, "Visible": { "Herramientas": {...} } }]
}
```

**Las tres trampas** que costaron 83 intentos fallidos: `mart`, `locale` y
`version` NO son lo que dice la plantilla. La cabecera del XLSX
(`Version=3.24,marketplace,tools,es_mx,external,Herramientas`) decodifica como
versión-de-plantilla, sellingChannel, **subCategory**, locale, subset y categoría
— y solo `subCategory` se reutiliza tal cual en el feed.

**feedTypes válidos en MX**: `MP_ITEM_INTL`, `MP_ITEM_MATCH`, `MP_MAINTENANCE`,
`MP_INVENTORY`, `SKU_TEMPLATE_MAP`, `OMNI_WFSSETUP`, `OMNI_WFSCONVERT`, `item`.
Límites: 10,000 artículos y 10 MB por feed.

**Seguimiento**: `GET /v3/feeds/{feedId}?includeDetails=true` devuelve
`itemsReceived/Succeeded/Failed` y el detalle por SKU con el error exacto.

### Resultado de la prueba real (31-jul, feed `18C77628119B527C808DEC1FE7FF8774`)

```
PROCESSED  recibidos=1  ok=0  fallidos=1
SKU TEC-1571-MET -> DATA_ERROR
   "Please provide a valid Product ID (i.e., GTIN, UPC, EAN, ISSN, ISBN, etc.)"
```

**Prueba concluyente: la exención de GTIN NO está activa en la cuenta.** Todos
los demás campos —precio, imágenes, dimensiones, clave SAT, NOM, categoría,
atributos de la categoría— fueron aceptados sin una sola queja.

## 7. Lo que falta por probar

1. **Confirmar el permiso "sin GTIN"**: Brandon dice que ya lo autorizaron. Falta
   ver qué valor toma `productIdType` en ese caso y si la plantilla cambia.
2. **Publicar 1 artículo real** y luego `GET /v3/items/{sku}` para ver qué
   devuelve la API — el plan original de Brandon, sigue siendo el paso correcto.
3. **Clave SAT**: decidir si se mapea por categoría (una clave por familia) o se
   captura por SKU. Seller Center tiene buscador de códigos fiscales.
4. `POST /v3/mx/associations` con un SKU real, para las plantillas de envío.
5. Verificar si Woo tiene `brand`/`manufacturer` por producto.

## 8. Orden recomendado

1. Arreglar `piezas_por_caja` — bloquea el flete correcto aquí Y corrige los
   13,046 SKUs de los otros canales
2. Confirmar la exención de GTIN y resolver la clave SAT
3. Publicar 1 SKU manual → `GET` → congelar el contrato de campos
4. Recién entonces escribir `services/walmart.py` con el esquema verificado
