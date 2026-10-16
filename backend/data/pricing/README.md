# Foto del «Monitor de precios FULL» (pestaña PRICING)

Lo que sirve `GET /api/pricing/*` (`services/pricing_monitor.py`). Es una FOTO, no datos en vivo.

- `monitor.json.gz`: el JSON del artifact de José del 14-oct-2026 (`meta`, `pubs`, `riv`): 658
  publicaciones FULL de BEKURA y SANCORFASHION con precio, stock, ventas y visitas por día, competencia y
  categoría revisadas; 593 SKUs con sus rivales.
- `img/p0.jpg`, `img/p1.jpg`: miniaturas de nuestras publicaciones (re-empacadas).
- `img/s*.jpg`: miniaturas de los rivales; la pantalla las pide solo al abrir «lo scrapeado».

**Cómo se actualiza.** Con el JSON (o el HTML) de un artifact nuevo y sus hojas `s*.jpg`:

```bash
python backend/scripts/pricing_preparar_foto.py datos.json carpeta_hojas backend/data/pricing
```

Commit y deploy. El backend la toma en 10 minutos (su caché).

Las reglas de «Qué hacer» no viven aquí: corren en `frontend/components/pricing/reglas.ts`.

**«Hecho» y «Promoción hoy» (v0.654.0).**

- `hechos.json`: los precios que sí se movieron en ML (precio antes, nuevo, normal, desde, hasta). Lo arma
  `python backend/scripts/pricing_hechos.py <bitacora.jsonl> [<verificacion.json>]` con la bitácora del script
  que aplicó los descuentos. Lo vendido después del cambio NO va aquí: el backend lo lee de kubera al servirlo.
- `promos.json`: la promoción que tiene cada publicación al momento de leerla (`leido`) y a cuáles puede entrar.
  Lo arma `python backend/scripts/pricing_promos.py` (solo GET a ML). Desde v0.656.0 es solo el ARRANQUE: el servidor
  las relee de ML cada 30 min en segundo plano.
