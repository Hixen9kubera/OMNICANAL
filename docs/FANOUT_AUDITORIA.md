# Fan-out de stock — auditoría completa (14-ago-2026)

Todo lo de aquí está **medido contra producción** el 14-ago. Lo que no se pudo
medir se marca `NO VERIFICADO`.

---

## 0. Qué se encendió hoy

| Variable | Antes | Ahora |
|---|---|---|
| `PEDIDOS_TIKTOK_ENABLED` | false | **true** — las ventas de TikTok entran como pedidos |
| `FANOUT_CANALES` | `amazon,mercado_libre` | **`amazon,mercado_libre,tiktok`** |

Confirmado en el arranque del backend:

```
TikTok · contenido al crear: ENCENDIDO · pedidos por webhook: ENCENDIDO ·
fan-out de stock: ENCENDIDO · canales del fan-out: ['amazon', 'mercado_libre', 'tiktok']
```

---

## 1. Qué hace el fan-out, en una frase

Lee el stock **DROP de WooCommerce** (`_stock`, que es la bodega propia) y lo
escribe en cada publicación **que no viva en la bodega de un marketplace**.

```
Woo (_stock)  ──►  ML no-FULL  ·  Amazon MFN  ·  TikTok
                   (las FULL / FBA NO se tocan: su stock no es nuestro)
```

La regla de exclusión, textual en el código: una publicación se omite si
`is_fulfillment` está marcada **o** tiene piezas en `stock_full` **o** en
`stock_fba` → *"FULL/FBA (bodega del marketplace, no se toca)"*.

---

## 2. Quién dispara el fan-out — los cinco caminos, con su estado REAL

| # | Disparador | Estado hoy | Evidencia (7 días) |
|---|---|---|---|
| 1 | Venta en ML **no-FULL** | ✅ vivo | `pedidos_ml.sincronizar` |
| 2 | **Cambio de stock en Woo** (vigilante de inventario, cada 20 min) | ✅ **vivo y en modo real** (`solo_registro=False`, tope 300) | 44 `woo_cambio` |
| 3 | **Delta de Odoo → Woo** (mismo vigilante) | ✅ vivo | 244 `odoo_delta` |
| 4 | Movimiento de bodega FULL (webhook de ML) | ⚠️ **solo observación** | 3,299 `full_ignorado`, 826 `full_aviso` |
| 5 | Ingreso a FBA (comparación de fotos, cada 15 min) | ⚠️ **solo observación** | 101 `fba_ingreso_sim` (**sim** = simulado) |

**El fan-out escribe de verdad** (`dry_run=0`): 103 escrituras reales a Mercado
Libre en 7 días. Ejemplo del registro:

```
2026-08-14 06:20  TEC-0004-BLN  mercado_libre SANCORFASHION  250 -> 248  ok
2026-08-14 05:20  ORG-0326-MET  mercado_libre BEKURA         232 -> 230  ok
```

⚠️ **Los caminos 4 y 5 están en modo observación** porque
`FULL_WATCH_SOLO_REGISTRO` no está definida en Railway y su valor por defecto es
`True`. Consecuencia práctica: **mandar mercancía a FULL o a FBA no baja Woo por
sí solo** — se anota lo que haría y ya. Lo que sí baja Woo es el delta de Odoo
(camino 3).

---

## 3. Qué recibe cada canal — censo medido

| Canal | Publicaciones | FULL/FBA (excluidas) | Candidatas DROP |
|---|---|---|---|
| Mercado Libre | 4,863 | 1,746 | **3,117** |
| Amazon | 1,791 | **20** | **1,771** |
| TikTok | 901 | 0 | 901 |
| Temu | **0** | — | — |

### 3.1 Amazon NO es FBA — es al revés

**1,771 de 1,791 publicaciones son MFN/DROP** (bodega nuestra) y solo **20 son
FBA**. Amazon es, de hecho, el canal DROP más grande del catálogo.

**Pero en la práctica casi nunca recibe stock**, y la causa es otra:

| `situacion` en Amazon | Publicaciones |
|---|---|
| DISCOVERABLE | 1,258 |
| closed | 289 |
| (sin dato) | 111 |
| **BUYABLE** | **85** |
| PUBLISHED | 48 |

El fan-out se niega a escribirles: *"Amazon DISCOVERABLE — dormido, escribirle
lo DESPERTARÍA"*. En 7 días: **88 omisiones, 0 escrituras**. Es una decisión
deliberada del código, no una falla — pero significa que **el stock de Amazon
no se está sincronizando** para 1,258 publicaciones.

### 3.2 TikTok: 901 cargadas, 284 a la venta

| `status` | Publicaciones |
|---|---|
| DRAFT | 599 |
| **ACTIVATE** | **284** |
| FAILED | 11 |
| PENDING | 7 |

El fan-out solo escribe a las **ACTIVATE** — mirar `status`, y no el veredicto
de la auditoría (`APPROVED`), es a propósito: quien decide si está a la venta es
`status`.

---

## 4. El caso del reparto FULL/FBA — la pregunta concreta

> *"Si de un SKU se mandan 50 piezas a FULL y pasa de 60 a 10 para drop, ¿ese
> inventario se replica en TikTok y pasa de 60 a 10?"*

**Sí.** Y a Mercado Libre le pasan las dos cosas a la vez, porque el mismo SKU
suele tener publicación FULL **y** publicación DROP.

**1,008 SKUs están exactamente en esa situación** (tienen publicación FULL/FBA y
publicación DROP al mismo tiempo). De esos, **414 también están en TikTok**.

### Plan REAL del fan-out, corrido sobre SKUs de ese grupo

```
--- ACC-0266-ROJ  ·  stock DROP en Woo = 15  ->  objetivo 15
      tiktok         KUBERA          tiene=15      SIN_CAMBIO  el canal ya tiene 15
      mercado_libre  SANCORFASHION   tiene=15      SIN_CAMBIO  el canal ya tiene 15
      mercado_libre  BEKURA          tiene=0       OMITIR      FULL/FBA (bodega del marketplace)

--- BEB-0043-AZL  ·  stock DROP en Woo = 30  ->  objetivo 30
      tiktok         KUBERA          tiene=30      SIN_CAMBIO
      mercado_libre  SANCORFASHION   tiene=30      SIN_CAMBIO
      mercado_libre  BEKURA          tiene=0       OMITIR      FULL/FBA
      amazon         -               tiene=0       OMITIR      situacion=discoverable (lo REACTIVARÍA)
```

**La respuesta canal por canal:**

| Canal | ¿Pasa de 60 a 10? | Por qué |
|---|---|---|
| **TikTok** | ✅ **sí** | vende de nuestra bodega |
| **ML — publicación DROP** | ✅ **sí** | vende de nuestra bodega |
| **ML — publicación FULL** | ❌ no, y está bien | su stock vive en la bodega de ML; ese número lo lleva ML |
| **Amazon MFN (no FBA)** | ⚠️ sí **si está BUYABLE** | hoy 1,258 están dormidas y se omiten |
| **Amazon FBA** | ❌ no | bodega de Amazon (solo 20 publicaciones) |

Tu intuición era correcta en la forma y equivocada en un dato: **no es que ML no
aplique "porque es FULL"** — aplica a su publicación DROP y no a su publicación
FULL, y la mayoría de los SKUs grandes tienen las dos.

### El eslabón que hay que cuidar

El fan-out replica **lo que diga Woo**. Que Woo pase de 60 a 10 depende del
camino 3 (el delta de Odoo). Si el almacén manda 50 piezas a FULL **y no lo
registra en Odoo**, Woo se queda en 60 y los canales DROP siguen ofreciendo 60
piezas que ya no están. El camino 4 (el webhook de FULL) existe para cerrar
justo ese hueco, pero **está en modo observación**.

---

## 5. Impacto real de encender TikTok — medido antes de encenderlo

De las 284 publicaciones a la venta:

| | |
|---|---|
| Ya coincidían con Woo | 277 |
| El fan-out **bajará** | **6** ← las que evitaban sobreventa |
| El fan-out **subirá** | 1 |
| Sin stock legible en Woo | 0 |

```
ORG-0476-MUL          16 -> 0     BAJA   (ofrecía 16 piezas inexistentes)
TEC-0933-NEG           1 -> 0     BAJA
TEC-1651-MET-TOOLS    98 -> 96    BAJA
ORG-0176-MUL          50 -> 47    BAJA
JAR-0031-NEG          42 -> 41    BAJA
TEC-1778-NEG           1 -> 16    sube
```

Encenderlo es de bajo riesgo: 277 de 284 ya estaban alineadas. El caso que lo
justifica es `ORG-0476-MUL`, que ofrecía **16 piezas que no existen**.

---

## 6. Huecos encontrados (con dueño)

| # | Hueco | Impacto | Dueño |
|---|---|---|---|
| 1 | **1,258 publicaciones de Amazon DISCOVERABLE** no reciben stock | Si despiertan, venden con el número viejo | Decisión: despertarlas o cerrarlas |
| 2 | **Vigilante de FULL/FBA en observación** (`FULL_WATCH_SOLO_REGISTRO` sin definir) | Un envío a FULL no baja Woo salvo que se registre en Odoo | Brandon (encender = mueve inventario) |
| 3 | **599 publicaciones de TikTok en DRAFT** | 2 de cada 3 cargadas no están a la venta | Revisar por qué |
| 4 | **Temu no existe en el panel** | 0 publicaciones, 0 reglas de campos | Siguiente bloque de trabajo |

---

## 7. Temu — qué aplicará y qué falta

Cuando Temu entre al panel, **la misma regla aplica sin cambios**: recibe el
stock DROP de Woo, salvo que la publicación viva en una bodega de Temu. Hoy no
recibe nada porque **no hay una sola publicación de Temu en `channel.listings`**;
lo único vivo son sus pedidos (2 en 30 días, por el sondeo de M2E).

Faltan las mismas seis piezas que se construyeron para TikTok: cargar el
catálogo, categorías, reglas de campos, contenido con IA (ya existe
`temu_contenido.py`), publicador y webhook de pedidos.
