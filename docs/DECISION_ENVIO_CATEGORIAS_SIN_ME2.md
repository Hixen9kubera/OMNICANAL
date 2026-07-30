# DECISIÓN PENDIENTE — Envío propio para categorías que no admiten ME2

> Abierta el 2026-07-30. **Es decisión de negocio, no técnica**: define cuánto
> cobra Kubera por envío. Anotada para retomarla; no se implementa nada hasta
> que Brandon elija.

## El problema

14 SKUs no se pueden publicar en Mercado Libre. El error decía *"activar Mercado
Envíos 1"*, pero eso es imposible: **ML descontinuó ME1** (las preferencias de
las dos cuentas devuelven `modes: []`). El mensaje ya se corrigió en v0.40.0.

La causa real es la **categoría**. Verificado contra la API de ML:

| Categoría | `shipping_options` |
|---|---|
| Monederos y Carteras (`MLM5527`) | `carrier`, `custom` |
| Gorros (`MLM174552`) | `custom`, `carrier` |

Ninguna admite `me2`, que es lo único que manda el publicador. Para publicar ahí
hay que usar `custom` — **envío propio**, y eso obliga a fijarle un costo.

## ⚠️ ANTES DE DECIDIR: los números están inflados

Las dimensiones en `costos_validados` son de la **CAJA MASTER**, no de la pieza
(mismo bug que infla el flete de 13,046 SKUs). El envío calculado sale hasta 3×
de más:

| SKU | Piezas/caja | Envío con caja | Envío real estimado |
|---|---|---|---|
| `ROP-0621-GRI-5XL` | 45 | $178.00 | **$56.00** |
| `JUGU-0244-MUL` | 32 | $178.00 | **$59.60** |
| `VEH-0076-NEG` | 39 | $178.00 | **$56.00** |
| `ACC-0567-OVA-MUJ-NEG` | 300 | $129.20 | **$56.00** |

**Decidir con los números de hoy = cobrar 3× de más y salirse del mercado.**
Conviene resolver primero `piezas_por_caja`.

## Las 4 opciones

### A) Envío gratis (Kubera absorbe, va en el precio)
ML premia el envío gratis con mejor posición y es gancho comercial.
Costo real: **$52–$60 por pieza**. En un producto de $400 son ~14%.
No sirve en los baratos: `ACC-0446-MUL` ($43.60) tendría un envío del 120%.

### B) Tarifa fija que paga el comprador
Un número redondo ($99 / $120). Simple y no depende de datos correctos por SKU.
Se pierde el gancho de envío gratis; en productos baratos se ve desproporcionado.

### C) Por peso, con la tabla oficial de ML
La tabla ya está en `services/costos.py::_TARIFA_ML`. Cobra lo que cuesta.
**Bloqueada** hasta que las dimensiones sean por pieza.

### D) No publicar en esas categorías
Son 14 de 7,151 SKUs. Si el margen no aguanta el envío propio, es válido.

## Recomendación

**Mezcla**: envío gratis en los que aguanten el margen (los de $300+, donde $56
es ~18%) y no publicar los que no. Pero **primero arreglar `piezas_por_caja`**,
que además corrige el costo de los otros 13,046 SKUs.

## Los 14 SKUs

`ACC-0029-CAF`, `ACC-0141`, `ACC-0445-CAF`, `ACC-0446-MUL`, `ACC-0466-BLN`,
`ACC-0567-OVA-MUJ-NEG`, `CALZ-0182`, `JUEG-0029-MUL`, `JUGU-0244-MUL`,
`MUE-0431-REC-INTER`, `ROP-0197`, `ROP-0372`, `ROP-0621-GRI-5XL`, `VEH-0076-NEG`

6 de ellos no tienen precio ni datos de costo cargados — ésos hay que completarlos
antes de poder evaluarlos.

## Qué se implementa cuando se decida

Reintentar la publicación con `mode: 'custom'` y el costo elegido, en el adaptador
(`services/publicar_ready.py`) — nunca en `vendor/`, que no se toca.
