/**
 * DATOS DE DISEÑO de la pestaña FULLFILMENT — todavía no hay backend.
 *
 * Salen del mockup `FULLFILMENT.dc.html` (handoff de diseño, 14-sep-2026) y valen
 * como CONTRATO DE FORMA, no de contenido. Desde v0.523.0 los envíos, el enviado
 * a FULL/FBA/WFS, los envíos sin número, los días de proceso y la captura por
 * KAM ya salen de Odoo (`/api/fulfillment/envios`); aquí queda SOLO lo que aún no
 * tiene fuente:
 *   · stock en FULL por cuenta y stock FBA (cifras del mockup, cada tarjeta que
 *     las usa lleva el chip «diseño»);
 *   · planeación semanal y ficha de SKU (ejemplos simulados);
 *   · los ejemplos de la pantalla de Variaciones (`TABLERO`, `DIAS`, `ENVIOS`).
 *
 * Cada bloque se borra el día que su dato tenga fuente.
 */

import type {
  CuentaFull, DiaSemana, Envio, Instante, RenglonPlan,
} from "./tipos";

export const FECHA_DISENO = "14 sep 2026";

/** Hora de CDMX (UTC−6 todo el año desde 2022). */
const cdmx = (fecha: string, aprox = false): Instante =>
  ({ ts: `${fecha}:00-06:00`, aprox });

export const TABLERO = {
  enviadoFull: { piezas: 116_895, ordenes: 204, desde: "13 ene", hasta: "14 sep", pctDePedido: 97.9 },
};

export const DIAS: DiaSemana[] = [
  { dia: "lun", nombre: "lunes", ordenes: 28, salidas: 45 },
  { dia: "mar", nombre: "martes", ordenes: 52, salidas: 40 },
  { dia: "mié", nombre: "miércoles", ordenes: 32, salidas: 23 },
  { dia: "jue", nombre: "jueves", ordenes: 60, salidas: 35 },
  { dia: "vie", nombre: "viernes", ordenes: 24, salidas: 50 },
  { dia: "sáb", nombre: "sábado", ordenes: 10, salidas: 13 },
];

export const FULL_POR_CUENTA: CuentaFull[] = [
  { cuenta: "Kubera", enCero: 734, publicaciones: 1_010, piezas: 12_428, conStock: 276 },
  { cuenta: "San Corpe", enCero: 600, publicaciones: 874, piezas: 7_914, conStock: 274 },
];

export const FBA = {
  cuenta: "San Corpe",
  disponibles: 1_923, reservadas: 1_568, enCamino: 1_390,
  enPreparacion: 1_239, enviadas: 149, recibiendose: 2,
  ordenes: 40, piezas: 4_353, pctDePedido: 100,
};

export const ENVIOS: Envio[] = [
  {
    orden: "S04918", envio: "75652884", canal: "meli", cuenta: "San Corpe", kam: "Thalía",
    piezas: 1_240, pedidas: 1_265, cajas: 31, estado: "recepcion",
    etapas: [cdmx("2026-09-03T09:15"), cdmx("2026-09-05T13:22"), null, null, null],
  },
  {
    orden: "S04902", envio: "70688003", canal: "meli", cuenta: "Kubera", kam: "Cinthya",
    piezas: 890, pedidas: 890, cajas: 23, estado: "cerrado", tasaPct: 96,
    etapas: [cdmx("2026-08-27T08:58"), cdmx("2026-08-29T12:10"), cdmx("2026-08-31T09:14"),
             cdmx("2026-08-31T11:00", true), cdmx("2026-09-01T16:41")],
  },
  {
    orden: "S04877", envio: null, canal: "meli", cuenta: "San Corpe", kam: "Thalía",
    piezas: 430, pedidas: 455, cajas: 11, estado: "sinEnlazar",
    etapas: [cdmx("2026-08-20T09:30"), cdmx("2026-08-22T14:48"), null, null, null],
  },
  {
    orden: "S04861", envio: "71421571", canal: "meli", cuenta: "Kubera", kam: "Cinthya",
    piezas: 1_690, pedidas: 1_690, cajas: 42, estado: "cerrado", tasaPct: 88,
    etapas: [cdmx("2026-08-19T09:02"), cdmx("2026-08-21T11:35"), cdmx("2026-08-24T08:40"),
             cdmx("2026-08-24T10:30", true), cdmx("2026-08-24T21:07")],
  },
  {
    orden: "S04840", envio: "71421653", canal: "meli", cuenta: "San Corpe", kam: "Thalía",
    piezas: 310, pedidas: 310, cajas: 8, estado: "sinVenta", tasaPct: 100,
    etapas: [cdmx("2026-08-12T08:45"), cdmx("2026-08-14T10:02"), cdmx("2026-08-18T09:20"),
             cdmx("2026-08-18T12:00", true), null],
  },
  {
    orden: "S04799", envio: "FBA15XKQ2", canal: "amazon", cuenta: "San Corpe", kam: "Nancy",
    piezas: 620, pedidas: 620, cajas: 16, estado: "amazonSinLectura",
    etapas: [cdmx("2026-09-10T08:20"), cdmx("2026-09-12T12:40"), null, null, null],
  },
  {
    orden: null, envio: null, canal: "walmart", cuenta: null, kam: null,
    piezas: null, pedidas: null, cajas: null, estado: "wfs",
    etapas: [null, null, null, null, null],
  },
];

export const PLAN: RenglonPlan[] = [
  { sku: "TEC-0664-ROS", nombre: "Set de brochas rosa", destino: "meli_bekura", libre: 318, pidio: 240, bodega: 240, ia: 210, estado: "aprobado" },
  { sku: "ORG-0841-ROS", nombre: "Organizador 6 cajones", destino: "meli_bekura", libre: 96, pidio: 180, bodega: 96, ia: 96, estado: "recorte" },
  { sku: "MASC-1022-CAF", nombre: "Mascarilla café x24", destino: "meli_sank", libre: 540, pidio: 300, bodega: 300, ia: 360, estado: "aprobado" },
  { sku: "COC-0153-MET", nombre: "Juego de sartenes", destino: "meli_bekura", libre: 0, pidio: 120, bodega: 0, ia: 0, estado: "recorte" },
  { sku: "VIA-0024-NEG", nombre: "Maleta cabina negra", destino: "fba", libre: 30, pidio: 60, bodega: 29, ia: 29, estado: "recorte" },
  { sku: "HERR-0029", nombre: "Caja de herramientas", destino: "meli_sank", libre: 210, pidio: 150, bodega: 150, ia: 150, estado: "aprobado" },
  { sku: "EST-0091", nombre: "Repisa flotante", destino: "drop", libre: 72, pidio: 40, bodega: 40, ia: 40, estado: "aprobado" },
  { sku: "ROP-0509-NEG-X", nombre: "Playera negra talla X", destino: "wfs", libre: 140, pidio: 90, bodega: null, ia: null, estado: "pendiente" },
  { sku: "ACC-0653-CHE", nombre: "Faros de niebla", destino: "meli_bekura", libre: 65, pidio: 75, bodega: null, ia: null, estado: "pendiente" },
];

export const SEMANA_PLAN = {
  iso: 38, rango: "14 → 20 sep 2026",
  solicitud: { cuando: cdmx("2026-09-14T10:20"), destino: "meli_bekura" },
  revisados: 7,
};

export const SKU_EJEMPLO = {
  sku: "TEC-0664-ROS", nombre: "Set de brochas rosa", cuenta: "Kubera" as const,
  publicacion: "MLM1874553201", tipo: "Premium (gold_pro)",
  enFullHoy: 142, coberturaDias: 18, piezasDia: 7.8,
  visitas30d: 1_284,
  eventos: [
    { cuando: cdmx("2026-08-27T09:14"), titulo: "Envío 75652884 recibido en FULL",
      enviadas: 180, recibidas: 172, rechazadas: 8 },
    { cuando: cdmx("2026-08-27T11:00", true), titulo: "La publicación se prende en FULL" },
    { cuando: cdmx("2026-08-28T16:41"), titulo: "Primera venta desde FULL", horasDesdeActivacion: 31 },
    { cuando: null, titulo: "Visitas al momento de la primera venta" },
    { cuando: cdmx("2026-09-14T08:02"), titulo: "Sugerido de resurtido: 210 piezas" },
  ],
  ventaDesdeActivacion: [
    { dias: 7, pct: 22 }, { dias: 14, pct: 41 }, { dias: 30, pct: 74 },
  ],
  precio: {
    vigente: 396,
    cambios: [
      { cuando: cdmx("2026-09-02T11:02"), antes: 429, despues: 396 },
      { cuando: cdmx("2026-08-19T09:45"), antes: 449, despues: 429 },
    ],
    historialDesde: "17 jul",
  },
};
