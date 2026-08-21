"use client";

/**
 * /analisis/reportes — catálogo de reportes descargables.
 *
 * PRIMER REPORTE VIVO (Eduardo, 05-ago): "Ventas por categoría" en Excel (la
 * réplica del xlsx de José) se descarga desde AQUÍ — se quitó el botón de la
 * página de Categorías. Filtros propios: cuenta, período relativo o absoluto
 * (desde → hasta manda sobre los botones). El resto del catálogo (porta de
 * routers/reports.py + scripts CLI del fulfillment de José, ver
 * docs/fulfillment/prompts_originales_jose.txt, prompt 3) sigue PENDIENTE en
 * la tarjeta de abajo; `reporte_semanal` es la BASE de bodega_k, posiciones_k
 * y envío.
 *
 * OJO con el contrato de costos que declara ese prompt "sin excepciones":
 * costo = costos_validados.costo_total (ya aplicado en Reabastecimiento) y
 * precio sugerido = costos_finales.precio_base — esto último NO adoptado
 * todavía porque choca con la semántica de Brandon (v0.33.x), donde
 * precio_base es el precio de lista antes del descuento. Decisión pendiente.
 */

import { useEffect, useState } from "react";
import { AlertTriangle, Boxes, Download, Eye, FileSpreadsheet, FileText, X } from "lucide-react";
import { API_BASE, descargar, fetchSesion, mensajeDeError } from "@/lib/api";
import FulfillmentPendiente from "@/components/FulfillmentPendiente";

/* Lo que /categorias/excel/preview responde: el contenido del archivo ANTES de
   bajarlo. Sale del mismo `_datos_reporte` que la descarga, así que lo que se
   ve aquí es lo que llega. */
interface Preview {
  rango: {
    desde: string; hasta: string;
    primera_venta: string | null; ultima_venta: string | null;
    dias_con_venta: number;
    /* null cuando el hueco inicial es despreciable — el backend aplica la
       MISMA regla que usa para el aviso dentro del Excel. */
    parcial: {
      primera_venta: string; ultima_venta: string;
      dias_sin_captura: number; dias_ventana: number;
      pct_sin_captura: number; dias_con_venta: number;
    } | null;
  };
  totales: {
    lineas: number; pedidos: number; publicaciones: number;
    categorias: number; skus: number; unidades: number; ingreso: number;
  };
  cobertura: {
    costo: { lineas: number; pct: number; venta_con_costo: number; pct_venta: number };
    envio: { reales: number; estimadas: number; sin_dato: number; pct_real: number };
  };
  diagnosticos: { codigo: string; lineas: number; pct: number }[];
  hojas: { nombre: string; filas: number; columnas: number }[];
}

const money = (n: number) =>
  "$" + Math.round(n).toLocaleString("es-MX");
const miles = (n: number) => n.toLocaleString("es-MX");

/* Barra de cobertura: verde si el dato está completo, ámbar si va a medias.
   El número siempre al lado — una barra sin cifra invita a estimar a ojo. */
function Barra({ pct, bueno }: { pct: number; bueno: boolean }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
      <div className={`h-full rounded-full ${bueno ? "bg-emerald-500" : "bg-amber-500"}`}
           style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </div>
  );
}

/* Se calcula sola con los filtros puestos. Mantiene una altura mínima estable
   para que la tarjeta no salte entre "cargando" y "listo" — el mismo cuidado
   que en el popup de Análisis. */
function VistaPrevia({ p, cargando, error }: {
  p: Preview | null; cargando: boolean; error: string | null;
}) {
  if (error) {
    return (
      <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-4 text-[13px] text-rose-700">
        {error}
      </div>
    );
  }
  if (!p) {
    return (
      <div className="mt-4 min-h-[132px] animate-pulse rounded-xl border border-slate-200 bg-slate-50/70 p-4">
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i}>
              <div className="h-2.5 w-24 rounded bg-slate-200" />
              <div className="mt-2 h-5 w-20 rounded bg-slate-200" />
            </div>
          ))}
        </div>
        <div className="mt-4 h-1.5 w-full rounded-full bg-slate-200" />
      </div>
    );
  }
  const cobCosto = p.cobertura.costo;
  const cobEnvio = p.cobertura.envio;
  return (
    <div className={`mt-4 min-h-[132px] rounded-xl border border-slate-200 bg-slate-50/70 p-4 transition-opacity ${
      cargando ? "opacity-50" : "opacity-100"}`}>
      <div className="mb-3 flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-slate-400">
        <Eye size={13} />
        {cargando ? "Actualizando la vista previa…" : "Esto es lo que vas a descargar"}
      </div>
      {/* El aviso del rango va PRIMERO: es lo que evita bajar un archivo que
          parece cubrir un año y cubre siete semanas. */}
      {p.totales.lineas === 0 ? (
        <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            <b>Sin ventas en este rango.</b> El archivo saldría vacío: no es que
            no haya margen, es que no hay pedidos capturados en esas fechas.
          </span>
        </p>
      ) : p.rango.parcial ? (
        <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            <b>El rango pedido no es el rango con datos.</b> Pediste desde{" "}
            {p.rango.desde}, pero la primera venta capturada es del{" "}
            <b>{p.rango.parcial.primera_venta}</b>:{" "}
            <b>{miles(p.rango.parcial.dias_sin_captura)} días</b> ({p.rango.parcial.pct_sin_captura}%
            {" "}del rango) van sin captura. Hay ventas en{" "}
            <b>{p.rango.parcial.dias_con_venta} días distintos</b>. Los meses
            anteriores no salen bajos: salen sin captura.
          </span>
        </p>
      ) : null}

      {p.totales.lineas > 0 && (
        <>
          <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
            {[
              /* "Pedidos" NO va aquí: en estos datos cada pedido trae
                 exactamente una línea, así que sería la misma cifra dos veces
                 (verificado 7-ago: max_lineas = 1 sobre 9,875 pedidos). */
              ["Líneas vendidas", miles(p.totales.lineas)],
              ["Unidades", miles(p.totales.unidades)],
              ["SKUs distintos", miles(p.totales.skus)],
              ["Ingreso", money(p.totales.ingreso)],
            ].map(([et, v]) => (
              <div key={et}>
                <p className="text-[11px] uppercase tracking-wide text-slate-400">{et}</p>
                <p className="text-lg font-semibold text-slate-800">{v}</p>
              </div>
            ))}
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <div>
              <div className="flex items-baseline justify-between">
                <p className="text-[12px] font-medium text-slate-600">
                  Envío con el cobro REAL de ML
                </p>
                <p className="text-[13px] font-semibold text-slate-800">
                  {cobEnvio.pct_real}%
                </p>
              </div>
              <div className="mt-1"><Barra pct={cobEnvio.pct_real} bueno={cobEnvio.pct_real >= 95} /></div>
              <p className="mt-1 text-[11px] text-slate-500">
                {miles(cobEnvio.reales)} real · {miles(cobEnvio.estimadas)} estimado
                {cobEnvio.sin_dato > 0 && ` · ${miles(cobEnvio.sin_dato)} sin dato`}
              </p>
            </div>
            <div>
              <div className="flex items-baseline justify-between">
                <p className="text-[12px] font-medium text-slate-600">
                  Venta con costo capturado
                </p>
                <p className="text-[13px] font-semibold text-slate-800">
                  {cobCosto.pct_venta}%
                </p>
              </div>
              <div className="mt-1"><Barra pct={cobCosto.pct_venta} bueno={cobCosto.pct_venta >= 95} /></div>
              <p className="mt-1 text-[11px] text-slate-500">
                {money(cobCosto.venta_con_costo)} de {money(p.totales.ingreso)}
              </p>
            </div>
          </div>

          {p.diagnosticos.length > 0 && (
            <div className="mt-4">
              <p className="text-[11px] uppercase tracking-wide text-slate-400">
                Diagnósticos que va a traer
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {p.diagnosticos.map((d) => (
                  <span key={d.codigo}
                        className="rounded-lg bg-white px-2 py-1 text-[11px] text-slate-600 ring-1 ring-slate-200">
                    {d.codigo}{" "}
                    <b className="text-slate-800">{miles(d.lineas)}</b>
                    <span className="text-slate-400"> ({d.pct}%)</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <p className="mt-4 border-t border-slate-200 pt-2.5 text-[11px] text-slate-500">
        {p.hojas.map((h) => `${h.nombre}: ${miles(h.filas)} filas × ${h.columnas} col`).join("  ·  ")}
      </p>
    </div>
  );
}

/* ── Inventario accionable ─────────────────────────────────────────────────
   Dos poblaciones opuestas, no un volcado del almacén:
   INMOVILIZADO = el mercado no lo quiere (y en FULL paga renta todos los días).
   INVISIBLE    = el mercado sí lo quiere y no se lo estamos ofreciendo.       */
interface PreviewInv {
  dias: number;
  inmovilizado: {
    skus: number; unidades_full: number; nunca_vendieron: number;
    /* `variantes` > 0 = el renglón es una FAMILIA y el sku de la izquierda es
       el padre, que nunca vende por sí mismo; `donde` dice en qué variante y
       cuenta están las piezas (Eduardo, 14-ago, sobre CAM-0030). */
    top: { sku: string; titulo: string; full: number; propio: number;
           variantes: number;
           donde: { sku: string; cuenta: string; uds: number }[];
           ultima_venta: string | null }[];
  };
  invisible: {
    skus: number; unidades_vendidas: number; stock_disponible: number;
    top: { sku: string; titulo: string; uds: number; stock: number;
           ultima_venta: string | null }[];
  };
}

const PERIODOS_INV = [30, 60, 90];

function TarjetaInventario() {
  const [cuenta, setCuenta] = useState("");
  const [dias, setDias] = useState(30);
  const [previa, setPrevia] = useState<PreviewInv | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bajando, setBajando] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      setCargando(true);
      setError(null);
      try {
        const par = new URLSearchParams({ dias: String(dias) });
        if (cuenta) par.set("cuenta", cuenta);
        const r = await fetchSesion(
          `${API_BASE}/api/fulfillment/inventario/excel/preview?${par.toString()}`,
          { signal: ctrl.signal });
        if (!r.ok) throw new Error(`El servidor respondió ${r.status}`);
        setPrevia(await r.json());
        setCargando(false);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setError(mensajeDeError(e, "No se pudo calcular la vista previa."));
        setCargando(false);
      }
    }, 450);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [cuenta, dias]);

  const q = new URLSearchParams({ dias: String(dias) });
  if (cuenta) q.set("cuenta", cuenta);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="rounded-xl bg-sky-50 p-2.5 text-sky-600">
          <Boxes size={22} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-slate-800">
            Inventario accionable (Excel)
          </h2>
          <p className="mt-0.5 text-[13px] text-slate-500">
            No es el inventario completo: son las dos poblaciones sobre las que
            se puede actuar hoy.{" "}
            <b className="text-slate-600">Inmovilizado</b> — hay stock en FULL y
            no vende, así que paga renta a Mercado Libre todos los días.{" "}
            <b className="text-slate-600">Invisible</b> — vende y tiene stock,
            pero ninguna publicación está activa. Sin valor en dinero: el costo
            capturado no es de fiar en ~⅓ del catálogo.
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              {CUENTAS.map((c) => (
                <button key={c.id} onClick={() => setCuenta(c.id)}
                        className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                          cuenta === c.id
                            ? "bg-indigo-600 font-semibold text-white"
                            : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
                  {c.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              {PERIODOS_INV.map((d) => (
                <button key={d} onClick={() => setDias(d)}
                        className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                          dias === d
                            ? "bg-slate-900 font-semibold text-white"
                            : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
                  {d} días
                </button>
              ))}
            </div>
            <button
              type="button"
              disabled={bajando}
              onClick={async () => {
                setBajando(true);
                try {
                  await descargar(
                    `${API_BASE}/api/fulfillment/inventario/excel?${q.toString()}`,
                    `inventario-accionable-${cuenta || "consolidado"}-${dias}d.xlsx`,
                  );
                } catch (e) {
                  setError(mensajeDeError(e, "No se pudo descargar."));
                } finally {
                  setBajando(false);
                }
              }}
              className="flex items-center gap-1.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-sm font-medium text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60">
              <Download size={15} /> {bajando ? "Preparando…" : "Descargar"}
            </button>
          </div>
        </div>
      </div>
      {error && (
        <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-700">
          {error}
        </p>
      )}
      <PreviaInventario p={previa} cargando={cargando} dias={dias} />
    </div>
  );
}

function BloqueInv({ titulo, subtitulo, cifras, top, tono }: {
  titulo: string; subtitulo: string;
  cifras: [string, string][];
  top: { sku: string; titulo: string; der: string; pie?: string }[];
  tono: "amber" | "sky";
}) {
  const borde = tono === "amber" ? "border-amber-200 bg-amber-50/50"
                                 : "border-sky-200 bg-sky-50/50";
  const texto = tono === "amber" ? "text-amber-800" : "text-sky-800";
  return (
    <div className={`rounded-xl border p-4 ${borde}`}>
      <p className={`text-[12px] font-bold uppercase tracking-wide ${texto}`}>{titulo}</p>
      <p className="mt-0.5 text-[11px] text-slate-500">{subtitulo}</p>
      <div className="mt-2.5 flex flex-wrap gap-x-6 gap-y-2">
        {cifras.map(([et, v]) => (
          <div key={et}>
            <p className="text-[10px] uppercase tracking-wide text-slate-400">{et}</p>
            <p className="text-base font-semibold text-slate-800">{v}</p>
          </div>
        ))}
      </div>
      {top.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-slate-200/70 pt-2">
          {top.map((t) => (
            <li key={t.sku} className="text-[11px]">
              <div className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate text-slate-600">
                  <code className="font-semibold text-slate-700">{t.sku}</code>{" "}
                  {t.titulo}
                </span>
                <span className="shrink-0 font-semibold text-slate-700">{t.der}</span>
              </div>
              {/* El renglón de una familia se nombra con el SKU padre, que no
                  vende por sí mismo. Sin decir dónde están las piezas, quien
                  revisa busca las ventas del padre y concluye que el reporte
                  miente (Eduardo, 14-ago). */}
              {t.pie && (
                <div className="truncate pl-2 text-[10px] text-slate-400">↳ {t.pie}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PreviaInventario({ p, cargando, dias }: {
  p: PreviewInv | null; cargando: boolean; dias: number;
}) {
  if (!p) {
    return (
      <div className="mt-4 grid min-h-[168px] animate-pulse gap-4 sm:grid-cols-2">
        {[0, 1].map((i) => (
          <div key={i} className="rounded-xl border border-slate-200 bg-slate-50 p-4">
            <div className="h-3 w-32 rounded bg-slate-200" />
            <div className="mt-3 h-6 w-24 rounded bg-slate-200" />
          </div>
        ))}
      </div>
    );
  }
  const m = (n: number) => n.toLocaleString("es-MX");
  return (
    <div className={`mt-4 grid min-h-[168px] gap-4 transition-opacity sm:grid-cols-2 ${
      cargando ? "opacity-50" : "opacity-100"}`}>
      <BloqueInv
        tono="amber"
        titulo="Inmovilizado"
        subtitulo="En FULL y sin una sola venta: paga renta todos los días"
        cifras={[
          ["SKUs", m(p.inmovilizado.skus)],
          ["Unidades en FULL", m(p.inmovilizado.unidades_full)],
          ["Nunca vendieron", m(p.inmovilizado.nunca_vendieron)],
        ]}
        top={p.inmovilizado.top.map((t) => ({
          sku: t.sku, titulo: t.titulo,
          der: `${m(t.full)} en FULL · ${t.ultima_venta ?? "nunca vendió"}`,
          pie: t.variantes
            ? `familia de ${t.variantes} variantes — el stock está en `
              + (t.donde ?? []).map((d) => `${d.sku} (${d.cuenta} ${m(d.uds)})`).join(" · ")
            : undefined,
        }))}
      />
      <BloqueInv
        tono="sky"
        titulo="Invisible"
        subtitulo={`Vendió en ${dias} días, tiene stock y ninguna publicación activa`}
        cifras={[
          ["SKUs", m(p.invisible.skus)],
          [`Unidades vendidas (${dias}d)`, m(p.invisible.unidades_vendidas)],
          ["Stock disponible", m(p.invisible.stock_disponible)],
        ]}
        top={p.invisible.top.map((t) => ({
          sku: t.sku, titulo: t.titulo,
          der: `vendió ${m(t.uds)} · ${m(t.stock)} en bodega`,
        }))}
      />
    </div>
  );
}

const CUENTAS = [
  { id: "", label: "Consolidado" },
  { id: "BEKURA", label: "Bekura" },
  { id: "SANCORFASHION", label: "Sancor" },
  { id: "AMAZON", label: "Amazon" },
];
const PERIODOS = [
  { dias: 7, label: "7 días" },
  { dias: 30, label: "30 días" },
  { dias: 60, label: "60 días" },
  { dias: 90, label: "90 días" },
  { dias: 400, label: "Histórico" },
];

/* Tarjeta del reporte VIVO: Excel de ventas por categoría (tipo José). */
function TarjetaVentasCategoria() {
  const [cuenta, setCuenta] = useState("");
  const [dias, setDias] = useState(60);
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const rangoActivo = Boolean(desde || hasta);
  const [bajando, setBajando] = useState(false);
  const [errorBaja, setErrorBaja] = useState<string | null>(null);
  const [previa, setPrevia] = useState<Preview | null>(null);
  const [cargandoPrevia, setCargandoPrevia] = useState(true);
  const [errorPrevia, setErrorPrevia] = useState<string | null>(null);

  const q = new URLSearchParams({ dias: String(dias) });
  if (cuenta) q.set("cuenta", cuenta);
  if (desde) q.set("desde", desde);
  if (hasta) q.set("hasta", hasta);

  // La vista previa se calcula SOLA con los filtros puestos — sin botón, que
  // era un paso de más para algo que siempre quieres ver. Con retardo porque
  // cada cálculo son 1.4-7 s en el servidor: pasar de "7 días" a "Histórico"
  // haría 4 peticiones si se disparara en cada clic. La anterior se aborta,
  // así que la que pinta es siempre la última pedida, no la que llegue antes.
  useEffect(() => {
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      setCargandoPrevia(true);
      setErrorPrevia(null);
      try {
        const par = new URLSearchParams({ dias: String(dias) });
        if (cuenta) par.set("cuenta", cuenta);
        if (desde) par.set("desde", desde);
        if (hasta) par.set("hasta", hasta);
        const r = await fetchSesion(
          `${API_BASE}/api/fulfillment/categorias/excel/preview?${par.toString()}`,
          { signal: ctrl.signal });
        if (!r.ok) throw new Error(`El servidor respondió ${r.status}`);
        setPrevia(await r.json());
        setCargandoPrevia(false);
      } catch (e) {
        if (ctrl.signal.aborted) return;   // la reemplazó otra: no es un error
        setErrorPrevia(mensajeDeError(e, "No se pudo calcular la vista previa."));
        setCargandoPrevia(false);
      }
    }, 450);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [cuenta, dias, desde, hasta]);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="rounded-xl bg-emerald-50 p-2.5 text-emerald-600">
          <FileSpreadsheet size={22} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-slate-800">
            Ventas y costos (Excel)
          </h2>
          <p className="mt-0.5 text-[13px] text-slate-500">
            Tres hojas en un archivo: <b>Resumen</b> por categoría principal,
            el <b>árbol</b> completo de categorías con sus publicaciones
            (plegable con los +/− de Excel) y <b>Ventas</b>, una fila por cada
            línea vendida. Todas traen costo base y costo final —con la comisión
            REAL de Mercado Libre y el envío—.{" "}
            <b className="text-slate-600">
              No incluye ganancia ni margen:
            </b>{" "}
            la base de costos tiene defectos medidos (precios placeholder, peso
            de caja capturado como pieza) y un margen calculado sobre eso se lee
            como un hecho sin serlo.
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              {CUENTAS.map((c) => (
                <button key={c.id} onClick={() => setCuenta(c.id)}
                        className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                          cuenta === c.id
                            ? "bg-indigo-600 font-semibold text-white"
                            : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
                  {c.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              {PERIODOS.map((p) => (
                <button key={p.dias}
                        onClick={() => { setDias(p.dias); setDesde(""); setHasta(""); }}
                        className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                          dias === p.dias && !rangoActivo
                            ? "bg-slate-900 font-semibold text-white"
                            : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className={`flex items-center gap-1.5 rounded-xl border p-1.5 shadow-sm ${
              rangoActivo ? "border-slate-900 bg-white" : "border-slate-200 bg-white"}`}>
              <input type="date" value={desde} max={hasta || undefined}
                     onChange={(e) => setDesde(e.target.value)}
                     className="rounded-lg px-2 py-1 text-sm text-slate-600 outline-none" />
              <span className="text-xs text-slate-400">a</span>
              <input type="date" value={hasta} min={desde || undefined}
                     onChange={(e) => setHasta(e.target.value)}
                     className="rounded-lg px-2 py-1 text-sm text-slate-600 outline-none" />
              {rangoActivo && (
                <button onClick={() => { setDesde(""); setHasta(""); }}
                        title="Volver a los períodos relativos"
                        className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                  <X size={14} />
                </button>
              )}
            </div>
            {/* Botón y NO un <a href>: una navegación del navegador no manda el
                token de sesión, así que desde el enforcement (5-ago) bajaba un
                401 en vez del Excel. `descargar()` lo pide con la sesión. */}
            <button
              type="button"
              disabled={bajando}
              onClick={async () => {
                setErrorBaja(null);
                setBajando(true);
                try {
                  await descargar(
                    `${API_BASE}/api/fulfillment/categorias/excel?${q.toString()}`,
                    `ventas-por-categoria-${cuenta || "consolidado"}.xlsx`,
                  );
                } catch (e) {
                  setErrorBaja(e instanceof Error ? e.message : "No se pudo descargar.");
                } finally {
                  setBajando(false);
                }
              }}
              className="flex items-center gap-1.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-sm font-medium text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60">
              <Download size={15} /> {bajando ? "Preparando…" : "Descargar"}
            </button>
          </div>
        </div>
      </div>
      {/* FUERA de la fila flex. Estaban DENTRO (el error ya venía así), así que
          se volvían un tercer ítem del flex y le robaban el ancho al texto:
          la descripción de la tarjeta se aplastaba a una palabra por renglón. */}
      {errorBaja && (
        <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-700">
          {errorBaja}
        </p>
      )}
      <VistaPrevia p={previa} cargando={cargandoPrevia} error={errorPrevia} />
    </div>
  );
}

export default function ReportesPage() {
  return (
    <div className="space-y-4">
      <TarjetaVentasCategoria />
      <TarjetaInventario />
      <FulfillmentPendiente
        p={{
          titulo: "Más reportes descargables",
          icono: FileText,
          resumen:
            "Catálogo de reportes en CSV/Excel generados desde el panel, sin scripts " +
            "en la máquina de nadie: ventas, publicaciones, sin ventas, bodega y envío.",
          contenido: [
            "Tarjeta por reporte con su descripción, formato y parámetros (p. ej. umbral de 'sin ventas', default 5)",
            "Botón Generar con estado por tarjeta y descarga al terminar",
            "Reporte maestro por producto cruzando ventas, inventario y costos (CSV)",
            "Aviso en los que dependen del Reporte Semanal (es la base de bodega, posiciones y envío)",
            "Historial de archivos ya generados con su link de descarga",
          ],
          fuentes: [
            {
              nombre: "Historial de ventas por producto",
              detalle: "completo y al día, sin huecos",
              listo: true,
            },
            {
              nombre: "Inventario y precios por canal",
              detalle: "stock FULL / FBA / DROP y precio de cada publicación",
              listo: true,
            },
            {
              nombre: "Costos por producto",
              detalle: "el costo validado de cada variante",
              listo: true,
            },
            // "Carpeta de descargas" salió de aquí: no era una fuente de datos
            // sino el mismo prerrequisito que se retiró, listado dos veces.
          ],
          // Los "bloqueos" se retiraron (Eduardo, 7-ago): ya no aplican. En su
          // lugar va el estado de la CALIDAD DE LOS NÚMEROS, que es lo que hoy
          // limita el reporte — no le impide salir, le impide ser creíble.
          // A propósito EN GRUESO: sin nombres de tablas ni cifras al detalle,
          // que para eso está la columna Diagnóstico del propio Excel.
          numeros: [
            {
              punto: "Costo de envío: se usa el cobro REAL de Mercado Libre por cada embarque, no una estimación por peso",
              listo: true,
            },
            {
              punto: "Comisión: es la que Mercado Libre cobró de verdad en cada venta",
              listo: true,
            },
            {
              punto: "Huecos señalados: cada renglón del Excel dice por qué le falta un dato, en vez de dejarlo en blanco o en cero",
              listo: true,
            },
            {
              punto: "Costo de compra: cerca de un tercio del catálogo trae un precio de lista en dólares en lugar de lo que realmente se pagó. Se destraba con la factura del contenedor",
              listo: false,
            },
            {
              punto: "Productos sin costo: aproximadamente 1 de cada 7 SKUs vendidos no tiene ningún costo capturado, así que su margen no se puede calcular",
              listo: false,
            },
            {
              punto: "Pesos mal capturados: unos 500 productos traen el peso de la caja completa como si fuera el de una pieza. Ya no afecta al envío, pero sí al precio sugerido",
              listo: false,
            },
            {
              punto: "Historial corto: las ventas están capturadas desde finales de junio; pedir un rango más largo no devuelve más historia, y el reporte lo avisa",
              listo: false,
            },
          ],
        }}
      />
    </div>
  );
}
