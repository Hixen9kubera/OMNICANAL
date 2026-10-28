# Precios de mercado del catálogo maestro («Sugerir precio» del Estudio)

Lo que sirve `GET /api/precio-sugerido/catalogo/{sku}` (`services/catalogo_precios.py`). Es una FOTO del catálogo
maestro (CATALOGO_VIVO), no datos en vivo.

- `precios.json.gz`:
  - `skus[SKU].ml`: la MEDIA de Mercado Libre (`precio_ml_final` del v3, el precio que manda), MÍN, MÁX y sus
    fuentes. El rango es entre FUENTES, no entre rivales.
  - `skus[SKU].az`: la palabra clave de Amazon, la estadística de los comparables y el veredicto de cada
    publicación leída (`v`: m = match, x = no match, ? = sin veredicto guardado).
  - `busquedas[palabra]`: las publicaciones de Amazon de esa palabra (ASIN de la familia, título, precio de la
    caja de compra).

**Solo precios de mercado.** El repo es público: no lleva costos, ni lo que deja despejarlos (`estimado_costo`,
`topado_10x_costo`, `notas`). Lo comprueba `tests/test_precio_sugerido.py`.

**Cómo se actualiza.** Con el catálogo al día en `reportes/catalogo_vivo` (local, fuera de git):

```bash
python backend/scripts/catalogo_precios_preparar.py reportes/catalogo_vivo
```

Commit y deploy. El backend lo abre una vez por proceso, así que lo toma al reiniciar.
