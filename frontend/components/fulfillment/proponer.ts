/**
 * La propuesta de «Crear FULL»: cuánto mandar de cada SKU a FULL.
 *
 * Se calcula AQUÍ, en el navegador, para que mover la cobertura o el mínimo se
 * vea al instante sin volver a leer Odoo. Los insumos (ventas, stock en FULL, en
 * camino, borradores y libre por almacén) los arma
 * `backend/services/fulfillment_full.py`; el encabezado de ese archivo explica de
 * dónde sale cada uno.
 *
 *   venta diaria = ventas FULL de 30 días / 30
 *   objetivo     = ⌈venta diaria × días de cobertura⌉
 *   necesidad    = objetivo − en FULL hoy − en camino − en borradores
 *   libre        = TEXCO + TEXCO II − lo que se deja en bodega (DROP)
 *   sugerido     = min(necesidad, libre). Si las DOS cuentas piden el mismo SKU y
 *                  no alcanza, lo libre se reparte en proporción a lo que
 *                  necesita cada una (las dos compiten por el mismo stock de
 *                  Odoo: 9 SKUs el 18-sep, y en 4 la suma pasaba lo libre).
 *
 * Nada de esto escribe: es la sugerencia que la persona corrige renglón por
 * renglón antes de crear.
 */

import type { Cuenta, FilaPropuesta, ParametrosFull } from "./tipos";

export const CUENTAS: Cuenta[] = ["Kubera", "San Corpe"];

export type EstadoPropuesta =
  | "mandar"        // necesita y Odoo alcanza
  | "tope_odoo"     // necesita más de lo que Odoo tiene libre: va lo que hay
  | "sin_odoo"      // necesita y Odoo no tiene (o menos del mínimo): señal de COMPRAS, no de FULL
  | "no_en_odoo"    // el SKU no existe en Odoo
  | "bajo_minimo"   // le falta menos del mínimo por renglón
  | "cubierto"      // lo que hay en FULL + en camino alcanza la cobertura
  | "poca_venta";   // vende menos del mínimo en 30 días: el ritmo es ruido

export interface Propuesta extends FilaPropuesta {
  venta_dia: number;
  /** Cuántos días aguanta lo que hay HOY en FULL. null = stock desconocido o sin venta. */
  aguanta: number | null;
  objetivo: number;
  necesidad: number;
  libre_total: number | null;
  /** Lo libre que le toca a esta cuenta después del reparto. */
  libre_asignado: number | null;
  sugerido: number;
  estado: EstadoPropuesta;
  /** Si el libre se repartió con la otra cuenta, cómo. */
  repartido?: string;
  /** La venta de 7 días va muy por encima del ritmo de 30. */
  sube: boolean;
}

const suma = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

export function proponer(cuentas: Partial<Record<Cuenta, FilaPropuesta[]>>,
                         p: ParametrosFull): Record<Cuenta, Propuesta[]> {
  const base = {} as Record<Cuenta, Propuesta[]>;
  for (const c of CUENTAS) {
    base[c] = (cuentas[c] ?? []).map((f) => {
      const vd = f.v30 / 30;
      // Multiplicar ANTES de dividir: 125/30×30 da 125.00000000000001 y el
      // redondeo hacia arriba lo volvía 126.
      const objetivo = Math.ceil((f.v30 * p.cobertura_dias) / 30);
      const necesidad = objetivo - (f.stock_full ?? 0) - f.en_camino - f.borrador;
      const libreTotal = f.libre ? Math.max(0, suma(Object.values(f.libre)) - p.dejar_en_bodega) : null;
      return {
        ...f, venta_dia: vd, objetivo, necesidad,
        aguanta: vd > 0 && f.stock_full !== null ? f.stock_full / vd : null,
        libre_total: libreTotal, libre_asignado: libreTotal, sugerido: 0, estado: "cubierto",
        sube: vd > 0 && f.v7 / 7 > 1.5 * vd,
      };
    });
  }

  // El mismo producto pedido por las DOS cuentas y lo libre no alcanza: se
  // reparte en proporción a la necesidad de cada una.
  const porProducto = new Map<number, { cuenta: Cuenta; r: Propuesta }[]>();
  for (const c of CUENTAS) {
    for (const r of base[c]) {
      if (r.product_id === null || r.necesidad <= 0 || r.v30 < p.min_ventas_30) continue;
      const lista = porProducto.get(r.product_id) ?? [];
      lista.push({ cuenta: c, r });
      porProducto.set(r.product_id, lista);
    }
  }
  for (const lista of porProducto.values()) {
    if (lista.length < 2) continue;
    const libre = lista[0].r.libre_total ?? 0;
    const total = suma(lista.map((x) => x.r.necesidad));
    if (total <= libre) continue;
    let resto = libre;
    lista.forEach((x, i) => {
      const parte = i === lista.length - 1 ? resto : Math.floor((libre * x.r.necesidad) / total);
      resto -= parte;
      x.r.libre_asignado = parte;
      const otra = lista.filter((y) => y !== x).map((y) => y.cuenta).join(", ");
      x.r.repartido = `Las dos cuentas lo piden y Odoo tiene ${libre} libres: ${parte} para ${x.cuenta}, `
        + `el resto para ${otra}.`;
    });
  }

  for (const c of CUENTAS) {
    for (const r of base[c]) {
      if (r.v30 < p.min_ventas_30) { r.estado = "poca_venta"; continue; }
      if (r.necesidad <= 0) { r.estado = "cubierto"; continue; }
      if (r.necesidad < p.min_piezas) { r.estado = "bajo_minimo"; continue; }
      if (r.libre_total === null) { r.estado = "no_en_odoo"; continue; }
      const puede = Math.min(r.necesidad, r.libre_asignado ?? 0);
      if (puede < p.min_piezas) { r.estado = "sin_odoo"; continue; }
      r.sugerido = puede;
      r.estado = puede < r.necesidad ? "tope_odoo" : "mandar";
    }
    // Lo más urgente arriba: lo que se manda, y dentro de eso lo que menos aguanta.
    const peso: Record<EstadoPropuesta, number> = {
      mandar: 0, tope_odoo: 0, sin_odoo: 1, no_en_odoo: 1, bajo_minimo: 2, cubierto: 3, poca_venta: 4,
    };
    base[c].sort((a, b) => peso[a.estado] - peso[b.estado]
      || (a.aguanta ?? -1) - (b.aguanta ?? -1)
      || b.v30 - a.v30);
  }
  return base;
}

/** El CSV de la lista final (para quien la siga capturando a mano en Odoo). */
export function csvDe(filas: Propuesta[], cantidades: Record<string, number>, cuenta: Cuenta): string {
  const enc = ["sku", "producto", "cuenta", "publicacion", "vende_30d", "en_full_hoy", "en_camino",
               "en_borradores", "libre_texco", "libre_texco_ii", "sugerido", "a_mandar"];
  const celda = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lineas = filas
    .filter((f) => (cantidades[f.sku] ?? 0) > 0)
    .map((f) => [f.sku, f.nombre, cuenta, f.listing_id, f.v30, f.stock_full, f.en_camino, f.borrador,
                 f.libre?.["TEXCO"], f.libre?.["TEXCO II"], f.sugerido, cantidades[f.sku]].map(celda).join(","));
  return [enc.join(","), ...lineas].join("\n");
}
