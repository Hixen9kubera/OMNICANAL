"use client";

/**
 * /automatizacion — Lo que el panel hace solo, y con qué inventario lo hizo.
 *
 * Cada venta de TikTok o Temu se vuelve orden de venta en Odoo. Ésta es la
 * única pantalla donde se vigila eso, y se abre con dos preguntas en este
 * orden: "¿está funcionando?" (de un vistazo) y "¿esta venta cómo salió?".
 *
 * LA COLUMNA QUE JUSTIFICA LA PANTALLA es "stock al momento de la venta". No
 * se puede pedir en vivo: `free_qty` ya cambió. Sale de una foto congelada al
 * crear la orden, y es el único dato con el que se contesta "¿por qué se
 * sobrevendió?" tres días después. Por eso vive en el DETALLE: se consulta
 * cuando alguien pregunta "por qué", no de paso.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * LA DECISIÓN CENTRAL DEL DISEÑO (handoff de Claude Design, 4-sep-2026)
 * ─────────────────────────────────────────────────────────────────────────
 * Hay 15 desenlaces y sólo 5 piden que alguien haga algo. La versión anterior
 * los pintaba a todos con el mismo peso —y varios en rojo—, así que las 2
 * órdenes que importaban se perdían entre las 28 que no. Ahora las tres
 * familias se distinguen SIN LEER, con tres recursos apilados:
 *
 *   familia        barra izq.    punto      texto              motivo
 *   pide acción    sí, en color  color      13.5px/700 color   visible
 *   salió bien     verde pálido  #10B981    13.5px/600 gris    sólo si aporta
 *   no pide nada   NINGUNA       #CBD5E1    13.5px/500 apagado visible, gris
 *
 * Los 7 inertes dejan de parecer fallos porque son los únicos sin barra.
 *
 * Y hay DOS ROJOS, no uno: `#E11D48` para los errores de verdad, y ÁMBAR
 * `#F59E0B` para "sin respaldo de inventario" — es grave, pero la orden
 * existe y el cliente ya pagó. Un solo rojo volvía a aplanar lo que este
 * rediseño vino a separar.
 *
 * Solo admin: la orden trae la guía del comprador.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Braces, Camera, CheckCircle2, ChevronDown, ChevronRight,
  ChevronUp, Clock, Copy, ExternalLink, ImageIcon, Loader2, MousePointerClick,
  Package, Power, Radio, RotateCw, Truck, X,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";

/* ══════════════════════════════════════════════════════════════════════════
   TOKENS — del handoff. Se escriben una vez y nadie los adivina después.
   ══════════════════════════════════════════════════════════════════════════ */

/** Un canal, con su marca. El acento pinta el filo de su tarjeta; el fondo del
 *  panel sigue siendo claro. */
const CANALES = [
  {
    id: "tiktok",
    nombre: "TikTok Shop",
    mono: "TT",
    base: "#111827",      // fondo del monograma y de la píldora activa
    punto: "#25F4EE",     // el cian de TikTok: SÓLO en el punto y el monograma
    suave: "#F1F1F4",     // cabecera de su tarjeta
    borde: "#d7d7de",     // filo de la píldora inactiva
    tinta: "#111827",
    tintaSuave: "#6b7280",
  },
  {
    id: "temu",
    nombre: "Temu",
    mono: "Tm",
    base: "#FB7701",
    punto: "#FB7701",
    suave: "#FFF0E3",
    borde: "#f0d3b6",
    tinta: "#7c3a02",
    tintaSuave: "#9a5a25",
  },
] as const;

type CanalId = (typeof CANALES)[number]["id"];

/** Las cinco variantes visuales. `marca` vacía = sin barra, que es lo que
 *  separa a los inertes de todo lo demás. */
const V = {
  rojo:     { marca: "#E11D48", filaBg: "#FFFBFB", punto: "#E11D48", color: "#9F1239", peso: 700, motivoColor: "#be123c" },
  ambar:    { marca: "#F59E0B", filaBg: "#FFFDF7", punto: "#F59E0B", color: "#92400E", peso: 700, motivoColor: "#b45309" },
  ok:       { marca: "#A7F3D0", filaBg: "#ffffff", punto: "#10B981", color: "#334155", peso: 600, motivoColor: "#94a3b8" },
  inerte:   { marca: "",        filaBg: "#ffffff", punto: "#CBD5E1", color: "#94A3B8", peso: 500, motivoColor: "#b6c0cf" },
  obs:      { marca: "",        filaBg: "#ffffff", punto: "#7DD3FC", color: "#64748B", peso: 500, motivoColor: "#94a3b8" },
  dividido: { marca: "#818CF8", filaBg: "#FAFBFF", punto: "#4F46E5", color: "#3730A3", peso: 700, motivoColor: "#4338ca" },
} as const;

type Variante = keyof typeof V;

/** Los desenlaces, con su rótulo textual y su familia.
 *
 *  EL VOCABULARIO ES LITERAL: son las palabras que ya se midieron contra la
 *  operación. Cambiar "Nació cancelada" por "Cancelada al nacer" obliga a
 *  reaprender la pantalla y no gana nada. */
const ESTADOS: Record<string, { txt: string; v: Variante; urgente?: string }> = {
  // ── Piden que alguien haga algo ──────────────────────────────────────
  sku_sin_producto:       { txt: "Error · SKU sin producto en Odoo", v: "rojo" },
  error:                  { txt: "Error", v: "rojo" },
  no_se_pudo_confirmar:   { txt: "No se pudo confirmar", v: "rojo", urgente: "Sobreventa viva" },
  no_se_pudo_cancelar:    { txt: "No se pudo cancelar", v: "rojo", urgente: "Orden viva, venta muerta" },
  // ── Salieron bien ────────────────────────────────────────────────────
  confirmada:             { txt: "Confirmada (Odoo reservó)", v: "ok" },
  creada:                 { txt: "Creada (en borrador)", v: "ok" },
  ya_existia:             { txt: "Ya existía", v: "ok" },
  // ── No piden nada ────────────────────────────────────────────────────
  nacio_cancelada:        { txt: "Nació cancelada", v: "inerte" },
  sin_orden:              { txt: "Cancelada · no había orden", v: "inerte" },
  ya_cancelada:           { txt: "Ya estaba cancelada", v: "inerte" },
  cancelada:              { txt: "Cancelada", v: "inerte" },
  apagado:                { txt: "Apagado", v: "inerte" },
  canal_apagado:          { txt: "Canal apagado", v: "inerte" },
  simulado:               { txt: "Simulación", v: "inerte" },
  solo_registro:          { txt: "Observando", v: "obs" },
  solo_registro_cancelar: { txt: "Observando · cancelación", v: "obs" },
};

/** Los almacenes que mira el automatismo, en su orden de preferencia. La foto
 *  del stock viene llaveada por ID —no por nombre: el nombre se puede
 *  renombrar en Odoo, el id no. */
const ALMACENES: Array<[string, string]> = [
  ["135", "TEXCO"],
  ["150", "TEXCO II"],
];

/** Los campos que viajan a Odoo en cada orden. Es la respuesta a "¿qué
 *  necesita el sistema para generar la orden de venta?" — y por eso dice de
 *  dónde sale cada uno, no solo cómo se llama. */
const PARAMETROS = [
  { campo: "partner_id", valor: "tiktokshop (1739238) · temu (1738206)",
    fuente: "fijo por canal — el mismo que ya usa Gaby. El comprador real NO entra a Odoo (es dato personal y quien envía es el marketplace)" },
  { campo: "warehouse_id", valor: "TEXCO (135) o TEXCO II (150)",
    fuente: "SE ELIGE leyendo free_qty por almacén: gana el primero que cubra la orden completa; si ninguno la cubre solo, se reparte" },
  { campo: "client_order_ref", valor: "id de la orden del canal",
    fuente: "la llave de idempotencia — impide crear la misma venta dos veces. Al dividir lleva sufijo #1 / #2" },
  { campo: "origin", valor: "TikTok <id> · Temu <id>",
    fuente: "etiqueta legible para quien surte" },
  { campo: "date_order", valor: "fecha de la VENTA",
    fuente: "no la de captura: si no, los cortes por día quedan corridos" },
  { campo: "order_line.product_id", valor: "producto de Odoo",
    fuente: "se resuelve por default_code = nuestro SKU. Si UNA línea no resuelve, NO se crea la orden a medias" },
  { campo: "order_line.product_uom_qty", valor: "unidades",
    fuente: "TikTok manda una línea por pieza y se agrupan por (SKU, precio); Temu ya manda la cantidad" },
  { campo: "order_line.price_unit", valor: "TikTok: el cobrado · Temu: el de catálogo",
    fuente: "TikTok da `sale_price`; Temu NO expone importes (3000032), así que se usa el precio publicado" },
  { campo: "order_line.name", valor: "[SKU] título del canal",
    fuente: "el título con el que el comprador lo compró, no el del ERP" },
];

/* ══════════════════════════════════════════════════════════════════════════
   TIPOS
   ══════════════════════════════════════════════════════════════════════════ */

interface Linea {
  sku: string;
  titulo: string | null;
  imagen: string | null;
  cantidad: number;
  precio_unitario: number | null;
  /** La foto del stock del momento, llaveada por ID de almacén. */
  stock_libre: Record<string, number> | null;
}

interface OrdenOdoo {
  canal: string;
  external_order_id: string;
  odoo_order_id: number | null;
  odoo_name: string | null;
  estado: string | null;
  accion: string;
  almacen: string | null;
  cobertura: string | null;
  guia: string | null;
  paqueteria: string | null;
  total: number | null;
  motivo: string | null;
  creado_at: string;
  /** Cuándo COMPRÓ el cliente. Null si la venta no está en channel.orders. */
  venta_at: string | null;
  lineas: Linea[];
}

interface Estado {
  odoo_ventas: {
    encendido: boolean;
    persistido: boolean;
    por_omision: boolean;
    actualizado_por: string | null;
    motivo: string | null;
    solo_registro: boolean;
    confirmar: boolean;
    canales: string[];
    escalon: string;
    escalon_id: "apagado" | "observando" | "creando" | "creando_confirmando";
    odoo_url: string;
    canales_estado?: Record<string, {
      encendido: boolean;
      persistido: boolean;
      actualizado_por: string | null;
      motivo: string | null;
    }>;
  };
  resumen: { total_30d: number; parciales: number; errores: number; nota?: string };
  publicaciones?: Record<string, { total: number; activas: number | null }>;
}

/* ══════════════════════════════════════════════════════════════════════════
   AYUDANTES
   ══════════════════════════════════════════════════════════════════════════ */

const MESES = ["ene", "feb", "mar", "abr", "may", "jun",
               "jul", "ago", "sep", "oct", "nov", "dic"];

/** "02 sep 11:02" — corto, en el orden en que se lee una fecha en español. */
function fecha(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${dd} ${MESES[d.getMonth()]} ${hh}:${mm}`;
}

/** El chip de rezago SÓLO cuando la brecha existe. Una compra de agosto
 *  procesada hoy no debe parecer una venta vieja — ni una venta de hoy. */
function rezago(venta: string | null, proceso: string): string | null {
  if (!venta) return null;
  const a = new Date(venta).getTime();
  const b = new Date(proceso).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  const dias = Math.floor((b - a) / 86_400_000);
  if (dias < 1) return null;
  return `${dias} d de rezago`;
}

function haceCuanto(iso: string | null): string {
  if (!iso) return "nunca";
  const min = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (min < 1) return "hace instantes";
  if (min < 60) return `hace ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.floor(h / 24)} d`;
}

const dinero = (n: number | null) =>
  n === null || n === undefined
    ? "—"
    : new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(n);

/** ¿Requiere que alguien haga algo? Es la MISMA definición que usa el filtro
 *  del backend (`solo_problemas`), y tiene que seguir siéndolo: dos criterios
 *  que contestan la misma pregunta y no coinciden es peor que no tener filtro. */
function pideAccion(o: OrdenOdoo): boolean {
  return o.cobertura === "parcial"
    || ["error", "sku_sin_producto", "no_se_pudo_cancelar", "no_se_pudo_confirmar"]
        .includes(o.accion);
}

/** El desenlace de una venta, ya resuelto a estilo.
 *
 *  ORDEN DE PRECEDENCIA, y no es arbitrario:
 *    1. `parcial` gana sobre todo — se creó sin respaldo, y eso es lo que hay
 *       que saber aunque la acción diga "confirmada".
 *    2. `dividida` después — es información, no fallo.
 *    3. la acción, para todo lo demás. */
function desenlace(o: OrdenOdoo): { txt: string; v: Variante; urgente?: string } {
  if (o.cobertura === "parcial") {
    return { txt: "Sin respaldo de inventario", v: "ambar" };
  }
  if (o.cobertura === "dividida") {
    return { txt: "Surtido dividido", v: "dividido" };
  }
  return ESTADOS[o.accion] ?? { txt: o.accion, v: "inerte" };
}

/** Las partes de un surtido dividido. La bitácora guarda UNA fila por venta
 *  —su llave ES la venta— con los nombres unidos: "S37009 + S37010". Aquí se
 *  vuelven a separar para poder pintarlas como lo que son: dos entregas de la
 *  misma compra. */
function partes(o: OrdenOdoo): Array<{ nombre: string; almacen: string }> {
  const nombres = (o.odoo_name ?? "").split(" + ").map((s) => s.trim()).filter(Boolean);
  const almacenes = (o.almacen ?? "").split(" + ").map((s) => s.trim()).filter(Boolean);
  return nombres.map((nombre, i) => ({ nombre, almacen: almacenes[i] ?? "—" }));
}

/* ══════════════════════════════════════════════════════════════════════════
   PIEZAS
   ══════════════════════════════════════════════════════════════════════════ */

function Switch({
  activo, ocupado, etiqueta, onClick, ancho = 46,
}: {
  activo: boolean; ocupado: boolean; etiqueta: string;
  onClick: () => void; ancho?: number;
}) {
  const alto = ancho === 46 ? 26 : 22;
  const bola = ancho === 46 ? 20 : 16;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={ocupado}
      role="switch"
      aria-checked={activo}
      aria-label={etiqueta}
      className="relative shrink-0 rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50"
      style={{ width: ancho, height: alto, background: activo ? "#10b981" : "#cbd5e1" }}
    >
      <span
        className="absolute rounded-full bg-white shadow transition-all"
        style={{
          width: bola, height: bola, top: 3,
          left: activo ? ancho - bola - 3 : 3,
        }}
      />
    </button>
  );
}

/** El recorrido de tres pasos. El activo en verde; los anteriores, apagados.
 *
 *  ES INFORMATIVO, NO UN SELECTOR — todavía. El escalón vive en variables de
 *  Railway (`ODOO_VENTAS_SOLO_REGISTRO`, `ODOO_VENTAS_CONFIRMAR`), no en la
 *  tabla de banderas, así que la pantalla puede LEERLO pero no moverlo.
 *  Pintarlo como botones sería prometer un control que no existe. */
function Escalon({ id }: { id: Estado["odoo_ventas"]["escalon_id"] }) {
  const pasos: Array<[string, string]> = [
    ["observando", "Observando"],
    ["creando", "Creando"],
    ["creando_confirmando", "Creando y confirmando"],
  ];
  return (
    <div className="flex shrink-0 items-center overflow-hidden rounded-[10px] border"
         style={{ borderColor: "#e6e9f2" }}
         title="El escalón se fija con las variables de Railway; aquí sólo se lee">
      {pasos.map(([k, txt], i) => {
        const activo = k === id;
        return (
          <span
            key={k}
            className="px-3 py-[7px] text-[11.5px]"
            style={{
              borderLeft: i ? "1px solid #e6e9f2" : undefined,
              fontWeight: activo ? 800 : 700,
              color: activo ? "#047857" : "#94a3b8",
              background: activo ? "#ECFDF5" : "#fbfcfe",
            }}
          >
            {txt}
          </span>
        );
      })}
    </div>
  );
}

function Kpi({
  rotulo, valor, pie, tono,
}: { rotulo: string; valor: number; pie: string; tono?: "ambar" | "rojo" }) {
  const color = tono === "ambar" ? "#B45309" : tono === "rojo" ? "#9F1239" : "#0f172a";
  return (
    <div className="border-l px-[22px] py-[14px] first:border-l-0" style={{ borderColor: "#eef1f6" }}>
      <div className="text-[11px] font-bold uppercase tracking-[.07em] text-slate-400">
        {rotulo}
      </div>
      <div className="mt-[5px] flex items-baseline gap-2">
        <span className="font-mono text-[26px] font-extrabold" style={{ color }}>{valor}</span>
        <span className="text-[12px]" style={{ color: tono ? color : "#94a3b8" }}>{pie}</span>
      </div>
    </div>
  );
}

/* ── El renglón ─────────────────────────────────────────────────────────── */

const GRID = "4px 250px 88px 116px 186px 96px 1fr 110px 26px";

function FilaOrden({
  o, abierta, onAbrir, odooUrl,
}: {
  o: OrdenOdoo; abierta: boolean; onAbrir: () => void; odooUrl: string;
}) {
  const d = desenlace(o);
  const s = V[d.v];
  const piezas = o.lineas.reduce((n, l) => n + (l.cantidad ?? 0), 0);
  const rz = rezago(o.venta_at, o.creado_at);
  const dividido = d.v === "dividido";

  const Chevron = abierta ? ChevronUp : ChevronDown;

  return (
    <div style={{ background: s.filaBg, borderBottom: "1px solid #f4f6fa" }}>
      {/* ── ANCHO · SURTIDO DIVIDIDO: rejilla propia. Lo que hay que leer no
             son columnas, es una frase: una venta, dos órdenes, una guía. ── */}
      {dividido ? (
        <div
          onClick={onAbrir}
          className="hidden cursor-pointer items-center gap-3 pr-5 lg:grid"
          style={{ gridTemplateColumns: "4px 250px 1fr" }}
        >
          <span className="h-full min-h-[52px]" style={{ background: s.marca }} />
          <div className="flex items-center gap-[7px] py-3">
            <span className="h-[7px] w-[7px] shrink-0 rounded-full" style={{ background: s.punto }} />
            <span className="text-[13.5px] font-bold" style={{ color: s.color }}>Surtido dividido</span>
            <span className="shrink-0 rounded-full px-[8px] py-[2px] text-[9.5px] font-extrabold uppercase tracking-[.05em]"
                  style={{ background: "#EEF0FF", color: "#4338CA" }}>
              {partes(o).length} órdenes · 1 venta
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-[11.5px]" style={{ color: "#4338ca" }}>
            <span>Ningún almacén tenía la venta completa</span>
            {o.guia && (
              <span className="inline-flex items-center gap-[6px] rounded-full px-[9px] py-[3px] font-mono text-[11.5px] font-bold"
                    style={{ background: "#EEF0FF" }}>
                <Truck className="h-3 w-3" />{o.guia} · una sola guía
              </span>
            )}
            <span className="ml-auto font-mono text-[13px] font-bold text-slate-900">{dinero(o.total)}</span>
            <span className="whitespace-nowrap text-slate-400">
              compra <span className="font-mono font-bold text-slate-600">{fecha(o.venta_at)}</span>
              {" → "}
              <span className="font-mono font-bold text-slate-600">{fecha(o.creado_at)}</span>
            </span>
            <Chevron className="h-4 w-4" style={{ color: "#a5b4fc" }} />
          </div>
        </div>
      ) : (
      <div
        onClick={onAbrir}
        className="hidden cursor-pointer items-center gap-3 pr-5 lg:grid"
        style={{ gridTemplateColumns: GRID }}
      >
        <span className="h-full min-h-[54px]" style={{ background: s.marca || "transparent" }} />
        <div className="min-w-0 py-[10px]">
          <div className="flex flex-wrap items-center gap-x-[7px] gap-y-1">
            <span className="h-[7px] w-[7px] shrink-0 rounded-full" style={{ background: s.punto }} />
            <span className="text-[13.5px] leading-tight" style={{ fontWeight: s.peso, color: s.color }}>
              {d.txt}
            </span>
            {d.urgente && (
              <span className="rounded-full bg-rose-50 px-[7px] py-[2px] text-[9.5px] font-extrabold uppercase tracking-[.05em] text-rose-800">
                {d.urgente}
              </span>
            )}
          </div>
          {o.motivo && (
            <div className="mt-[3px] truncate pl-[14px] text-[11.5px]" style={{ color: s.motivoColor }}>
              {o.motivo}
            </div>
          )}
        </div>
        <div className="truncate font-mono text-[13px] font-bold"
             style={{ color: o.odoo_name ? (d.v === "inerte" ? "#94a3b8" : "#0f172a") : "#cbd5e1" }}>
          {o.odoo_name ?? "—"}
        </div>
        <div className="truncate text-[12px] font-semibold text-slate-600">{o.almacen ?? "—"}</div>
        <div className="min-w-0">
          {o.guia ? (
            <>
              <div className="truncate font-mono text-[12.5px] font-bold text-slate-700">{o.guia}</div>
              <div className="truncate text-[11px] text-slate-400">{o.paqueteria ?? ""}</div>
            </>
          ) : (
            <div className="text-[11px] text-slate-400">sin guía</div>
          )}
        </div>
        <div className="text-right font-mono text-[13px] font-bold text-slate-900">{dinero(o.total)}</div>
        <div className="flex min-w-0 items-center gap-2 text-[11.5px] text-slate-500">
          <span className="whitespace-nowrap">
            <span className="text-slate-400">compra </span>
            <span className="font-mono font-bold text-slate-700">{fecha(o.venta_at)}</span>
          </span>
          <span className="text-slate-300">→</span>
          <span className="whitespace-nowrap">
            <span className="text-slate-400">proceso </span>
            <span className="font-mono font-bold text-slate-700">{fecha(o.creado_at)}</span>
          </span>
          {rz && (
            <span className="shrink-0 rounded-full px-[7px] py-[2px] text-[10px] font-extrabold"
                  style={{ background: "#E0F2FE", color: "#0369A1" }}>
              {rz}
            </span>
          )}
        </div>
        <div className="flex items-center justify-end gap-[6px] text-[11.5px] text-slate-500">
          <Package className="h-[13px] w-[13px] text-slate-300" />
          {piezas}
        </div>
        <div className="text-slate-300"><Chevron className="h-4 w-4" /></div>
      </div>
      )}

      {/* ── ANGOSTO: el mismo renglón apilado. La barra de familia sobrevive a
             la pérdida de columnas — es el recurso que no se puede perder. ── */}
      <div onClick={onAbrir} className="grid cursor-pointer gap-3 lg:hidden"
           style={{ gridTemplateColumns: "4px 1fr" }}>
        <span className="h-full" style={{ background: s.marca || "transparent" }} />
        <div className="py-3 pr-[14px]">
          <div className="flex items-center gap-2">
            <span className="h-[7px] w-[7px] shrink-0 rounded-full" style={{ background: s.punto }} />
            <span className="text-[13.5px]" style={{ fontWeight: s.peso, color: s.color }}>{d.txt}</span>
            <span className="ml-auto font-mono text-[13px] font-bold"
                  style={{ color: o.odoo_name ? "#0f172a" : "#cbd5e1" }}>
              {o.odoo_name ?? "—"}
            </span>
            <Chevron className="h-[15px] w-[15px] shrink-0 text-slate-300" />
          </div>
          {o.motivo && (
            <div className="mt-1 pl-[15px] text-[11.5px]" style={{ color: s.motivoColor }}>{o.motivo}</div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2 pl-[15px] text-[11.5px] text-slate-500">
            {o.guia && <span className="font-mono font-bold text-slate-700">{o.guia}</span>}
            {o.guia && <span className="text-slate-300">·</span>}
            <span>{o.almacen ?? "—"}</span>
            <span className="text-slate-300">·</span>
            <span className="font-mono font-bold text-slate-900">{dinero(o.total)}</span>
            <span className="text-slate-300">·</span>
            <span>{piezas} renglones</span>
          </div>
          <div className="mt-[5px] flex flex-wrap items-center gap-[10px] pl-[15px] text-[11px] text-slate-400">
            <span>compra <span className="font-mono font-bold text-slate-600">{fecha(o.venta_at)}</span></span>
            <span>proceso <span className="font-mono font-bold text-slate-600">{fecha(o.creado_at)}</span></span>
            {rz && (
              <span className="rounded-full px-[7px] py-[1px] font-extrabold"
                    style={{ background: "#E0F2FE", color: "#0369A1" }}>{rz}</span>
            )}
          </div>
        </div>
      </div>

      {/* ── Las dos entregas de un surtido dividido, con su conector ── */}
      {dividido && (
        <div className="pb-3 pl-[34px] pr-5">
          {partes(o).map((p, i) => (
            <div key={p.nombre}
                 className="grid items-center gap-3 py-[7px]"
                 style={{
                   gridTemplateColumns: "14px 92px 116px 1fr",
                   borderTop: i ? "1px dashed #e0e5f2" : undefined,
                 }}>
              <span className="h-px" style={{ background: "#c7d2fe" }} />
              <span className="font-mono text-[12.5px] font-bold" style={{ color: "#4F46E5" }}>{p.nombre}</span>
              <span className="text-[12px] font-semibold text-slate-600">{p.almacen}</span>
              <span className="text-[11.5px] text-slate-500">
                entrega {i + 1} de {partes(o).length}
              </span>
            </div>
          ))}
        </div>
      )}

      {abierta && <Detalle o={o} odooUrl={odooUrl} />}
    </div>
  );
}

/* ── El detalle, in-situ ────────────────────────────────────────────────── */

function Detalle({ o, odooUrl }: { o: OrdenOdoo; odooUrl: string }) {
  const [copiada, setCopiada] = useState(false);
  const piezas = o.lineas.reduce((n, l) => n + (l.cantidad ?? 0), 0);
  const rz = rezago(o.venta_at, o.creado_at);
  const sinStock = o.lineas.filter(
    (l) => l.stock_libre && ALMACENES.every(([id]) => (l.stock_libre?.[id] ?? 0) <= 0),
  );

  const copiar = () => {
    if (!o.guia) return;
    void navigator.clipboard.writeText(o.guia).then(() => {
      setCopiada(true);
      setTimeout(() => setCopiada(false), 2000);
    });
  };

  return (
    <div className="grid gap-0 border-t xl:grid-cols-[1fr_300px]" style={{ borderColor: "#eef1f6" }}>
      {/* ── Renglones ── */}
      <div className="min-w-0 p-5">
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div>
            <div className="text-[13px] font-bold text-slate-900">Renglones de la venta</div>
            <div className="text-[11.5px] text-slate-400">
              {o.lineas.length} SKUs · {piezas} unidades
            </div>
          </div>
          {/* EL AVISO NO ES DECORATIVO. Sin él, alguien compara este número
              contra el stock de hoy y concluye que el sistema miente. */}
          <span className="ml-auto inline-flex items-center gap-[6px] rounded-[10px] px-[10px] py-[6px] text-[11.5px]"
                style={{ background: "#EEF0FF", color: "#4338CA" }}>
            <Camera className="h-[13px] w-[13px] shrink-0" />
            <span>
              <strong className="font-bold">Stock al momento de la venta.</strong>{" "}
              Foto congelada del {fecha(o.creado_at)} — el inventario de hoy ya es otro.
            </span>
          </span>
        </div>

        <div className="overflow-x-auto rounded-[14px] border" style={{ borderColor: "#eef1f6" }}>
          <table className="w-full min-w-[560px] text-sm">
            <thead>
              <tr className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400"
                  style={{ background: "#fbfcfe" }}>
                <th className="px-3 py-2 text-left">Producto</th>
                <th className="px-3 py-2 text-right">Unidades</th>
                <th className="px-3 py-2 text-right">P. unitario</th>
                {ALMACENES.map(([id, nombre]) => (
                  <th key={id} className="px-3 py-2 text-right whitespace-nowrap">Stock {nombre}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {o.lineas.map((l, i) => (
                <tr key={`${l.sku}-${i}`} className="border-t" style={{ borderColor: "#f4f6fa" }}>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-3">
                      {l.imagen ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={l.imagen} alt="" className="h-11 w-11 shrink-0 rounded-[10px] border object-cover"
                             style={{ borderColor: "#eef1f6" }} />
                      ) : (
                        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[10px] border"
                             style={{ borderColor: "#eef1f6", background: "#f8fafc" }}>
                          <ImageIcon className="h-4 w-4 text-slate-300" />
                        </div>
                      )}
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-semibold text-slate-800">
                          {l.titulo ?? "(sin título)"}
                        </div>
                        <div className="font-mono text-[11.5px] font-bold text-slate-500">{l.sku}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-[13px] font-bold text-slate-900">
                    {l.cantidad}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-[13px] text-slate-700">
                    {dinero(l.precio_unitario)}
                  </td>
                  {ALMACENES.map(([id]) => {
                    const n = l.stock_libre?.[id];
                    // NUNCA se suman los almacenes: la orden se surte de UNO,
                    // y un total de 3 puede ser 3+0 (surte) o 2+1 (no surte).
                    const tono = n === undefined || n === null
                      ? { bg: "transparent", fg: "#cbd5e1" }
                      : n <= 0 ? { bg: "#FFF1F2", fg: "#9F1239" }
                      : n < (l.cantidad ?? 1) ? { bg: "#FFFBEB", fg: "#B45309" }
                      : { bg: "#ECFDF5", fg: "#047857" };
                    return (
                      <td key={id} className="px-3 py-2.5 text-right">
                        <span className="inline-block min-w-[38px] rounded-full px-2 py-[3px] font-mono text-[12.5px] font-bold"
                              style={{ background: tono.bg, color: tono.fg }}>
                          {n ?? "sin medir"}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {sinStock.length > 0 && (
          <p className="mt-3 flex items-start gap-2 rounded-[12px] px-3 py-2.5 text-[12px]"
             style={{ background: "#FFFBEB", color: "#92400E" }}>
            <AlertTriangle className="mt-[1px] h-4 w-4 shrink-0" />
            <span>
              <strong className="font-bold">
                {sinStock.map((l) => l.sku).join(", ")} se vendió con 0 en ambos almacenes.
              </strong>{" "}
              La orden existe y el cliente ya pagó: hay que surtir de otra bodega, reponer
              o cancelar con el marketplace.
            </span>
          </p>
        )}
      </div>

      {/* ── Cronología, datos y acciones ── */}
      <div className="border-t p-5 xl:border-l xl:border-t-0"
           style={{ borderColor: "#eef1f6", background: "#fbfcfe" }}>
        <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">Cronología</div>
        <div className="mt-2 space-y-2 text-[12px]">
          <div>
            <div className="text-slate-400">El cliente compró</div>
            <div className="font-mono font-bold text-slate-700">{fecha(o.venta_at)}</div>
          </div>
          <div>
            <div className="text-slate-400">Nosotros la procesamos</div>
            <div className="font-mono font-bold text-slate-700">{fecha(o.creado_at)}</div>
          </div>
          {rz && (
            <span className="inline-flex items-center gap-[5px] rounded-full px-[8px] py-[2px] text-[11px] font-bold"
                  style={{ background: "#E0F2FE", color: "#0369A1" }}>
              <Clock className="h-3 w-3" />{rz}
            </span>
          )}
        </div>

        <div className="mt-4 text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">
          Datos de la orden
        </div>
        <dl className="mt-2 space-y-1.5 text-[12px]">
          {[
            ["Canal", o.canal === "temu" ? "Temu" : "TikTok Shop"],
            ["Almacén", o.almacen ?? "—"],
            ["Paquetería", o.paqueteria ?? "—"],
            ["Estado en Odoo", o.estado ?? "—"],
            ["Venta", o.external_order_id],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3">
              <dt className="text-slate-400">{k}</dt>
              <dd className="truncate text-right font-semibold text-slate-700">{v}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-4 space-y-2">
          {o.odoo_order_id && odooUrl && (
            <a
              href={`${odooUrl}/odoo/sales/${o.odoo_order_id}`}
              target="_blank"
              rel="noreferrer"
              className="flex w-full items-center justify-center gap-2 rounded-[10px] px-3 py-2.5 text-[13px] font-bold text-white"
              style={{ background: "#4F46E5" }}
            >
              <ExternalLink className="h-4 w-4" />
              Abrir {o.odoo_name} en Odoo
            </a>
          )}
          {o.guia && (
            <button
              type="button"
              onClick={copiar}
              className="flex w-full items-center justify-center gap-2 rounded-[10px] border bg-white px-3 py-2.5 text-[13px] font-semibold text-slate-600 hover:border-indigo-200 hover:text-indigo-600"
              style={{ borderColor: "#e6e9f2" }}
            >
              {copiada ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <Copy className="h-4 w-4" />}
              {copiada ? "Guía copiada" : "Copiar guía"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── La tarjeta de un canal ─────────────────────────────────────────────── */

function TarjetaCanal({
  canal, ordenes, encendido, escalonId, moviendo, abierta, onAbrir, onSwitch, odooUrl, filtrando,
}: {
  canal: (typeof CANALES)[number];
  ordenes: OrdenOdoo[];
  encendido: boolean;
  escalonId: Estado["odoo_ventas"]["escalon_id"];
  moviendo: boolean;
  abierta: string | null;
  onAbrir: (id: string | null) => void;
  onSwitch: () => void;
  odooUrl: string;
  filtrando: boolean;
}) {
  const ultima = ordenes[0]?.creado_at ?? null;
  const conOrden = ordenes.filter((o) => o.odoo_name).length;
  // "En observación" no es lo mismo que "encendido": el canal puede estar
  // encendido y el escalón medir sin escribir. Decir sólo "encendido" haría
  // esperar órdenes que no van a existir.
  const observando = encendido && escalonId === "observando";
  const rotulo = observando ? "Canal en observación" : encendido ? "Canal encendido" : "Canal apagado";
  const rotuloColor = observando ? "#0369A1" : encendido ? "#047857" : "#94a3b8";

  return (
    <div className="overflow-hidden rounded-[18px] border bg-white"
         style={{ borderColor: "#d9dcec", boxShadow: "0 1px 2px rgba(16,24,40,.04)" }}>
      <div className="flex flex-wrap items-center gap-[14px] border-b px-5 py-[13px]"
           style={{ borderColor: "#eef1f6", background: canal.suave }}>
        <span className="flex h-7 w-7 items-center justify-center rounded-[9px] text-[12px] font-extrabold"
              style={{ background: canal.base, color: canal.id === "tiktok" ? canal.punto : "#fff" }}>
          {canal.mono}
        </span>
        <div>
          <div className="text-[14.5px] font-extrabold" style={{ color: canal.tinta }}>{canal.nombre}</div>
          <div className="text-[11.5px]" style={{ color: canal.tintaSuave }}>
            {ordenes.length} {ordenes.length === 1 ? "venta procesada" : "ventas procesadas"}
            {ultima && ` · última ${haceCuanto(ultima)}`}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-[12px] font-bold" style={{ color: rotuloColor }}>{rotulo}</span>
          <Switch activo={encendido} ocupado={moviendo} ancho={40}
                  etiqueta={`interruptor de ${canal.nombre}`} onClick={onSwitch} />
        </div>
      </div>

      {ordenes.length > 0 && (
        <div className="hidden gap-3 border-b py-[9px] pr-5 text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400 lg:grid"
             style={{ gridTemplateColumns: GRID, borderColor: "#eef1f6" }}>
          <span /><span>Desenlace</span><span>Orden Odoo</span><span>Almacén</span>
          <span>Guía</span><span className="text-right">Total</span><span>Compra · proceso</span>
          <span className="text-right">Renglones</span><span />
        </div>
      )}

      {ordenes.map((o) => (
        <FilaOrden
          key={`${o.canal}-${o.external_order_id}`}
          o={o}
          odooUrl={odooUrl}
          abierta={abierta === o.external_order_id}
          onAbrir={() => onAbrir(abierta === o.external_order_id ? null : o.external_order_id)}
        />
      ))}

      {ordenes.length === 0 && (
        <p className="px-5 py-8 text-center text-[12.5px] text-slate-400">
          {filtrando
            ? `${canal.nombre} no tiene nada pendiente.`
            : `Todavía no ha entrado ninguna venta de ${canal.nombre}.`}
        </p>
      )}

      {ordenes.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 text-[12px] text-slate-400">
          <span>{ordenes.length} ventas · {conOrden} órdenes en Odoo</span>
          <span className="inline-flex items-center gap-[6px]">
            <MousePointerClick className="h-[13px] w-[13px]" />
            Clic en un renglón abre el detalle con el stock del momento
          </span>
        </div>
      )}
    </div>
  );
}

/* ── Encendido y sin ventas ─────────────────────────────────────────────── */

function SinVentas({ estado }: { estado: Estado }) {
  const ov = estado.odoo_ventas;
  const tk = estado.publicaciones?.tiktok;
  const activas = tk?.activas;
  return (
    <div className="rounded-[18px] border bg-white p-6"
         style={{ borderColor: "#d9dcec", boxShadow: "0 1px 2px rgba(16,24,40,.04)" }}>
      <div className="flex items-start gap-4">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px]"
              style={{ background: "#ECFDF5", color: "#047857" }}>
          <Radio className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <h2 className="text-[19px] font-extrabold tracking-[-.01em] text-slate-900">
            Todo en orden. Aún no entra ninguna venta.
          </h2>
          <p className="mt-1 text-[13px] text-slate-500">
            Cuando TikTok o Temu reporten una compra, la orden aparecerá aquí sola —
            no hay nada que hacer.
          </p>
        </div>
      </div>

      <div className="mt-4 space-y-2">
        {[
          `Automatización encendida en ${ov.escalon}`,
          `Canales activos: ${ov.canales.length ? ov.canales.join(" · ") : "ninguno"}`,
        ].map((t) => (
          <div key={t} className="flex items-center gap-2 rounded-[12px] px-3 py-2.5 text-[13px] text-slate-600"
               style={{ background: "#f8fafc" }}>
            <CheckCircle2 className="h-4 w-4 shrink-0" style={{ color: "#10b981" }} />
            {t}
          </div>
        ))}
        {/* LA CAUSA REAL, y no es un fallo del automatismo: sin publicaciones
            vivas no hay ventas que convertir. Decirlo evita que un cero se lea
            como "está roto". */}
        {typeof activas === "number" && tk && activas < tk.total && (
          <div className="flex flex-wrap items-center gap-2 rounded-[12px] px-3 py-2.5 text-[13px]"
               style={{ background: "#FFFBEB", color: "#92400E" }}>
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              Sólo <strong className="font-bold">{activas} publicación{activas === 1 ? "" : "es"} activa
              {activas === 1 ? "" : "s"} de {tk.total.toLocaleString("es-MX")}</strong> en TikTok Shop.
              Sin publicaciones no hay ventas que automatizar.
            </span>
          </div>
        )}
      </div>

      <p className="mt-4 border-t pt-3 text-[12px] text-slate-400" style={{ borderColor: "#eef1f6" }}>
        Esta pantalla no necesita que la vigiles: si algo se rompe, la cifra de errores
        deja de ser 0 y el renglón aparece con barra roja.
      </p>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   LA PANTALLA
   ══════════════════════════════════════════════════════════════════════════ */

export default function AutomatizacionPage() {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [ordenes, setOrdenes] = useState<OrdenOdoo[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [canal, setCanal] = useState<CanalId>("tiktok");
  const [soloAccion, setSoloAccion] = useState(false);
  const [dias, setDias] = useState(30);
  const [abierta, setAbierta] = useState<string | null>(null);
  const [verParams, setVerParams] = useState(false);

  const [confirmar, setConfirmar] = useState<
    { que: "general" | CanalId; encender: boolean } | null
  >(null);
  const [moviendo, setMoviendo] = useState(false);
  const [motivo, setMotivo] = useState("");

  /* LA CARGA TRAE LOS DOS CANALES, no sólo el visible. El contador del filtro
     tiene que sumar ambos: si el error está en Temu y estás viendo TikTok, sin
     eso no te enteras nunca. El recorte por canal se hace aquí, en memoria —
     son decenas de filas, no miles. */
  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const [e, o] = await Promise.all([
        fetchSesion(`${API_BASE}/api/automatizacion/estado`),
        fetchSesion(`${API_BASE}/api/automatizacion/ordenes-odoo?limite=400&dias=${dias}`),
      ]);
      if (!e.ok || !o.ok) throw new Error(`HTTP ${e.status} / ${o.status}`);
      setEstado(await e.json());
      setOrdenes((await o.json()).ordenes ?? []);
      // Se limpia AQUÍ, no sólo al empezar: con dos cargas en vuelo, la que
      // falla dejaba su banner rojo encima de los datos que la otra sí trajo.
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "no se pudo cargar");
    } finally {
      setCargando(false);
    }
  }, [dias]);

  useEffect(() => { void cargar(); }, [cargar]);

  const mover = useCallback(async (encendido: boolean, porque = "", cual?: string) => {
    setMoviendo(true);
    try {
      const url = `${API_BASE}/api/automatizacion/interruptor?encendido=${encendido}` +
        (porque ? `&motivo=${encodeURIComponent(porque)}` : "") +
        (cual ? `&canal=${encodeURIComponent(cual)}` : "");
      const r = await fetchSesion(url, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      if (!j.ok) throw new Error(j.motivo ?? "no se pudo mover el interruptor");
      setConfirmar(null);
      setMotivo("");
      await cargar();
    } catch (err) {
      setError(err instanceof Error ? err.message : "no se pudo mover el interruptor");
    } finally {
      setMoviendo(false);
    }
  }, [cargar]);

  const ov = estado?.odoo_ventas;
  const porCanal = useMemo(() => {
    const base = { tiktok: [] as OrdenOdoo[], temu: [] as OrdenOdoo[] };
    for (const o of ordenes) {
      if (o.canal === "tiktok" || o.canal === "temu") base[o.canal].push(o);
    }
    return base;
  }, [ordenes]);

  const pendientes = useMemo(() => ({
    tiktok: porCanal.tiktok.filter(pideAccion).length,
    temu: porCanal.temu.filter(pideAccion).length,
  }), [porCanal]);
  const pendientesTotal = pendientes.tiktok + pendientes.temu;

  const visibles = useMemo(() => {
    const l = porCanal[canal];
    return soloAccion ? l.filter(pideAccion) : l;
  }, [porCanal, canal, soloAccion]);

  const otro = CANALES.find((c) => c.id !== canal)!;
  const canalInfo = CANALES.find((c) => c.id === canal)!;
  const canalEncendido = (id: string) =>
    Boolean(ov?.canales_estado?.[id]?.encendido ?? ov?.canales?.includes(id));

  const sinNada = !cargando && ordenes.length === 0 && Boolean(ov?.encendido);

  return (
    <div className="min-h-screen" style={{ background: "#F6F7FB" }}>
      <AppNavbar />
      <main className="mx-auto max-w-[1400px] px-4 py-6">

        {/* ── CONTROL MAESTRO ── */}
        {ov && (
          <div className="rounded-[18px] border bg-white"
               style={{ borderColor: "#d9dcec", boxShadow: "0 1px 2px rgba(16,24,40,.04)" }}>
            <div className="flex flex-wrap items-stretch">
              <div className="flex flex-1 flex-wrap items-center gap-[18px] px-[22px] py-[18px]">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px]"
                      style={{
                        background: ov.encendido ? "#ECFDF5" : "#F1F5F9",
                        color: ov.encendido ? "#047857" : "#94a3b8",
                      }}>
                  {ov.encendido ? <CheckCircle2 className="h-[21px] w-[21px]" />
                                : <Power className="h-[21px] w-[21px]" />}
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-[10px]">
                    <span className="text-[17px] font-extrabold tracking-[-.01em] text-slate-900">
                      {ov.encendido ? "Automatización activa" : "Automatización apagada"}
                    </span>
                    <span className="inline-flex items-center gap-[5px] rounded-full px-[9px] py-[3px] text-[11px] font-extrabold"
                          style={{
                            background: ov.encendido ? "#ECFDF5" : "#F1F5F9",
                            color: ov.encendido ? "#047857" : "#64748B",
                          }}>
                      <span className="h-[6px] w-[6px] rounded-full"
                            style={{ background: ov.encendido ? "#10b981" : "#94a3b8" }} />
                      {ov.escalon}
                    </span>
                  </div>
                  <div className="mt-1 text-[13px] text-slate-500">
                    {ov.encendido
                      ? "Cada venta se vuelve orden de venta en Odoo, sola."
                      : "Las ventas siguen llegando y se registran, pero nadie crea la orden."}
                    {ov.actualizado_por && (
                      <span className="text-slate-400">
                        {" "}{ov.encendido ? "Encendida" : "Apagada"} por {ov.actualizado_por}.
                      </span>
                    )}
                  </div>
                </div>
                <div className="ml-auto hidden md:block"><Escalon id={ov.escalon_id} /></div>
              </div>
              <div className="flex shrink-0 items-center gap-[14px] border-t px-[22px] py-[18px] md:border-l md:border-t-0"
                   style={{ borderColor: "#eef1f6" }}>
                {ov.encendido && (
                  <button
                    type="button"
                    onClick={() => setConfirmar({ que: "general", encender: false })}
                    className="inline-flex items-center gap-[7px] rounded-[10px] border bg-white px-[14px] py-[9px] text-[13px] font-bold"
                    style={{ borderColor: "#FECDD3", color: "#9F1239" }}
                  >
                    <Power className="h-[15px] w-[15px]" />Apagar todo
                  </button>
                )}
                <Switch
                  activo={ov.encendido}
                  ocupado={moviendo}
                  etiqueta="interruptor general"
                  onClick={() => setConfirmar({ que: "general", encender: !ov.encendido })}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 border-t sm:grid-cols-3" style={{ borderColor: "#eef1f6" }}>
              <Kpi rotulo={`Órdenes creadas · ${dias} d`} valor={estado!.resumen.total_30d}
                   pie={`de ${ordenes.length} ventas`} />
              <Kpi rotulo="Sin respaldo de inventario" valor={estado!.resumen.parciales}
                   pie="se crearon sin stock" tono={estado!.resumen.parciales ? "ambar" : undefined} />
              <Kpi rotulo="Con error" valor={estado!.resumen.errores}
                   pie="nadie las capturó" tono={estado!.resumen.errores ? "rojo" : undefined} />
            </div>
          </div>
        )}

        {error && (
          <p className="mt-4 flex items-center gap-2 rounded-[12px] px-3 py-2.5 text-[13px]"
             style={{ background: "#FFF1F2", color: "#9F1239" }}>
            <AlertTriangle className="h-4 w-4 shrink-0" />{error}
          </p>
        )}

        {/* ── CANALES Y FILTROS ── */}
        <div className="mt-5 flex flex-wrap items-center gap-[10px]">
          {CANALES.map((c) => {
            const sel = c.id === canal;
            const n = soloAccion ? pendientes[c.id] : porCanal[c.id].length;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => { setCanal(c.id); setAbierta(null); }}
                className="inline-flex items-center gap-[9px] rounded-full border px-4 py-2 text-[13.5px] font-bold transition-all"
                style={{
                  background: sel ? c.base : "#fff",
                  borderColor: sel ? c.base : c.borde,
                  color: sel ? "#fff" : "#374151",
                }}
              >
                <span className="h-[9px] w-[9px] rounded-full" style={{ background: c.punto }} />
                {c.nombre}
                <span className="rounded-full px-2 py-[2px] font-mono text-[11px] font-bold"
                      style={{
                        background: sel ? "rgba(255,255,255,.16)" : c.suave,
                        color: sel ? "#fff" : c.tinta,
                      }}>
                  {n}
                </span>
              </button>
            );
          })}

          <div className="ml-auto flex flex-wrap items-center gap-[10px]">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-[10px] border px-[13px] py-2 text-[13px] font-bold"
                   style={{ borderColor: "#FDE68A", background: "#FFFBEB", color: "#92400E" }}>
              <input type="checkbox" checked={soloAccion} style={{ accentColor: "#B45309" }}
                     onChange={(e) => { setSoloAccion(e.target.checked); setAbierta(null); }} />
              Sólo lo que requiere acción
              <span className="rounded-full px-[7px] font-mono text-[11px]" style={{ background: "#FDE68A" }}>
                {pendientesTotal}
              </span>
            </label>
            <select
              value={dias}
              onChange={(e) => setDias(Number(e.target.value))}
              className="rounded-[10px] border bg-white px-3 py-[9px] text-[13px] font-semibold text-slate-600"
              style={{ borderColor: "#e6e9f2" }}
            >
              <option value={1}>Procesadas: últimas 24 h</option>
              <option value={7}>Procesadas: últimos 7 días</option>
              <option value={30}>Procesadas: últimos 30 días</option>
              <option value={365}>Procesadas: todo</option>
            </select>
            <button
              type="button"
              onClick={() => void cargar()}
              disabled={cargando}
              aria-label="Actualizar"
              className="inline-flex items-center justify-center rounded-[10px] border bg-white p-[9px] text-slate-500 hover:text-indigo-600 disabled:opacity-50"
              style={{ borderColor: "#e6e9f2" }}
            >
              <RotateCw className={`h-[15px] w-[15px] ${cargando ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>

        {/* EL PUENTE ENTRE CANALES. Con listas separadas, un error en Temu no se
            ve mientras miras TikTok. El contador de la casilla suma los dos,
            y esta línea dice dónde está lo que no estás viendo. */}
        {pendientes[otro.id] > 0 && (
          <button
            type="button"
            onClick={() => { setCanal(otro.id); setSoloAccion(true); setAbierta(null); }}
            className="mt-3 inline-flex items-center gap-2 rounded-[10px] px-3 py-2 text-[12.5px] font-semibold"
            style={{ background: "#FFFBEB", color: "#92400E" }}
          >
            <AlertTriangle className="h-[14px] w-[14px]" />
            {otro.nombre} tiene {pendientes[otro.id]} que requiere
            {pendientes[otro.id] === 1 ? "" : "n"} acción
            <ChevronRight className="h-[14px] w-[14px]" />
          </button>
        )}

        {/* ── LA LISTA ── */}
        <div className="mt-[14px]">
          {cargando && !estado ? (
            <div className="overflow-hidden rounded-[18px] border bg-white"
                 style={{ borderColor: "#d9dcec" }}>
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="grid gap-3 border-b px-5 py-4" style={{ borderColor: "#f4f6fa" }}>
                  <div className="h-3 w-1/3 animate-pulse rounded-full bg-slate-100" />
                  <div className="h-3 w-2/3 animate-pulse rounded-full bg-slate-50" />
                </div>
              ))}
            </div>
          ) : sinNada ? (
            <SinVentas estado={estado!} />
          ) : (
            <TarjetaCanal
              canal={canalInfo}
              ordenes={visibles}
              encendido={canalEncendido(canal)}
              escalonId={ov?.escalon_id ?? "apagado"}
              moviendo={moviendo}
              abierta={abierta}
              onAbrir={setAbierta}
              odooUrl={ov?.odoo_url ?? ""}
              filtrando={soloAccion}
              onSwitch={() => setConfirmar({ que: canal, encender: !canalEncendido(canal) })}
            />
          )}
        </div>

        {/* ── REFERENCIA TÉCNICA ── */}
        <div className="mt-4 rounded-[14px] border" style={{ borderColor: "#e6e9f2", background: "#fbfcfe" }}>
          <button
            type="button"
            onClick={() => setVerParams((v) => !v)}
            className="flex w-full items-center gap-[10px] px-[18px] py-[13px] text-left"
          >
            {verParams ? <ChevronDown className="h-[15px] w-[15px] text-slate-400" />
                       : <ChevronRight className="h-[15px] w-[15px] text-slate-400" />}
            <span className="text-[13px] font-bold text-slate-600">
              Referencia técnica · los 9 campos que viajan a Odoo
            </span>
            <span className="text-[12px] text-slate-400">Sólo lectura · para depurar</span>
            <Braces className="ml-auto h-[15px] w-[15px] text-slate-300" />
          </button>
          {verParams && (
            <div className="grid gap-x-8 gap-y-3 border-t px-[18px] py-4 md:grid-cols-2"
                 style={{ borderColor: "#eef1f6" }}>
              {PARAMETROS.map((p) => (
                <div key={p.campo}>
                  <div className="flex flex-wrap items-baseline gap-2">
                    <code className="font-mono text-[12px] font-bold" style={{ color: "#4F46E5" }}>
                      {p.campo}
                    </code>
                    <span className="text-[12.5px] font-semibold text-slate-700">{p.valor}</span>
                  </div>
                  <div className="text-[11.5px] text-slate-400">{p.fuente}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>

      {/* ── CONFIRMACIÓN ──
          La asimetría es a propósito: apagar explica QUÉ deja de pasar y quién
          se queda con el trabajo; encender explica qué empieza a escribirse en
          Odoo. Un diálogo que sólo dice "¿seguro?" se responde en automático y
          no informa nada. */}
      {confirmar && ov && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
             onClick={() => !moviendo && setConfirmar(null)}>
          <div className="w-full max-w-lg rounded-[18px] bg-white p-6 shadow-2xl"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px]"
                    style={{
                      background: confirmar.encender ? "#EEF0FF" : "#FFF1F2",
                      color: confirmar.encender ? "#4338CA" : "#9F1239",
                    }}>
                {confirmar.encender ? <CheckCircle2 className="h-5 w-5" /> : <Power className="h-5 w-5" />}
              </span>
              <div className="min-w-0">
                <h3 className="text-[16px] font-extrabold text-slate-900">
                  {confirmar.encender ? "Encender" : "Apagar"}
                  {confirmar.que === "general"
                    ? " la automatización"
                    : ` ${CANALES.find((c) => c.id === confirmar.que)!.nombre}`}
                </h3>
                <p className="mt-1.5 text-[13px] leading-relaxed text-slate-600">
                  {confirmar.encender ? (
                    <>
                      A partir de ahora cada venta{confirmar.que !== "general" && " de este canal"}{" "}
                      <strong className="font-bold">escribe en Odoo</strong> en el escalón{" "}
                      <em>{ov.escalon}</em>. Las ventas que entraron mientras estuvo apagada{" "}
                      <strong className="font-bold">no se procesan solas</strong>.
                    </>
                  ) : (
                    <>
                      Las ventas van a seguir llegando y registrándose, pero{" "}
                      <strong className="font-bold">nadie va a crear la orden en Odoo</strong>:
                      habrá que capturarlas a mano. Lo ya creado se queda como está.
                    </>
                  )}
                </p>
              </div>
              <button type="button" onClick={() => setConfirmar(null)}
                      className="ml-auto text-slate-300 hover:text-slate-500">
                <X className="h-5 w-5" />
              </button>
            </div>

            <input
              value={motivo}
              onChange={(e) => setMotivo(e.target.value)}
              placeholder="Motivo (opcional, queda en la bitácora)"
              className="mt-4 w-full rounded-[10px] border px-3 py-2 text-[13px]"
              style={{ borderColor: "#e6e9f2" }}
            />

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setConfirmar(null)} disabled={moviendo}
                      className="rounded-[10px] border bg-white px-4 py-2 text-[13px] font-semibold text-slate-600"
                      style={{ borderColor: "#e6e9f2" }}>
                Cancelar
              </button>
              <button
                type="button"
                disabled={moviendo}
                onClick={() => void mover(confirmar.encender, motivo,
                                          confirmar.que === "general" ? undefined : confirmar.que)}
                className="inline-flex items-center gap-2 rounded-[10px] px-4 py-2 text-[13px] font-bold text-white disabled:opacity-60"
                style={{ background: confirmar.encender ? "#4F46E5" : "#E11D48" }}
              >
                {moviendo && <Loader2 className="h-4 w-4 animate-spin" />}
                {confirmar.encender ? "Encender" : "Sí, apagar"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
