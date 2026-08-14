# TikTok Shop MX — qué nos cuesta vender ahí

> **Investigado el 13-ago-2026.** Separa lo MEDIDO en nuestra tienda de lo
> PUBLICADO por terceros, porque no coinciden y la diferencia importa.
> **Regla de este documento: si no lo pude verificar, lo digo.**

---

## LA RESPUESTA CORTA

| Costo | Cuánto | ¿Verificado? |
|---|---|---|
| **Envío** | **$0 para Kubera.** TikTok lo absorbe entero | ✅ **medido en 11 pedidos reales** |
| **Comisión por categoría** | **No se puede saber todavía** | ❌ ver abajo |
| Tarifa de transacción | existe, monto no publicado | ⚠️ sólo confirmada su existencia |

**La comisión real no es consultable hoy, y no por falta de intentos:** TikTok
la liquida en el *statement*, y **nuestra tienda no tiene ninguno todavía**
porque ningún pedido ha terminado su ciclo. No hay endpoint de tarifas.

---

## 1 · ENVÍO — esto sí está medido

Los **11 pedidos** de la tienda, desglosados con `/order/202309/orders/search`:

| | |
|---|---|
| GMV | **$1,924.60 MXN** |
| Costo real del envío | **$489.00** |
| Lo paga el comprador | **$0.00** |
| **Lo pone TikTok** | **$489.00 (100%)** |
| **Lo pone KUBERA** | **$0.00** |

El envío por pedido va de **$39 a $59 MXN** según el paquete, y en los 11 casos
el campo que lo cubre es `shipping_fee_platform_discount` — **TikTok entero**.
`shipping_fee_seller_discount` y `shipping_fee_cofunded_discount` están en cero
en todos.

```jsonc
// pedido real 585227524907697651
{"original_shipping_fee": "39",           // lo que cuesta mandarlo
 "shipping_fee": "0",                     // lo que paga el comprador
 "shipping_fee_platform_discount": "39",  // ← lo pone TikTok
 "shipping_fee_seller_discount": "0",     // ← lo pondría Kubera
 "shipping_fee_cofunded_discount": "0"}   // ← lo pondríamos a medias
```

⚠️ **Que hoy sea gratis NO significa que vaya a serlo siempre.** Existen los tres
campos —plataforma, vendedor y *cofunded*— porque TikTok contempla los tres
repartos. Hoy nos toca el bueno, probablemente por el programa de vendedor
nuevo. **Para vigilarlo basta con mirar si `shipping_fee_seller_discount` deja
de ser 0**: ese es el día en que el envío empieza a costarnos.

---

## 2 · COMISIÓN — por qué no te la puedo dar

Busqué por las tres vías y ninguna la da hoy:

| Vía | Resultado |
|---|---|
| `GET /finance/202309/statements` | ✅ responde · **`statements: []`** vacío |
| `GET /finance/202309/payments` | ✅ responde · **`payments: []`** vacío |
| `GET /finance/202309/orders/{id}/statement_transactions` | ✅ responde · **`statement_transactions: []`** en los 3 probados |
| El objeto `payment` de la orden | trae precios y envío, **no trae comisión** |
| Endpoint de tarifas (`/fees`, `/seller/fees`…) | ❌ **no existe** (`36009009 Invalid path`) |

**La causa no es un permiso ni un bug: es que todavía no hay nada liquidado.**
10 de los 11 pedidos son de HOY y ninguno ha cerrado su ciclo. La comisión
aparece cuando TikTok emite el *statement*.

### Lo que SÍ dicen los Términos de Servicio oficiales de MX

De [seller-mx.tiktok.com](https://seller-mx.tiktok.com/university/essay?knowledge_id=6295443664750337&lang=es-419) —
confirma la ESTRUCTURA, y remite al Seller Center para los números:

> *"Las Tarifas de Transacción y las Tarifas de Comisión se indicarán en el
> Centro de Vendedores"*

Tres cobros, confirmados como existentes:

1. **Tarifa de Comisión** — % sobre el total pagado por el comprador
2. **Tarifa de Transacción** — % adicional
3. **Programa de Tarifa de Envío (PTE)** — sobre pedidos entregados con éxito

---

## 3 · ⚠️ Lo que dicen terceros — TÓMALO CON PINZAS

Ningún blog es fuente para esto, y **se contradicen entre sí de forma brutal**:
unos dicen 2% por categoría y otros 9% general. Lo más probable es que se copien
entre ellos y mezclen mercados (la cifra de 9% viene de una nota sobre **otro
país**, no México).

**Lo pongo sólo para que tengas el orden de magnitud, NO para calcular precios:**

| Fuente secundaria | Dice |
|---|---|
| Comisión por categoría | Moda 2.0% · Belleza 3.0% · Electrónicos 1.5% · Hogar 2.5% · Alimentos 3.5% · Fitness 3.0% · Mascotas 2.5% · Libros 2.0% |
| Procesamiento de pago | ~1.5% |
| Programa de envío (SFP/PTE) | 8% desde el 15-ene-2026 |
| Vendedor nuevo | **0% de comisión los primeros 60 días** |
| Otra nota (¿otro mercado?) | 9% general, 7% en electrónicos y belleza |

Ese **0% los primeros 60 días** encaja sospechosamente bien con dos cosas que sí
medí: que la tienda esté en *probation* (tope de 300 publicaciones/día) y que
TikTok esté absorbiendo el envío completo. **Si es cierto, hay un reloj
corriendo** y conviene saber cuándo vence.

---

## 4 · Cómo conseguir el número de verdad

**Dos caminos, y el primero es de hoy:**

1. **Seller Center → Finanzas → Tarifas.** Es donde los propios Términos dicen
   que están. Requiere entrar con la cuenta; ahí lo ves en dos minutos.
2. **La API, en cuanto se liquide el primer pedido.** Ya está probado que los
   tres endpoints responden bien: sólo les falta contenido. En cuanto haya un
   *statement*, `statement_transactions` trae el desglose real por pedido —
   comisión incluida.

**Puedo dejar armado el segundo** para que el día que llegue el primer statement
el dato entre solo: un script que lee `statements` → `statement_transactions` y
guarda comisión y envío por SKU, igual que `pedidos_ml` congela `sale_fee`. Con
eso, `costos.py` podría calcular el margen de TikTok como ya lo hace con ML.

---

## 5 · Lo que encontré de paso, y no es menor

**La tienda ya está vendiendo: 10 de los 11 pedidos son de HOY**, el mismo día
que se activaron los primeros 282 productos.

| fecha | pedidos |
|---|---|
| 26-jul | 1 (el viejo `TEC-1212-NEG-150MTS`) |
| 12-ago | 1 |
| **13-ago** | **9** |

### El canal de pedidos SÍ está vivo (y `FLUJO_POR_CANAL.md` ya se quedó corto)

Ese documento dice *"`channel.orders`: tiktok 1"*. **Hoy son 8**, con su pedido
de WooCommerce creado. Los webhooks `ORDER_STATUS_CHANGE` que dimos de alta
ayer están funcionando y `PEDIDOS_TIKTOK_ENABLED` está encendido.

```
585534473262171333  AWAITING_SHIPMENT    wc=121469   $51.92
585534386350425965  AWAITING_SHIPMENT    wc=121439  $344.74
585533372153562629  AWAITING_COLLECTION  wc=121528  $232.50
…y 5 más
```

### ⚠️ Pero faltan 2 pedidos PAGADOS, y no es casualidad

| orden | estado | SKU | ¿debería entrar? |
|---|---|---|---|
| `585533446435931984` | AWAITING_COLLECTION | `JAR-0031-NEG` | **sí — falta** |
| `585533662754342335` | AWAITING_SHIPMENT | `JAR-0031-NEG` | **sí — falta** |
| `585534512170763486` | UNPAID | `VIA-0024-NEG` | no (sin pagar) |

El tercero está bien que no entre. Los otros dos son ventas reales que **nadie
ve desde el panel y cuyo stock no se ha descontado en Woo**.

**Y los dos son del mismo SKU: `JAR-0031-NEG`** — que es uno de los **4 SKUs que
tenían copias DUPLICADAS** en TikTok (junto con `VIA-0024-NEG`, el del tercero).
Las copias se borraron el 13-ago.

Dos ventas del mismo SKU duplicado, las dos perdidas, y el único otro SKU
involucrado es también duplicado: **es demasiada coincidencia para archivarlo.**
La sospecha razonable es que el pedido llegó apuntando a un `product_id` que
acabábamos de borrar, o que dos ventas del mismo SKU chocaron en el candado.

**Hay que reproducirlo antes de que se repita**: son ~$271 en dos pedidos, pero
el modo de fallo —venta que entra y no se registra— es el que ya costó caro en
Mercado Libre.
