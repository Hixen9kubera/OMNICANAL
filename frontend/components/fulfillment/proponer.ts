/**
 * La planeación semanal: cuánto mandar de cada SKU a cada tienda, con las reglas
 * del PROMPT ESTÁNDAR (el texto completo vive en backend/services/fulfillment_ia.py).
 *
 * Se calcula AQUÍ, en el navegador, para que mover un parámetro, prender o apagar
 * una tienda o corregir un renglón se vea al instante sin volver a leer Odoo. Los
 * insumos los arma `backend/services/fulfillment_full.py`.
 *
 *   velocidad  = vendidas en la ventana / días de la ventana
 *   objetivo   = ⌈vendidas × cobertura / ventana⌉
 *   pidió      = max(0, objetivo − en el almacén − en camino − en borradores)
 *   bodega     = libre en Odoo − colchón DROP, REPARTIDO entre las tiendas activas
 *                que piden el mismo producto (en proporción a lo que pidió cada una)
 *   propuesta  = min(bodega, pidió); menos del mínimo por renglón → 0
 *   estado     = aprobado (bodega ≥ pidió) · recorte (bodega < pidió) ·
 *                pendiente (sin dato de Odoo: NO es un cero)
 *
 * UNA diferencia deliberada con el prompt: al faltante también se le resta lo que
 * ya va en camino y lo de borradores; el prompt resta sólo el stock del almacén y
 * mandaría dos veces lo que salió el lunes y ML todavía no recibe.
 *
 * Nada de esto escribe: es la sugerencia que la persona corrige antes de crear.
 */

import type { FilaPlan, ParametrosFull, Tienda } from "./tipos";

export type EstadoRenglon = "aprobado" | "recorte" | "pendiente" | "cubierto";

export interface Renglon extends FilaPlan {
  clave: string;
  /** Agregado a mano (búsqueda o reemplazo): no venía en la planeación. */
  agregado?: boolean;
  velocidad: number;
  objetivo: number;
  pidio: number;
  libre_total: number | null;
  bodega: number | null;
  propuesta: number;
  estado: EstadoRenglon;
  /** Vendió ≥ el umbral, sin libre en Odoo y sin stock en el almacén. */
  ganador_agotado: boolean;
  aguanta: number | null;
  sube: boolean;
  repartido?: string;
}

export interface Totales {
  renglones: number;
  pedidas: number;
  propuestas: number;
  a_mandar: number;
  skus_a_mandar: number;
  /** bodega / pedido de los renglones revisados (sin pendientes). null = nada que medir. */
  tasa_validado: number | null;
  /** lo que se va a mandar contra lo pedido. */
  final_vs_pedido: number | null;
  pendientes: number;
}

const suma = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
export const claveDe = (t: Tienda, sku: string) => `${t}|${sku}`;

export function planear(filas: FilaPlan[], p: ParametrosFull, agregadas: Set<string> = new Set()): Renglon[] {
  const ventana = Math.max(1, p.ventana_dias);
  const base: Renglon[] = filas.map((f) => {
    const objetivo = Math.ceil((f.vv * p.cobertura_dias) / ventana);
    const pidio = Math.max(0, objetivo - (f.stock ?? 0) - f.en_camino - f.borrador);
    const libreTotal = f.libre ? Math.max(0, suma(Object.values(f.libre)) - p.dejar_en_bodega) : null;
    const velocidad = f.vv / ventana;
    const clave = claveDe(f.tienda, f.sku);
    return {
      ...f, clave, agregado: agregadas.has(clave), velocidad, objetivo, pidio,
      libre_total: libreTotal, bodega: libreTotal, propuesta: 0, estado: "cubierto",
      ganador_agotado: false,
      aguanta: velocidad > 0 && f.stock !== null ? f.stock / velocidad : null,
      sube: velocidad > 0 && f.v7 / 7 > 1.5 * velocidad,
    };
  });

  // El MISMO producto pedido por varias tiendas activas y lo libre no alcanza:
  // se reparte en proporción a lo que pidió cada una.
  const porProducto = new Map<number, Renglon[]>();
  for (const r of base) {
    if (r.product_id === null || r.pidio <= 0) continue;
    const l = porProducto.get(r.product_id) ?? [];
    l.push(r);
    porProducto.set(r.product_id, l);
  }
  for (const lista of porProducto.values()) {
    if (lista.length < 2) continue;
    const libre = lista[0].libre_total ?? 0;
    const total = suma(lista.map((r) => r.pidio));
    if (total <= libre) continue;
    let resto = libre;
    lista.forEach((r, i) => {
      const parte = i === lista.length - 1 ? resto : Math.floor((libre * r.pidio) / total);
      resto -= parte;
      r.bodega = parte;
      r.repartido = `${lista.length} tiendas lo piden y Odoo tiene ${libre} libres: ${parte} para ésta.`;
    });
  }

  for (const r of base) {
    if (r.bodega === null) {
      r.estado = "pendiente";
    } else if (r.pidio <= 0) {
      r.estado = "cubierto";
    } else {
      r.estado = r.bodega >= r.pidio ? "aprobado" : "recorte";
      const n = Math.min(r.bodega, r.pidio);
      r.propuesta = n >= p.min_piezas ? n : 0;
    }
    r.ganador_agotado = r.vv >= p.min_ventas && r.libre_total === 0 && (r.stock ?? 0) === 0;
  }
  return base;
}

/** Los totales B del prompt para un conjunto de renglones y sus cantidades finales. */
export function totalesDe(renglones: Renglon[], cantidad: (r: Renglon) => number): Totales {
  const revisados = renglones.filter((r) => r.estado !== "pendiente" && r.pidio > 0);
  const pedRev = suma(revisados.map((r) => r.pidio));
  const pedidas = suma(renglones.map((r) => r.pidio));
  const aMandar = suma(renglones.map(cantidad));
  return {
    renglones: renglones.length,
    pedidas,
    propuestas: suma(renglones.map((r) => r.propuesta)),
    a_mandar: aMandar,
    skus_a_mandar: renglones.filter((r) => cantidad(r) > 0).length,
    tasa_validado: pedRev ? Math.round((suma(revisados.map((r) => Math.min(r.bodega ?? 0, r.pidio))) / pedRev) * 1000) / 10 : null,
    final_vs_pedido: pedidas ? Math.round((aMandar / pedidas) * 1000) / 10 : null,
    pendientes: renglones.filter((r) => r.estado === "pendiente").length,
  };
}
