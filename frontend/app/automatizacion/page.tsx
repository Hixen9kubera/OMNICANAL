"use client";

/**
 * /automatizacion — Lo que el panel hace solo, y con qué inventario lo hizo.
 *
 * Hoy: las ÓRDENES DE VENTA que se crean en Odoo por cada venta de TikTok.
 * Hasta ahora eso se capturaba a mano y no había dónde ver si se hizo bien.
 *
 * LA COLUMNA QUE JUSTIFICA LA PANTALLA es "stock al momento de la venta". No se
 * puede pedir en vivo: `free_qty` ya cambió. Sale de una foto congelada al
 * crear la orden, y es el único dato con el que se contesta "¿por qué se
 * sobrevendió?" tres días después.
 *
 * EL INTERRUPTOR. Apagar es inmediato y seguro (lo creado se queda, no nace
 * nada nuevo); encender es lo que hay que pensar dos veces. Por eso la
 * advertencia al APAGAR no pregunta "¿seguro?" — explica qué deja de pasar y
 * quién se queda con el trabajo. Un diálogo que solo dice "¿seguro?" se
 * responde en automático y no informa nada.
 *
 * Solo admin: la orden trae la guía del comprador.
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ChevronDown, Loader2, PackageCheck, RefreshCw,
  Truck, Warehouse, X, XCircle,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";

/* Paleta de TikTok: negro, cian y magenta. Se usa para el acento del canal,
   no para el fondo — el resto del panel es claro y romperlo despistaría. */
const TT = { cian: "#25F4EE", rojo: "#FE2C55", negro: "#010101" };

/* El interruptor GENERAL es neutro a propósito: manda sobre los dos canales, y
   pintarlo con la marca de uno haría creer que solo aplica a ése. */
const NEUTRO = "#0F172A";

/** Un canal, con su marca. El acento se usa para el switch y el filo de sus
 *  tarjetas; el fondo del panel sigue siendo claro. */
const CANALES = [
  {
    id: "tiktok",
    nombre: "TikTok Shop",
    acento: TT.cian,
    tinta: TT.negro,
    // El rótulo de marca: TikTok parte su nombre en cian y magenta.
    marca: (
      <span className="rounded px-2 py-0.5 text-xs font-semibold text-white"
            style={{ background: TT.negro }}>
        Tik<span style={{ color: TT.cian }}>Tok</span>
        <span style={{ color: TT.rojo }}> Shop</span>
      </span>
    ),
  },
  {
    id: "temu",
    nombre: "Temu",
    acento: "#FB7701",          // el naranja de Temu
    tinta: "#E8590C",
    marca: (
      <span className="rounded px-2 py-0.5 text-xs font-semibold text-white"
            style={{ background: "#FB7701" }}>
        Temu
      </span>
    ),
  },
] as const;

interface Linea {
  sku: string;
  titulo: string | null;
  imagen: string | null;
  cantidad: number;
  precio_unitario: number | null;
  /** La foto del stock del momento, llaveada por ID de almacén — no por
   *  nombre: el nombre se puede renombrar en Odoo, el id no. */
  stock_libre: Record<string, number> | null;
}

/** Los almacenes que mira el automatismo, en su orden de preferencia. */
const ALMACENES: Array<[string, string]> = [
  ["135", "TEXCO"],
  ["150", "TEXCO II"],
];

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
  lineas: Linea[];
}

interface SondeoTemu {
  veredicto: string;
  resultados: Array<{
    grupo: string;
    endpoint: string;
    ok: boolean;
    codigo?: string | null;
    error?: string;
  }>;
  /** Solo si Temu dejó listar: cuántas órdenes declara y qué campos trae. */
  ordenes?: {
    endpoint: string;
    total_declarado: number | null;
    en_esta_pagina: number;
    campos_por_orden: string[];
    campos_del_renglon?: string[];
    campos_del_padre?: string[];
    /** ¿La guía viene dentro de la propia orden? */
    guia_en_la_orden: boolean;
    guia_con_valor: boolean;
    campos_de_guia: Array<{ campo: string; con_valor: boolean; valor: string | null }>;
  } | null;
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
    canales_estado?: Record<string, {
      encendido: boolean;
      persistido: boolean;
      actualizado_por: string | null;
      motivo: string | null;
    }>;
  };
  resumen: {
    total_30d: number;
    parciales: number;
    errores: number;
    nota?: string;
  };
}

const ESTADO_ODOO: Record<string, { txt: string; clase: string }> = {
  draft: { txt: "Borrador", clase: "bg-slate-100 text-slate-700" },
  sale: { txt: "Confirmada", clase: "bg-emerald-100 text-emerald-800" },
  cancel: { txt: "Cancelada", clase: "bg-rose-100 text-rose-800" },
};

/** Las acciones que NO dejaron una orden en Odoo. Se rotulan aparte porque
 *  confundir una simulación con una orden real es el peor error que puede
 *  cometer quien lee esta pantalla. */
const ACCION: Record<string, { txt: string; clase: string }> = {
  simulado: { txt: "Simulación", clase: "border border-dashed border-slate-400 text-slate-500" },
  nacio_cancelada: { txt: "Nació cancelada", clase: "bg-rose-50 text-rose-700" },
  sku_sin_producto: { txt: "SKU sin producto", clase: "bg-rose-100 text-rose-800" },
  apagado: { txt: "Apagado", clase: "bg-slate-100 text-slate-600" },
  canal_apagado: { txt: "Canal apagado", clase: "bg-slate-100 text-slate-600" },
  solo_registro: { txt: "Observando", clase: "bg-sky-50 text-sky-700" },
  error: { txt: "Error", clase: "bg-rose-100 text-rose-800" },
  no_se_pudo_cancelar: { txt: "No se pudo cancelar", clase: "bg-amber-100 text-amber-800" },
};

/** Los campos que viajan a Odoo en cada orden. Es la respuesta a "¿qué
 *  necesita el sistema para generar la orden de venta?" — y por eso dice de
 *  dónde sale cada uno, no solo cómo se llama. */
const PARAMETROS = [
  { campo: "partner_id", valor: "tiktokshop (1739238) · temu (1738206)",
    fuente: "fijo por canal — el mismo que ya usa Gaby. El comprador real NO entra a Odoo (es dato personal y quien envía es el marketplace)" },
  { campo: "warehouse_id", valor: "TEXCO (135) o TEXCO II (150)",
    fuente: "SE ELIGE leyendo free_qty por almacén: gana el primero que cubra la orden completa" },
  { campo: "client_order_ref", valor: "id de la orden del canal",
    fuente: "la llave de idempotencia — impide crear la misma venta dos veces" },
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

/** El interruptor. Uno solo, reusado por el maestro y por cada canal, para que
 *  todos se vean y se comporten igual; lo único que cambia es el color. */
function Switch({
  activo, color, etiqueta, ocupado, onClick,
}: {
  activo: boolean;
  color: string;
  etiqueta: string;
  ocupado: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={ocupado}
      role="switch"
      aria-checked={activo}
      aria-label={etiqueta}
      className="relative h-7 shrink-0 rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50"
      style={{ width: "3.25rem", background: activo ? color : "#cbd5e1" }}
    >
      <span
        className="absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-all"
        style={{ left: activo ? "calc(100% - 1.5rem)" : "0.25rem" }}
      />
    </button>
  );
}

export default function AutomatizacionPage() {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [ordenes, setOrdenes] = useState<OrdenOdoo[]>([]);
  const [cargando, setCargando] = useState(true);
  const [soloProblemas, setSoloProblemas] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verParams, setVerParams] = useState(false);
  // La pestaña activa. Cada canal tiene su propio interruptor, sus propias
  // órdenes y sus propios contadores: mezclarlos en una sola lista hacía
  // imposible contestar "¿cómo va TikTok?" sin leer canal por canal.
  const [canal, setCanal] = useState<string>("tiktok");
  const [temu, setTemu] = useState<SondeoTemu | null>(null);
  const [sondeando, setSondeando] = useState(false);
  const [confirmarApagado, setConfirmarApagado] = useState(false);
  const [confirmarCanal, setConfirmarCanal] = useState<string | null>(null);
  const [moviendo, setMoviendo] = useState(false);
  const [motivo, setMotivo] = useState("");

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const [e, o] = await Promise.all([
        fetchSesion(`${API_BASE}/api/automatizacion/estado?canal=${canal}`),
        fetchSesion(
          `${API_BASE}/api/automatizacion/ordenes-odoo?limite=200&canal=${canal}` +
            (soloProblemas ? "&solo_problemas=true" : ""),
        ),
      ]);
      if (!e.ok || !o.ok) throw new Error(`HTTP ${e.status} / ${o.status}`);
      setEstado(await e.json());
      setOrdenes((await o.json()).ordenes ?? []);
      // Limpiar el error AQUÍ, no solo al empezar. Con dos cargas en vuelo —lo
      // normal en desarrollo, y posible en producción con un fallo pasajero— la
      // que falla dejaba su banner rojo encima de los datos que la otra sí
      // trajo. Un error visible junto a datos correctos hace dudar de los datos.
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "no se pudo cargar");
    } finally {
      setCargando(false);
    }
  }, [soloProblemas, canal]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const sondearTemu = useCallback(async () => {
    setSondeando(true);
    try {
      const r = await fetchSesion(`${API_BASE}/api/automatizacion/temu/sondeo`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setTemu(await r.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "no se pudo sondear Temu");
    } finally {
      setSondeando(false);
    }
  }, []);

  const mover = useCallback(async (encendido: boolean, porque = "",
                                   canal?: string) => {
    setMoviendo(true);
    try {
      const url = `${API_BASE}/api/automatizacion/interruptor?encendido=${encendido}` +
        (porque ? `&motivo=${encodeURIComponent(porque)}` : "") +
        (canal ? `&canal=${encodeURIComponent(canal)}` : "");
      const r = await fetchSesion(url, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      if (!j.ok) throw new Error(j.motivo ?? "no se pudo mover el interruptor");
      setConfirmarApagado(false);
      setConfirmarCanal(null);
      setMotivo("");
      await cargar();
    } catch (err) {
      setError(err instanceof Error ? err.message : "no se pudo mover el interruptor");
    } finally {
      setMoviendo(false);
    }
  }, [cargar]);

  const ov = estado?.odoo_ventas;
  const on = !!ov?.encendido;

  return (
    <>
      <AppNavbar />
      <main className="mx-auto max-w-7xl px-4 py-6">
        {/* Acento de TikTok: la barra tricolor de la marca */}
        <div
          className="mb-5 h-1 w-full rounded-full"
          style={{ background: `linear-gradient(90deg, ${TT.cian}, ${TT.negro} 50%, ${TT.rojo})` }}
        />

        <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold">
              Automatización
              {CANALES.map((c) => (
                <span key={c.id}>{c.marca}</span>
              ))}
            </h1>
            <p className="text-sm text-slate-500">
              Cada venta se vuelve orden de venta en Odoo, sola. Cada canal se
              enciende por separado.
            </p>
          </div>
          <button
            onClick={() => void cargar()}
            className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-slate-50"
          >
            <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
            Actualizar
          </button>
        </header>

        {/* ── EL INTERRUPTOR ─────────────────────────────────────────────── */}
        {ov && (
          <section
            className="mb-6 overflow-hidden rounded-xl border"
            style={{ background: on ? NEUTRO : "#fff", borderColor: on ? NEUTRO : undefined }}
          >
            {/* EL MAESTRO. Neutro a propósito: manda sobre los dos canales, y
                pintarlo con la marca de uno haría creer que solo aplica a ése. */}
            <div className="flex flex-wrap items-center justify-between gap-4 p-4">
              <div className={on ? "text-white" : ""}>
                <div className="flex items-center gap-3">
                  <Switch
                    activo={on}
                    color="#34D399"
                    etiqueta="Generación automática de órdenes de venta en Odoo"
                    ocupado={moviendo}
                    onClick={() => (on ? setConfirmarApagado(true) : void mover(true))}
                  />
                  <span className="text-base font-semibold">
                    {on ? "Generando órdenes en Odoo" : "Generación apagada"}
                  </span>
                  {moviendo && <Loader2 className="h-4 w-4 animate-spin" />}
                </div>
                <p className={`mt-1 text-xs ${on ? "text-slate-300" : "text-slate-500"}`}>
                  {ov.escalon}
                  {ov.persistido && ov.actualizado_por && ` · último cambio: ${ov.actualizado_por}`}
                  {ov.motivo && ` — ${ov.motivo}`}
                </p>
              </div>
              <div className={`text-right text-sm ${on ? "text-slate-300" : "text-slate-500"}`}>
                <div>
                  Últimos 30 días: <b className={on ? "text-white" : "text-slate-800"}>
                    {estado.resumen.total_30d}
                  </b> órdenes
                </div>
                <div className="mt-0.5">
                  <b className="text-amber-500">{estado.resumen.parciales}</b> sin respaldo ·{" "}
                  <b style={{ color: TT.rojo }}>{estado.resumen.errores}</b> con error
                </div>
              </div>
            </div>

            {/* PESTAÑAS POR CANAL, al estilo de Productos y Omnicanal: la
                pastilla toma el color de la marca cuando está activa. Cada
                pestaña trae SUS órdenes, SU interruptor y SUS contadores.
                Mezclarlos hacía imposible contestar "¿cómo va TikTok?" sin
                leer canal por canal. */}
            <div className={`flex flex-wrap items-center gap-2 border-t px-4 py-3 ${
              on ? "border-slate-700" : ""}`}>
              {CANALES.map((c) => {
                const activa = c.id === canal;
                const ec = ov.canales_estado?.[c.id];
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => setCanal(c.id)}
                    aria-pressed={activa}
                    className={[
                      "flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold transition-all",
                      activa ? "scale-[1.02]" : "hover:-translate-y-0.5",
                    ].join(" ")}
                    style={
                      activa
                        ? { background: c.acento, color: c.id === "tiktok" ? "#000" : "#fff",
                            borderColor: c.acento, boxShadow: `0 6px 16px -6px ${c.acento}` }
                        : { borderColor: c.acento,
                            color: on ? "#E2E8F0" : "#374151",
                            background: on ? "transparent" : "#fff" }
                    }
                  >
                    {/* El punto dice si ESE canal está encendido, aunque no sea
                        la pestaña abierta: si no, había que entrar a cada una
                        para saberlo. */}
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ background: ec?.encendido ? "#22C55E" : "#94A3B8" }}
                      title={ec?.encendido ? "encendido" : "apagado"}
                    />
                    {c.nombre}
                  </button>
                );
              })}
            </div>

            {/* EL INTERRUPTOR DEL CANAL ABIERTO */}
            {(() => {
              const c = CANALES.find((x) => x.id === canal)!;
              const ec = ov.canales_estado?.[canal];
              const cOn = !!ec?.encendido;
              return (
                <div className={`flex flex-wrap items-center gap-3 border-t px-4 py-3 ${
                  on ? "border-slate-700" : ""}`}>
                  <Switch
                    activo={cOn}
                    color={c.acento}
                    etiqueta={`Órdenes de Odoo para ${c.nombre}`}
                    ocupado={moviendo}
                    onClick={() => (cOn ? setConfirmarCanal(c.id) : void mover(true, "", c.id))}
                  />
                  {c.marca}
                  <span className={`text-sm ${on ? "text-slate-200" : "text-slate-700"}`}>
                    {cOn ? "creando órdenes en Odoo" : "apagado"}
                  </span>
                  {!on && cOn && (
                    <span className="text-xs italic text-slate-400">
                      — el interruptor general está abajo, así que no corre
                    </span>
                  )}
                  <span className="ml-auto text-xs text-slate-400">
                    {ec?.persistido && ec.actualizado_por
                      ? `${ec.actualizado_por}${ec.motivo ? ` — ${ec.motivo}` : ""}`
                      : "por omisión"}
                  </span>
                </div>
              );
            })()}

            {estado.resumen.nota && (
              <p className="m-4 mt-0 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                {estado.resumen.nota} — falta aplicar la migración 0033 en kubera.
              </p>
            )}
          </section>
        )}

        {/* ── QUÉ NECESITA PARA ARMAR LA ORDEN ───────────────────────────── */}
        <section className="mb-6 rounded-xl border bg-white">
          <button
            onClick={() => setVerParams((v) => !v)}
            className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium hover:bg-slate-50"
          >
            Qué necesita para generar la orden de venta
            <ChevronDown className={`h-4 w-4 transition-transform ${verParams ? "rotate-180" : ""}`} />
          </button>
          {verParams && (
            <div className="overflow-x-auto border-t px-4 py-3">
              <table className="w-full min-w-[720px] text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-slate-400">
                    <th className="py-1 font-medium">Campo de Odoo</th>
                    <th className="py-1 font-medium">Valor</th>
                    <th className="py-1 font-medium">De dónde sale</th>
                  </tr>
                </thead>
                <tbody>
                  {PARAMETROS.map((p) => (
                    <tr key={p.campo} className="border-t align-top">
                      <td className="py-2 pr-3 font-mono text-xs" style={{ color: TT.rojo }}>
                        {p.campo}
                      </td>
                      <td className="py-2 pr-3 text-slate-700">{p.valor}</td>
                      <td className="py-2 text-xs text-slate-500">{p.fuente}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>


        {/* ¿Y Temu? La pregunta se contesta desde el servidor porque la lista
            blanca de Temu solo trae la IP de Railway: desde cualquier otra
            máquina toda llamada devuelve 5000003 y no se puede saber nada. */}
        {canal === "temu" && (
        <section className="mb-6 rounded-xl border bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">¿Se puede automatizar Temu?</h2>
              <p className="text-xs text-slate-500">
                Le pregunta a Temu si nos deja listar órdenes. Solo lectura.
              </p>
            </div>
            <button
              onClick={() => void sondearTemu()}
              disabled={sondeando}
              className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
            >
              {sondeando && <Loader2 className="h-4 w-4 animate-spin" />}
              Preguntar
            </button>
          </div>
          {temu && (
            <div className="mt-3 space-y-2">
              <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm font-medium">
                {temu.veredicto}
              </p>
              {temu.ordenes && (
                <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
                  Temu declara{" "}
                  <b>{temu.ordenes.total_declarado ?? "—"}</b> órdenes
                  {" "}(esta página trae {temu.ordenes.en_esta_pagina}) vía{" "}
                  <code className="font-mono text-xs">{temu.ordenes.endpoint}</code>.
                  {temu.ordenes.campos_del_renglon &&
                    temu.ordenes.campos_del_renglon.length > 0 && (
                      <span className="mt-1 block text-xs text-emerald-800">
                        Campos del renglón: {temu.ordenes.campos_del_renglon.join(", ")}
                      </span>
                    )}
                </p>
              )}
              {/* LA PREGUNTA DIRECTA, contestada sin que nadie lea listas de
                  campos: ¿viene la guía dentro de la orden? */}
              {temu.ordenes && (
                <p
                  className={`rounded-lg px-3 py-2 text-sm ${
                    temu.ordenes.guia_con_valor
                      ? "bg-emerald-50 text-emerald-900"
                      : temu.ordenes.guia_en_la_orden
                        ? "bg-amber-50 text-amber-900"
                        : "bg-rose-50 text-rose-900"
                  }`}
                >
                  <b>Guía:</b>{" "}
                  {temu.ordenes.guia_con_valor
                    ? "SÍ viene en la orden, y con valor."
                    : temu.ordenes.guia_en_la_orden
                      ? "el campo existe pero llega vacío en esta orden."
                      : "NO aparece dentro de la orden."}
                  {temu.ordenes.campos_de_guia?.length > 0 && (
                    <span className="mt-1 block font-mono text-xs">
                      {temu.ordenes.campos_de_guia
                        .map((g) => `${g.campo}${g.valor ? " = " + g.valor : " (vacío)"}`)
                        .join(" · ")}
                    </span>
                  )}
                </p>
              )}
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-xs">
                  <tbody>
                    {temu.resultados.map((r) => (
                      <tr key={r.endpoint} className="border-t">
                        <td className="py-1.5 pr-3 text-slate-400">{r.grupo}</td>
                        <td className="py-1.5 pr-3 font-mono">{r.endpoint}</td>
                        {/* El motivo va junto al veredicto: un "falla" a secas
                            no distingue "la IP está fuera" de "le faltó un
                            parámetro", y esa diferencia es todo el diagnóstico. */}
                        <td className="py-1.5">
                          {r.ok ? (
                            <span className="text-emerald-700">responde</span>
                          ) : (
                            <span className="text-rose-700">
                              {r.codigo ?? "falla"}
                              {r.error && (
                                <span className="ml-2 font-normal text-slate-400">
                                  {r.error}
                                </span>
                              )}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
        )}

        <label className="mb-4 flex w-fit items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={soloProblemas}
            onChange={(ev) => setSoloProblemas(ev.target.checked)}
          />
          Solo lo que necesita que alguien actúe
          <span className="text-slate-400">
            — sin crear, sin respaldo de inventario, o quedó viva tras cancelarse
          </span>
        </label>

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-800">
            <XCircle className="h-4 w-4" /> {error}
          </div>
        )}

        {!cargando && ordenes.length === 0 && !error && (
          <p className="rounded-xl border bg-white p-8 text-center text-sm text-slate-500">
            Todavía no hay órdenes registradas.
          </p>
        )}

        <div className="space-y-3">
          {ordenes.map((o) => {
            const simulado = o.accion === "simulado";
            // El estado de Odoo manda cuando la orden existe; si no existe,
            // lo que hay que rotular es POR QUÉ no existe.
            const est =
              ESTADO_ODOO[o.estado ?? ""] ??
              ACCION[o.accion] ?? { txt: o.accion, clase: "bg-slate-100 text-slate-700" };
            const parcial = o.cobertura === "parcial";
            return (
              <article
                key={`${o.canal}-${o.external_order_id}`}
                className={`overflow-hidden rounded-xl border bg-white ${
                  simulado ? "border-dashed opacity-90" : ""
                }`}
                style={parcial ? { borderColor: "#fcd34d" } : undefined}
              >
                <div
                  className="h-0.5 w-full"
                  style={{ background: simulado ? "#cbd5e1" : TT.cian }}
                />
                <div className="p-4">
                  <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                    <span className="font-mono text-base font-semibold">
                      {o.odoo_name ?? "— sin orden —"}
                    </span>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs ${est.clase}`}>
                      {est.txt}
                    </span>
                    {simulado && (
                      <span className="text-xs italic text-slate-400">
                        no se escribió en Odoo
                      </span>
                    )}
                    <span className="flex items-center gap-1 text-slate-500">
                      <Warehouse className="h-3.5 w-3.5" />
                      {o.almacen ?? "—"}
                    </span>

                    {/* LA GUÍA, con el acento del canal: es lo que busca quien empaca */}
                    {o.guia ? (
                      <span
                        className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold text-white"
                        style={{ background: TT.negro }}
                      >
                        <Truck className="h-3.5 w-3.5" style={{ color: TT.cian }} />
                        <span className="font-mono tracking-wide">{o.guia}</span>
                        {o.paqueteria && (
                          <span className="font-normal" style={{ color: TT.cian }}>
                            {o.paqueteria}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="text-xs text-slate-400">sin guía todavía</span>
                    )}

                    <span className="ml-auto text-xs text-slate-400">
                      venta {o.external_order_id} ·{" "}
                      {new Date(o.creado_at).toLocaleString("es-MX")}
                    </span>
                  </div>

                  {parcial && (
                    <p className="mb-3 flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      Ningún almacén cubría la orden completa: la reserva no va a
                      ocurrir y el stock no bajará solo.
                    </p>
                  )}
                  {o.motivo && (
                    <p className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800">
                      {o.motivo}
                    </p>
                  )}

                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[640px] text-sm">
                      <thead>
                        <tr className="text-left text-xs uppercase text-slate-400">
                          <th className="py-1 font-medium">Producto</th>
                          <th className="py-1 font-medium">SKU</th>
                          <th className="py-1 text-right font-medium">Unidades</th>
                          <th className="py-1 text-right font-medium">Precio</th>
                          <th className="py-1 text-right font-medium">
                            Stock al momento de la venta
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {o.lineas.map((l, i) => (
                          <tr key={`${l.sku}-${i}`} className="border-t">
                            <td className="py-2">
                              <div className="flex items-center gap-2">
                                {l.imagen ? (
                                  // eslint-disable-next-line @next/next/no-img-element
                                  <img
                                    src={l.imagen}
                                    alt={l.sku}
                                    className="h-10 w-10 rounded object-cover"
                                    loading="lazy"
                                  />
                                ) : (
                                  <span className="flex h-10 w-10 items-center justify-center rounded bg-slate-100">
                                    <PackageCheck className="h-4 w-4 text-slate-400" />
                                  </span>
                                )}
                                <span className="line-clamp-2 max-w-sm text-slate-700">
                                  {l.titulo ?? "—"}
                                </span>
                              </div>
                            </td>
                            <td className="py-2 font-mono text-xs">{l.sku}</td>
                            <td className="py-2 text-right font-semibold">{l.cantidad}</td>
                            <td className="py-2 text-right">
                              {l.precio_unitario != null
                                ? `$${Number(l.precio_unitario).toFixed(2)}`
                                : "—"}
                            </td>
                            <td className="py-2 text-right text-xs">
                              {!l.stock_libre ? (
                                <span className="text-slate-400">sin medir</span>
                              ) : (
                                <span className="text-slate-600">
                                  {ALMACENES.map(([id, nombre], j) => (
                                    <span key={id}>
                                      {j > 0 && " · "}
                                      {nombre} <b>{l.stock_libre?.[id] ?? 0}</b>
                                    </span>
                                  ))}
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {o.estado === "sale" && (
                    <p className="mt-2 flex items-center gap-1 text-xs text-emerald-700">
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      Confirmada: Odoo reservó la mercancía.
                    </p>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </main>

      {/* ── ADVERTENCIA AL APAGAR ─────────────────────────────────────────
          No pregunta "¿seguro?" — dice qué deja de pasar y quién carga con
          ello. Un diálogo que solo pide confirmación se contesta sin leer. */}
      {/* Apagar UN canal: la advertencia dice qué se detiene y qué sigue. */}
      {confirmarCanal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg rounded-xl bg-white shadow-xl">
            <div
              className="h-1 w-full rounded-t-xl"
              style={{
                background:
                  CANALES.find((c) => c.id === confirmarCanal)?.acento ?? TT.rojo,
              }}
            />
            <div className="p-5">
              <div className="mb-3 flex items-start justify-between gap-4">
                <h2 className="flex items-center gap-2 text-lg font-semibold">
                  <AlertTriangle className="h-5 w-5" style={{ color: TT.rojo }} />
                  Vas a apagar {CANALES.find((c) => c.id === confirmarCanal)?.nombre}
                </h2>
                <button
                  onClick={() => setConfirmarCanal(null)}
                  className="rounded p-1 text-slate-400 hover:bg-slate-100"
                  aria-label="Cerrar"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <ul className="mb-4 space-y-2 text-sm text-slate-700">
                <li>
                  · Las ventas de ese canal <b>seguirán entrando</b> como pedido de
                  WooCommerce. Solo se detiene la orden de venta en Odoo.
                </li>
                <li>
                  · <b>El otro canal no se toca</b> — por eso cada uno tiene su
                  interruptor.
                </li>
                <li>
                  · A partir de ahí <b>alguien tiene que capturarlas a mano</b>. Si
                  nadie lo hace, el almacén no se entera de la venta.
                </li>
                <li>· Las órdenes ya creadas <b>se quedan</b>. Esto no borra nada.</li>
              </ul>
              <label className="mb-4 block text-sm">
                <span className="mb-1 block text-slate-600">
                  ¿Por qué lo apagas? Queda en la bitácora.
                </span>
                <input
                  value={motivo}
                  onChange={(e) => setMotivo(e.target.value)}
                  placeholder="p. ej. órdenes duplicadas, Odoo caído…"
                  className="w-full rounded-lg border px-3 py-2 text-sm"
                />
              </label>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setConfirmarCanal(null)}
                  className="rounded-lg border px-4 py-2 text-sm hover:bg-slate-50"
                >
                  Cancelar
                </button>
                <button
                  onClick={() => void mover(false, motivo, confirmarCanal)}
                  disabled={moviendo}
                  className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  style={{ background: TT.rojo }}
                >
                  {moviendo && <Loader2 className="h-4 w-4 animate-spin" />}
                  Sí, apagar
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {confirmarApagado && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg rounded-xl bg-white shadow-xl">
            <div className="h-1 w-full rounded-t-xl" style={{ background: TT.rojo }} />
            <div className="p-5">
              <div className="mb-3 flex items-start justify-between gap-4">
                <h2 className="flex items-center gap-2 text-lg font-semibold">
                  <AlertTriangle className="h-5 w-5" style={{ color: TT.rojo }} />
                  Vas a apagar la generación de órdenes
                </h2>
                <button
                  onClick={() => setConfirmarApagado(false)}
                  className="rounded p-1 text-slate-400 hover:bg-slate-100"
                  aria-label="Cerrar"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <ul className="mb-4 space-y-2 text-sm text-slate-700">
                <li className="flex gap-2">
                  <span style={{ color: TT.rojo }}>•</span>
                  <span>
                    Las ventas de TikTok <b>seguirán entrando</b> como pedido de
                    WooCommerce. Lo único que se detiene es la orden de venta en Odoo.
                  </span>
                </li>
                <li className="flex gap-2">
                  <span style={{ color: TT.rojo }}>•</span>
                  <span>
                    A partir de ese momento <b>alguien tiene que capturarlas a mano</b>,
                    como antes. Si nadie lo hace, el almacén no se entera de la venta.
                  </span>
                </li>
                <li className="flex gap-2">
                  <span style={{ color: TT.rojo }}>•</span>
                  <span>
                    Las órdenes <b>ya creadas se quedan</b> — son de ventas reales.
                    Esto no borra nada.
                  </span>
                </li>
                <li className="flex gap-2">
                  <span style={{ color: TT.rojo }}>•</span>
                  <span>
                    Tarda <b>hasta 30 segundos</b> en surtir efecto en todos los procesos.
                  </span>
                </li>
              </ul>

              <label className="mb-4 block text-sm">
                <span className="mb-1 block text-slate-600">
                  ¿Por qué lo apagas? Queda en la bitácora.
                </span>
                <input
                  value={motivo}
                  onChange={(e) => setMotivo(e.target.value)}
                  placeholder="p. ej. órdenes duplicadas, Odoo caído…"
                  className="w-full rounded-lg border px-3 py-2 text-sm"
                />
              </label>

              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setConfirmarApagado(false)}
                  className="rounded-lg border px-4 py-2 text-sm hover:bg-slate-50"
                >
                  Cancelar
                </button>
                <button
                  onClick={() => void mover(false, motivo)}
                  disabled={moviendo}
                  className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  style={{ background: TT.rojo }}
                >
                  {moviendo && <Loader2 className="h-4 w-4 animate-spin" />}
                  Sí, apagar
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
