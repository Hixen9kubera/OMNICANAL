"use client";

/**
 * /analisis/fba — Amazon FBA: inventario, cobertura y plan de envío.
 *
 * Deja de ser placeholder (Eduardo, 18-ago). La fuente es el export
 * "Manage FBA Inventory" de Seller Central, subido aquí mismo: el sync veía
 * 20 SKUs con FBA donde el reporte trae 101, y el reporte además sabe lo que
 * el sync no — lo EN CAMINO (3,426 uds al 18-ago), el ASIN y el volumen por
 * unidad que mide el propio Amazon.
 *
 * Del prompt original de José quedan fuera capacidad contratada y tier por
 * peso (no hay de dónde sacarlos todavía); el plan de envío y el semáforo de
 * cobertura (14/30/50 días) sí están.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CloudDownload, RefreshCw, Upload } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";

interface FilaFba {
  sku: string; titulo: string | null; asin: string | null;
  precio: number | null;
  disponible: number; reservado: number; no_vendible: number; en_camino: number;
  /* `en_inventario` = disponible + reservado + no vendible: lo que HOY ocupa
     espacio y paga almacenaje. `declarado` suma además lo que va en camino. */
  en_inventario: number; declarado: number;
  uds_periodo: number; uds_dia: number | null; ultima_venta: string | null;
  cobertura_dias: number | null; semaforo: string;
  sugerido: number; vol_envio_m3: number | null;
  vol_unidad_cm3: number | null; vol_costeo_cm3: number | null;
  vol_divergente: boolean; costo: number | null;
}
interface Tablero {
  reporte: { archivo: string | null; subido_at: string | null; skus: number } | null;
  /* Avance del refresco por SP-API: Amazon tarda minutos en generar el
     reporte, así que corre en segundo plano y la página lo va leyendo. */
  refresco?: { fase: string; detalle: string | null };
  dias: number; objetivo: number;
  kpis: {
    skus_con_stock: number; en_inventario: number; disponibles: number;
    reservadas: number; en_camino: number; declarado: number;
    sin_venta_skus: number; sin_venta_uds: number;
    plan_uds: number; plan_m3: number;
  } | null;
  filas: FilaFba[];
  sin_fba: { sku: string; titulo: string | null; uds: number }[];
}

const fN = (v: number | null | undefined, dec = 0) =>
  v == null ? "—" : Number(v).toLocaleString("es-MX", { maximumFractionDigits: dec });

/* El semáforo habla de COBERTURA (disponible ÷ venta diaria), con los umbrales
   del restock original: crítico ≤14 días, alerta ≤30, ok ≤50, sobrado arriba.
   AGOTADO es aparte y va primero: vende y FBA está en cero. */
const SEMAFORO: Record<string, { txt: string; chip: string; ayuda: string }> = {
  agotado:   { txt: "AGOTADO",   chip: "bg-red-100 text-red-700",
               ayuda: "Vende y FBA está en CERO: cada día sin mandar es venta perdida" },
  critico:   { txt: "CRÍTICO",   chip: "bg-red-50 text-red-600",
               ayuda: "Al ritmo actual se agota en 14 días o menos" },
  alerta:    { txt: "ALERTA",    chip: "bg-amber-50 text-amber-700",
               ayuda: "Se agota entre 14 y 30 días" },
  ok:        { txt: "OK",        chip: "bg-emerald-50 text-emerald-700",
               ayuda: "Cobertura de 30 a 50 días" },
  sobrado:   { txt: "SOBRADO",   chip: "bg-sky-50 text-sky-700",
               ayuda: "Más de 50 días de cobertura al ritmo actual" },
  sin_venta: { txt: "SIN VENTA", chip: "bg-slate-100 text-slate-500",
               ayuda: "Tiene stock en FBA y no vendió una pieza en el período — paga almacenaje sin devolver nada" },
  en_transito:{ txt: "EN CAMINO", chip: "bg-indigo-50 text-indigo-600",
               ayuda: "Sin stock disponible todavía, pero hay unidades en camino a la bodega" },
  sin_stock: { txt: "SIN STOCK", chip: "bg-slate-50 text-slate-400",
               ayuda: "Sin stock en FBA y sin venta en el período" },
};

const FILTROS: { id: string; label: string; grupos: string[] }[] = [
  { id: "accion",   label: "Requieren acción", grupos: ["agotado", "critico", "alerta"] },
  { id: "stock",    label: "Con stock",        grupos: ["critico", "alerta", "ok", "sobrado", "sin_venta"] },
  { id: "sinventa", label: "Sin venta",        grupos: ["sin_venta"] },
  { id: "todos",    label: "Todos",            grupos: [] },
];

export default function FbaPage() {
  const [data, setData] = useState<Tablero | null>(null);
  const [dias, setDias] = useState(60);
  const [objetivo, setObjetivo] = useState(60);
  const [filtro, setFiltro] = useState("accion");
  const [cargando, setCargando] = useState(true);
  const [subiendo, setSubiendo] = useState(false);
  const [pidiendo, setPidiendo] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const cargar = useCallback(async () => {
    setCargando(true); setErr(null);
    try {
      const r = await fetchSesion(
        `${API_BASE}/api/fba?dias=${dias}&objetivo=${objetivo}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`API ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCargando(false);
    }
  }, [dias, objetivo]);

  useEffect(() => { void cargar(); }, [cargar]);

  const subir = useCallback(async (archivo: File) => {
    setSubiendo(true); setErr(null);
    try {
      const fd = new FormData();
      fd.append("archivo", archivo);
      const r = await fetchSesion(`${API_BASE}/api/fba/reporte`, { method: "POST", body: fd });
      const cuerpo = await r.json().catch(() => null);
      if (!r.ok) throw new Error(cuerpo?.detail ?? `API ${r.status}`);
      await cargar();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubiendo(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }, [cargar]);

  const refrescando = ["arrancando", "solicitando", "esperando", "descargando"]
    .includes(data?.refresco?.fase ?? "");

  /* Mientras Amazon genera el reporte, la página se refresca sola cada 10 s
     para ver el avance y cargar el snapshot nuevo cuando aterrice. */
  useEffect(() => {
    if (!refrescando) return;
    const t = setInterval(() => { void cargar(); }, 10_000);
    return () => clearInterval(t);
  }, [refrescando, cargar]);

  const pedirAmazon = useCallback(async () => {
    setPidiendo(true); setErr(null);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fba/refrescar`, { method: "POST" });
      const cuerpo = await r.json().catch(() => null);
      if (!r.ok) throw new Error(cuerpo?.detail ?? `API ${r.status}`);
      await cargar();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPidiendo(false);
    }
  }, [cargar]);

  const grupos = FILTROS.find((f) => f.id === filtro)?.grupos ?? [];
  const filas = (data?.filas ?? []).filter(
    (f) => !grupos.length || grupos.includes(f.semaforo));

  return (
    <div className="space-y-4">
      {/* Encabezado: qué reporte está cargado y el botón de subir */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <p className="text-sm font-bold text-slate-800">Amazon FBA · inventario y plan de envío</p>
          <p className="text-[11px] text-slate-500">
            {data?.reporte
              ? <>reporte <span className="font-semibold">{data.reporte.archivo}</span> · {fN(data.reporte.skus)} SKUs · subido {data.reporte.subido_at?.slice(0, 16) ?? "—"}</>
              : "sin reporte cargado — sube el export de Seller Central para encender la pestaña"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void pedirAmazon()}
            disabled={pidiendo || refrescando}
            title="Pide el reporte a Amazon por su API y lo carga al terminar (tarda unos minutos)"
            className="flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-600 disabled:opacity-50">
            <CloudDownload size={13} />
            {refrescando
              ? (data?.refresco?.fase === "esperando" ? "Amazon lo genera…" : "Trayendo…")
              : "Traer de Amazon"}
          </button>
          <input ref={inputRef} type="file" accept=".csv,.txt" className="hidden"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) void subir(f); }} />
          <button
            onClick={() => inputRef.current?.click()}
            disabled={subiendo}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
            <Upload size={13} /> {subiendo ? "Subiendo…" : "Subir reporte"}
          </button>
          <button onClick={() => void cargar()} disabled={cargando}
                  className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50"
                  title="Refrescar">
            <RefreshCw size={13} className={cargando ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {data?.refresco?.fase === "error" && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-[12px] text-amber-800">
          El refresco desde Amazon falló: {data.refresco.detalle ?? "sin detalle"}.
          El reporte cargado no se tocó — puedes subirlo a mano mientras tanto.
        </div>
      )}
      {err && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {err}
        </div>
      )}

      {/* Sin reporte: la pestaña explica cómo encenderse en vez de verse rota */}
      {data && !data.reporte && !err && (
        <div className="rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-600 shadow-sm">
          <p className="font-semibold text-slate-800">Cómo encender esta pestaña</p>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-[13px]">
            <li>En Seller Central: <b>Inventario → Administrar inventario FBA → Descargar</b> (reporte “Manage FBA Inventory”).</li>
            <li>Sube aquí el CSV con el botón <b>Subir reporte</b>.</li>
          </ol>
          <p className="mt-2 text-[12px] text-slate-400">
            Cada subida reemplaza la foto anterior. El sync automático no alcanza:
            ve una fracción del FBA real y no sabe nada de lo que va en camino.
          </p>
        </div>
      )}

      {data?.kpis && (
        <>
          {/* KPIs */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
            {([
              ["En inventario", fN(data.kpis.en_inventario),
               "afn-warehouse-quantity: lo que HOY está físicamente en la bodega de Amazon "
               + "y paga almacenaje = disponibles + reservadas + no vendibles. Es el número "
               + "que cuadra con Seller Central"],
              ["Disponibles", fN(data.kpis.disponibles),
               "afn-fulfillable-quantity: solo lo que puede venderse ahora. Es menor que "
               + "«En inventario» porque lo reservado y lo no vendible ocupan lugar pero no se venden"],
              ["En camino", fN(data.kpis.en_camino),
               "afn-inbound: en preparación, embarcadas o recibiéndose. Todavía no ocupan bodega"],
              ["Declarado en FULL", fN(data.kpis.declarado),
               "afn-total-quantity: todo lo comprometido con FBA = en inventario + en camino"],
              ["SKUs con stock", fN(data.kpis.skus_con_stock), "productos con al menos una pieza disponible"],
              ["Sin venta", `${fN(data.kpis.sin_venta_skus)} · ${fN(data.kpis.sin_venta_uds)}u`,
               "SKUs con stock FBA y cero ventas en el período — pagan almacenaje sin devolver nada. "
               + "Las unidades cuentan TODO lo que está en bodega, no solo lo vendible"],
              ["Plan de envío", `${fN(data.kpis.plan_uds)}u`,
               `piezas para dejar ${data.objetivo} días de cobertura, descontando lo disponible y lo en camino`],
              ["Volumen del plan", `${fN(data.kpis.plan_m3, 2)} m³`,
               "volumen del envío sugerido, con el volumen por unidad que mide Amazon. "
               + "OJO: es volumen de PRODUCTO, no de espacio en bodega — Amazon mide la ocupación "
               + "con otro criterio y sale bastante mayor"],
            ] as [string, string, string][]).map(([et, v, ayuda]) => (
              <div key={et} title={ayuda}
                   className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-sm">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{et}</p>
                <p className="text-lg font-bold text-slate-800">{v}</p>
              </div>
            ))}
          </div>

          {/* Controles */}
          <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-white px-4 py-2.5 shadow-sm text-[11px]">
            <span className="flex items-center gap-1.5">
              <span className="font-semibold uppercase text-slate-400">Ventas de</span>
              {[30, 60, 90].map((d) => (
                <button key={d} onClick={() => setDias(d)}
                        className={`rounded px-2 py-0.5 font-semibold ${dias === d ? "bg-slate-800 text-white" : "text-slate-500 hover:bg-slate-100"}`}>
                  {d}d
                </button>
              ))}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="font-semibold uppercase text-slate-400"
                    title="El plan de envío intenta dejar esta cobertura">Objetivo</span>
              {[30, 60, 90].map((d) => (
                <button key={d} onClick={() => setObjetivo(d)}
                        className={`rounded px-2 py-0.5 font-semibold ${objetivo === d ? "bg-indigo-600 text-white" : "text-slate-500 hover:bg-slate-100"}`}>
                  {d}d
                </button>
              ))}
            </span>
            <span className="ml-auto flex items-center gap-1.5">
              {FILTROS.map((f) => (
                <button key={f.id} onClick={() => setFiltro(f.id)}
                        className={`rounded px-2 py-0.5 font-semibold ${filtro === f.id ? "bg-slate-800 text-white" : "text-slate-500 hover:bg-slate-100"}`}>
                  {f.label}
                </button>
              ))}
            </span>
          </div>

          {/* Tabla */}
          <div className={`overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm transition-opacity ${cargando ? "opacity-50" : ""}`}>
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/70 text-[10px] uppercase tracking-wide text-slate-500">
                  <th className="px-3 py-2 text-left font-semibold">Producto</th>
                  <th className="px-2 py-2 text-right font-semibold">Precio</th>
                  <th className="px-2 py-2 text-right font-semibold" title="Listas para venderse">Disponible</th>
                  <th className="px-2 py-2 text-right font-semibold" title="Apartadas por pedidos o traslados internos de Amazon">Reserv.</th>
                  <th className="px-2 py-2 text-right font-semibold" title="Inbound: en preparación + embarcadas + recibiéndose">En camino</th>
                  <th className="px-2 py-2 text-right font-semibold">Vendido {data.dias}d</th>
                  <th className="px-2 py-2 text-right font-semibold" title="disponible ÷ venta diaria del período">Cobertura</th>
                  <th className="px-2 py-2 text-left font-semibold">Estado</th>
                  <th className="px-2 py-2 text-right font-semibold"
                      title={`Piezas para dejar ${data.objetivo} días de cobertura, descontando lo disponible y lo en camino`}>Enviar</th>
                  <th className="px-2 py-2 text-right font-semibold" title="Volumen del envío sugerido (medida de Amazon por unidad)">m³</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((f) => {
                  const s = SEMAFORO[f.semaforo] ?? SEMAFORO.sin_stock;
                  return (
                    <tr key={f.sku} className="border-b border-slate-50 align-middle hover:bg-slate-50/60">
                      <td className="max-w-[300px] px-3 py-1.5">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-[11px] font-semibold text-indigo-600">{f.sku}</span>
                          {f.asin && (
                            <a href={`https://www.amazon.com.mx/dp/${f.asin}`} target="_blank"
                               rel="noreferrer" title={`Abrir ${f.asin} en Amazon`}
                               className="rounded bg-slate-100 px-1 text-[9px] font-bold text-slate-500 hover:bg-amber-100 hover:text-amber-700">
                              {f.asin}
                            </a>
                          )}
                          {f.vol_divergente && (
                            <span className="font-bold text-amber-500"
                                  title={`Amazon mide ${fN(f.vol_unidad_cm3)} cm³ por unidad contra ${fN(f.vol_costeo_cm3)} cm³ del costeo — más del doble de diferencia: alguna de las dos capturas está mal (mismo criterio que el peso de la báscula de ML)`}>
                              ⚠
                            </span>
                          )}
                        </div>
                        <div className="truncate text-[11px] text-slate-400">{f.titulo ?? ""}</div>
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-slate-600">
                        {f.precio ? `$${fN(f.precio, 2)}` : "—"}
                      </td>
                      <td className="px-2 py-1.5 text-right font-semibold tabular-nums text-slate-700"
                          title={`En inventario (ocupa bodega): ${fN(f.en_inventario)} · declarado en FULL: ${fN(f.declarado)}`}>
                        {fN(f.disponible)}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-slate-400">{f.reservado || "—"}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-indigo-600">{f.en_camino || "—"}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-slate-600"
                          title={f.ultima_venta ? `última venta ${f.ultima_venta}` : "sin ventas en el período"}>
                        {f.uds_periodo || "—"}
                        {f.uds_dia != null && <span className="ml-1 text-[10px] text-slate-400">({fN(f.uds_dia, 2)}/d)</span>}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-slate-600">
                        {f.cobertura_dias != null ? `${fN(f.cobertura_dias)}d` : "—"}
                      </td>
                      <td className="px-2 py-1.5">
                        <span title={s.ayuda} className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${s.chip}`}>{s.txt}</span>
                      </td>
                      <td className="px-2 py-1.5 text-right font-semibold tabular-nums text-slate-800">
                        {f.sugerido || "—"}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">
                        {f.vol_envio_m3 != null ? fN(f.vol_envio_m3, 2) : "—"}
                      </td>
                    </tr>
                  );
                })}
                {!filas.length && (
                  <tr><td colSpan={10} className="px-3 py-8 text-center text-slate-400">
                    Nada en este filtro.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Venden en Amazon sin listing FBA: los candidatos que el reporte no ve */}
          {data.sin_fba.length > 0 && (
            <div className="rounded-2xl border border-amber-200 bg-amber-50/50 px-4 py-3 text-[12px]">
              <p className="font-bold text-amber-800">
                Venden en Amazon y NO están en FBA ({data.sin_fba.length})
              </p>
              <p className="mb-2 text-[11px] text-slate-500">
                se surten por otra vía; candidatos naturales a mandar a la bodega de Amazon
              </p>
              <div className="flex flex-wrap gap-x-5 gap-y-1">
                {data.sin_fba.map((s) => (
                  <span key={s.sku} title={s.titulo ?? ""}>
                    <span className="font-mono font-semibold text-slate-700">{s.sku}</span>
                    <span className="text-slate-500"> · {fN(s.uds)}u</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
