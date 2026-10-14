/**
 * Pruebas de dos reglas PURAS del panel (14-oct-2026, caso ACC-0040-ROJ):
 *
 *     node frontend/lib/pruebas/tarjeta_y_banda.prueba.cjs
 *
 *   · STOCK-*: el número y el color del stock en la tarjeta de un canal
 *     (`lib/stockTarjeta.ts`). Una publicación FULL trae `stock_real` 0
 *     siempre; la tarjeta pinta la bodega del SKU y no se pone en rojo si
 *     el SKU vende desde FULL/FBA.
 *   · BANDA-*: las etiquetas de la banda «A precio de mercado»
 *     (`lib/etiquetasBanda.ts`) no se enciman nunca, sea cual sea la
 *     distancia entre marcadores, y no se salen de la pista.
 *
 * Usa el cargador de las pruebas de Órdenes (transpila `.ts` al vuelo y
 * resuelve `@/`); sin dependencias nuevas.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
require("../../components/ordenes/pruebas/cargar.cjs");

const { stockDeTarjeta } = require("@/lib/stockTarjeta");
const { acomodarEtiquetas, AIRE_PX } = require("@/lib/etiquetasBanda");

// ── STOCK ────────────────────────────────────────────────────────────────────

const pub = (o) => ({
  stock: null, stock_real: null, stock_full: null, stock_fba: null,
  stock_bodega: null, stock_bodega_fuente: null, full_fba_sku: null, ...o,
});

test("STOCK-1 · San Corpe en FULL: pinta la bodega (3), no el espejo (0), y en verde", () => {
  const r = stockDeTarjeta(pub({ stock: 0, stock_real: 0, stock_full: 5, stock_bodega: 3,
                                  stock_bodega_fuente: "woo", full_fba_sku: 5 }));
  assert.equal(r.valor, 3);
  assert.equal(r.tono, "verde");
  assert.match(r.titulo, /En bodega: 3 u \(WooCommerce/);
  assert.match(r.titulo, /vende desde la bodega del marketplace/);
});

test("STOCK-2 · BEKURA pausada sin FULL propio: la bodega del SKU, y dice qué ofrece la publicación", () => {
  const r = stockDeTarjeta(pub({ stock: 0, stock_real: 0, stock_full: 0, stock_bodega: 3,
                                  stock_bodega_fuente: "woo", full_fba_sku: 5 }));
  assert.equal(r.valor, 3);
  assert.equal(r.tono, "verde");
  assert.match(r.titulo, /Esta publicación ofrece 0 u/);
});

test("STOCK-3 · bodega en 0 pero el SKU tiene FULL: NO es rojo", () => {
  const r = stockDeTarjeta(pub({ stock_real: 0, stock_full: 5, stock_bodega: 0,
                                  stock_bodega_fuente: "woo", full_fba_sku: 5 }));
  assert.equal(r.valor, 0);
  assert.equal(r.tono, "neutro");
  assert.match(r.titulo, /no está agotado/);
});

test("STOCK-4 · el FULL de OTRA cuenta también salva del rojo", () => {
  const r = stockDeTarjeta(pub({ stock_real: 0, stock_full: 0, stock_bodega: 0, full_fba_sku: 7 }));
  assert.equal(r.tono, "neutro");
});

test("STOCK-5 · sin bodega ni FULL/FBA en ningún lado: rojo", () => {
  const r = stockDeTarjeta(pub({ stock_real: 0, stock_full: 0, stock_bodega: 0, full_fba_sku: 0 }));
  assert.equal(r.tono, "rojo");
  assert.match(r.titulo, /Sin piezas en bodega ni en FULL\/FBA/);
});

test("STOCK-6 · backend viejo (sin stock_bodega): pinta el espejo, pero FULL no queda en rojo", () => {
  const r = stockDeTarjeta(pub({ stock: 0, stock_real: 0, stock_full: 5 }));
  assert.equal(r.valor, 0);
  assert.equal(r.tono, "neutro");
});

test("STOCK-7 · sin ningún dato: «—» en gris, nunca rojo", () => {
  const r = stockDeTarjeta(pub({}));
  assert.equal(r.valor, null);
  assert.equal(r.tono, "neutro");
});

test("STOCK-8 · Amazon: FBA cuenta igual que FULL", () => {
  const r = stockDeTarjeta(pub({ stock_real: 0, stock_fba: 4, stock_bodega: 0 }));
  assert.equal(r.tono, "neutro");
});

// ── BANDA ────────────────────────────────────────────────────────────────────

/** Comprueba que nadie se encima en su renglón ni se sale de la pista. */
function sinChoques(medidas, ancho, out) {
  assert.equal(out.length, medidas.length);
  out.forEach((c, i) => {
    const w = medidas[i].w;
    if (w <= ancho) {
      assert.ok(c.left >= -0.01, `etiqueta ${i} se sale por la izquierda (${c.left})`);
      assert.ok(c.left + w <= ancho + 0.01, `etiqueta ${i} se sale por la derecha (${c.left + w} > ${ancho})`);
    }
  });
  for (let i = 0; i < out.length; i++) {
    for (let j = i + 1; j < out.length; j++) {
      if (out[i].fila !== out[j].fila) continue;
      const [a, b] = out[i].left <= out[j].left ? [i, j] : [j, i];
      assert.ok(out[a].left + medidas[a].w + AIRE_PX <= out[b].left + 0.02,
        `se enciman ${a} y ${b}: ${JSON.stringify({ medidas, ancho, out })}`);
    }
  }
}

// Anchos medidos en el navegador (escritorio): «TÚ $253.00» y «MERCADO $342.20».
const TU = 78;
const MERCADO = 108;

test("BANDA-1 · el caso real: Tú al 66 % y Mercado al 89 % en 340 px ya no se enciman", () => {
  const m = [{ p: 89.3, w: MERCADO }, { p: 66.0, w: TU }];
  const out = acomodarEtiquetas(m, 340);
  sinChoques(m, 340, out);
  assert.ok(out.every((c) => c.fila === 0), "caben en un renglón");
});

test("BANDA-2 · precios IGUALES: se abren a los dos lados del marcador", () => {
  const m = [{ p: 50, w: MERCADO }, { p: 50, w: TU }];
  const out = acomodarEtiquetas(m, 340);
  sinChoques(m, 340, out);
  // el desplazamiento se reparte: el marcador queda entre las dos
  const centro = 170;
  const izq = Math.min(out[0].left, out[1].left);
  const der = Math.max(out[0].left + m[0].w, out[1].left + m[1].w);
  assert.ok(izq < centro && der > centro);
});

test("BANDA-3 · precios MUY CERCANOS, en medio y pegados a cada orilla", () => {
  for (const [a, b] of [[49, 51], [1, 2], [0, 0], [98, 99.5], [100, 100]]) {
    const m = [{ p: a, w: MERCADO }, { p: b, w: TU }];
    sinChoques(m, 340, acomodarEtiquetas(m, 340));
  }
});

test("BANDA-4 · precios MUY LEJANOS: cada una centrada en su marcador, sin moverse", () => {
  const m = [{ p: 20, w: TU }, { p: 80, w: MERCADO }];
  const out = acomodarEtiquetas(m, 340);
  sinChoques(m, 340, out);
  assert.equal(out[0].left, 0.2 * 340 - TU / 2);
  assert.equal(out[1].left, 0.8 * 340 - MERCADO / 2);
});

test("BANDA-5 · móvil (pista de 300 px): igual que en escritorio, sin encimarse", () => {
  for (const [a, b] of [[66, 89.3], [50, 50], [10, 12], [95, 100]]) {
    const m = [{ p: a, w: TU }, { p: b, w: MERCADO }];
    sinChoques(m, 300, acomodarEtiquetas(m, 300));
  }
});

test("BANDA-6 · si no caben en un renglón, la segunda baja: dos renglones, cero choques", () => {
  const m = [{ p: 50, w: 120 }, { p: 52, w: 120 }];
  const out = acomodarEtiquetas(m, 200);
  sinChoques(m, 200, out);
  assert.deepEqual(out.map((c) => c.fila).sort(), [0, 1]);
});

test("BANDA-7 · las fronteras (0 % y 20 % con montos) tampoco se enciman", () => {
  const m = [{ p: 61.2, w: 70 }, { p: 64.0, w: 82 }];
  sinChoques(m, 340, acomodarEtiquetas(m, 340));
});

test("BANDA-8 · barrido: 5,000 combinaciones de posiciones, anchos y pistas", () => {
  let semilla = 7;
  const azar = () => ((semilla = (semilla * 1103515245 + 12345) % 2147483648) / 2147483648);
  for (let k = 0; k < 5000; k++) {
    const ancho = 180 + Math.floor(azar() * 260);
    const n = 1 + Math.floor(azar() * 3);
    const m = Array.from({ length: n }, () => ({ p: azar() * 100, w: 30 + Math.floor(azar() * 120) }));
    sinChoques(m, ancho, acomodarEtiquetas(m, ancho));
  }
});
