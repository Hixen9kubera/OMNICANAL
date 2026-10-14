/* El stock que pinta la tarjeta de un canal (Mosaico de Productos).

   Hasta v0.644.0 pintaba `stock_real`: el espejo de ESA publicación. El de una
   publicación FULL vale 0 siempre —ML guarda sus piezas en `stock_full`—, así
   que las 645 publicaciones activas en FULL salían con «0 u» en rojo, y la de
   la otra cuenta del mismo SKU también (ACC-0040-ROJ, 14-oct-2026: 3 en Woo,
   5 en FULL en San Corpe; su tarjeta de BEKURA, pausada, en rojo).

   La regla de la casa: el espejo se cuenta una vez y la fuente buena es Woo.
   El backend manda `stock_bodega` (Woo; un padre suma sus variantes en Woo; sin
   fila en Woo, el mayor de los espejos) y `full_fba_sku` (FULL + FBA del SKU en
   todas sus cuentas). El rojo queda para lo que de verdad no tiene de dónde
   vender: ni bodega ni FULL/FBA. */

import type { Producto } from "./types";

export type TonoStock = "verde" | "neutro" | "rojo";

type Datos = Pick<Producto, "stock" | "stock_real" | "stock_full" | "stock_fba"
  | "stock_bodega" | "stock_bodega_fuente" | "full_fba_sku">;

const FUENTE: Record<string, string> = {
  woo: "WooCommerce, la fuente del almacén",
  woo_variantes: "suma de sus variantes en WooCommerce",
  espejo: "sin fila en WooCommerce: el mayor de los espejos de sus publicaciones",
};

export function stockDeTarjeta(p: Datos): { valor: number | null; tono: TonoStock; titulo: string } {
  const bodega = p.stock_bodega ?? null;
  // Sin el dato nuevo (backend anterior o lectura caída) se pinta el espejo,
  // como antes, en vez de dejar la tarjeta sin número.
  const valor = bodega ?? p.stock_real ?? p.stock ?? null;
  const dePublicacion = (p.stock_full ?? 0) + (p.stock_fba ?? 0);
  const fullFba = Math.max(p.full_fba_sku ?? 0, dePublicacion);

  const tono: TonoStock =
    valor === null ? "neutro"
      : valor > 0 ? "verde"
        : fullFba > 0 ? "neutro"
          : "rojo";

  const lineas: string[] = [];
  if (bodega !== null) {
    lineas.push(`En bodega: ${bodega} u (${FUENTE[p.stock_bodega_fuente ?? ""] ?? "almacén propio"}).`);
  } else {
    lineas.push("Stock que reporta esta publicación (sin dato de la bodega).");
  }
  if (dePublicacion > 0) {
    lineas.push("Esta publicación vende desde la bodega del marketplace (FULL/FBA).");
  } else if (bodega !== null && p.stock_real != null && p.stock_real !== bodega) {
    lineas.push(`Esta publicación ofrece ${p.stock_real} u.`);
  }
  if (tono === "neutro" && valor !== null) {
    lineas.push(`Sin piezas en nuestra bodega, pero el SKU tiene ${fullFba} u en FULL/FBA: no está agotado.`);
  } else if (tono === "rojo") {
    lineas.push("Sin piezas en bodega ni en FULL/FBA.");
  }
  return { valor, tono, titulo: lineas.join("\n") };
}
