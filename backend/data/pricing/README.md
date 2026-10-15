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
