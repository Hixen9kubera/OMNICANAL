"use client";

/**
 * ResolverCostosModal — compara un packing list contra los costos ya capturados.
 *
 * Flujo: cargar (.xlsx o liga de Drive) → el backend parsea, identifica productos
 * con IA, prorratea el flete y empata cada renglón con el SKU que ya existe en
 * ese contenedor → tabla comparativa + análisis del agente → guardar lo aprobado.
 *
 * No persiste nada: el análisis vive 3 h en memoria del backend. Lo único que se
 * escribe es el UPSERT a costos_validados, y solo con lo que el usuario confirme.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ClipboardCopy,
  FileSpreadsheet,
  Link2,
  Loader2,
  Save,
  Search,
  Sparkles,
  Upload,
  X,
} from "lucide-react";

import CeldaNum from "@/components/resolver/CeldaNum";
import {
  COLOR,
  COSTO_CONTENEDOR_DEFAULT,
  ESTADO_ESTILO,
  TIPO_CAMBIO_DEFAULT,
  mxn,
} from "@/components/resolver/comunes";
import {
  analizarPackingArchivo,
  analizarPackingUrl,
  buscarSkuResolver,
  capturarFilaResolver,
  corregirEmpateResolver,
  estadoResolver,
  guardarResolver,
  mensajeDeError,
} from "@/lib/api";
import type {
  ResolverCandidato,
  ResolverEstado,
  ResolverFila,
  ResolverSkuBuscado,
  ResolverValores,
} from "@/lib/types";

export default function ResolverCostosModal({ onCerrar }: { onCerrar: () => void }) {
  const [jid, setJid] = useState<string | null>(null);
  const [est, setEst] = useState<ResolverEstado | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [subiendo, setSubiendo] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [copiado, setCopiado] = useState(false);
  const [urlDrive, setUrlDrive] = useState("");
  const [costoContenedor, setCostoContenedor] = useState(String(COSTO_CONTENEDOR_DEFAULT));
  const [tipoCambio, setTipoCambio] = useState(String(TIPO_CAMBIO_DEFAULT));
  const [vista, setVista] = useState<"todos" | "revisar" | "sin_empate">("todos");
  const inputArchivo = useRef<HTMLInputElement>(null);
  // Ediciones del usuario por índice de renglón. Se mandan al guardar; lo que
  // no tocó viaja como se calculó.
  /**
   * Captura un dato y deja que el backend derive el resto.
   *
   * No se guarda local y ya: el solucionador vive en el servidor porque el
   * cálculo encadena (piezas → piezas por caja → CBM por pieza → flete) y
   * porque cambiar las unidades de un renglón mueve el prorrateo de TODOS —
   * el flete se reparte sobre el CBM del contenedor completo.
   */
  const capturar = useCallback(
    async (indice: number, campo: string, valor: number) => {
      if (!jid) return;
      try {
        await capturarFilaResolver(jid, indice, { [campo]: valor });
        setEst(await estadoResolver(jid));
      } catch (e) {
        setError(mensajeDeError(e, "No se pudo capturar el dato."));
      }
    },
    [jid],
  );

  const opciones = () => ({
    costoContenedor: Number(costoContenedor) || undefined,
    tipoCambio: Number(tipoCambio) || undefined,
  });

  const arrancar = useCallback(async (fn: () => Promise<{ id: string }>) => {
    setSubiendo(true);
    setError(null);
    try {
      const r = await fn();
      setJid(r.id);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo arrancar el análisis."));
    } finally {
      setSubiendo(false);
    }
  }, []);

  // Polling mientras el backend procesa. El análisis de un contenedor grande son
  // cientos de llamadas al LLM: minutos, no segundos.
  useEffect(() => {
    if (!jid) return;
    let vivo = true;
    const tick = async () => {
      try {
        const e = await estadoResolver(jid);
        if (!vivo) return;
        setEst(e);
        if (e.paso !== "listo" && e.paso !== "error") setTimeout(tick, 2500);
      } catch (e) {
        if (vivo)
          setError(mensajeDeError(e, "Se perdió el contacto con el análisis."));
      }
    };
    tick();
    return () => {
      vivo = false;
    };
  }, [jid]);

  const corregir = useCallback(
    async (indice: number, sku: string) => {
      if (!jid) return;
      try {
        await corregirEmpateResolver(jid, indice, sku.trim() || null);
        setEst(await estadoResolver(jid));
      } catch (e) {
        setError(mensajeDeError(e, "No se pudo corregir el empate."));
      }
    },
    [jid],
  );

  const copiar = useCallback(async () => {
    if (!est?.tsv) return;
    await navigator.clipboard.writeText(est.tsv);
    setCopiado(true);
    setTimeout(() => setCopiado(false), 2000);
  }, [est]);

  const guardar = useCallback(
    async (soloAprobados: boolean) => {
      if (!jid || !est?.comparacion) return;
      const filas = est.comparacion.filas;
      // "Guardar aprobados" excluye dos cosas: lo que el agente marcó para
      // revisión, y los empates de CONFIANZA BAJA — que es donde caen los
      // aciertos falsos del reconocimiento de imagen (empató la foto pero el
      // nombre no concuerda). Escribir uno de esos mete el costo de un producto
      // en el SKU de otro.
      const skus = filas
        .filter(
          (f) =>
            f.sku &&
            (!soloAprobados ||
              (f.estado !== "revisar" && f.confianza !== "baja")),
        )
        .map((f) => f.sku as string);
      if (!skus.length) {
        setError("No hay renglones con SKU que guardar.");
        return;
      }
      const ok = window.confirm(
        `Se van a escribir ${skus.length} SKUs en costos_validados.\n\n` +
          `Esto reemplaza el costo actual de esos productos y afecta el precio ` +
          `sugerido de Mercado Libre y el resync a WooCommerce.\n\n¿Continuar?`,
      );
      if (!ok) return;
      setGuardando(true);
      try {
        // Las capturas ya viven en el servidor (capturarFilaResolver), así que
        // guardar solo manda los SKUs aprobados.
        const r = await guardarResolver(jid, skus);
        const partes = [`${r.escritos} SKUs escritos`];
        if (r.saltados.length) partes.push(`${r.saltados.length} saltados`);
        if (r.errores.length) partes.push(`${r.errores.length} con error`);
        setError(null);
        window.alert(partes.join(" · "));
      } catch (e) {
        setError(mensajeDeError(e, "No se pudieron guardar los costos."));
      } finally {
        setGuardando(false);
      }
    },
    [jid, est],
  );

  const listo = est?.paso === "listo";
  const filas = est?.comparacion?.filas ?? [];
  const visibles =
    vista === "revisar"
      ? filas.filter((f) => f.estado === "revisar")
      : vista === "sin_empate"
        ? filas.filter((f) => !f.sku)
        : filas;
  // SKUs del contenedor que ningún renglón reclamó: son los que quedan
  // disponibles para empatar a mano.
  const reclamados = new Set(filas.map((f) => f.sku).filter(Boolean));
  const libres = (est?.candidatos ?? []).filter((c) => !reclamados.has(c.sku));
  const res = est?.comparacion?.resumen;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/50 p-4">
      <div className="my-6 w-full max-w-[1500px] rounded-2xl bg-white shadow-2xl">
        {/* Cabecera */}
        <div className="flex items-center gap-3 border-b border-slate-200 px-6 py-4">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-lg text-white"
            style={{ background: COLOR }}
          >
            <Sparkles size={18} />
          </div>
          <div className="flex-1">
            <h2 className="text-base font-bold text-slate-900">
              Resolver costos desde packing list
            </h2>
            <p className="text-xs text-slate-500">
              Compara lo que ya está capturado contra lo que sale del Excel del
              contenedor. Nada se guarda hasta que tú lo apruebes.
            </p>
          </div>
          <button
            onClick={onCerrar}
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-5">
          {error && (
            <div className="mb-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span className="flex-1">{error}</span>
              <button onClick={() => setError(null)}>✕</button>
            </div>
          )}

          {/* ── Paso 1: cargar ── */}
          {!jid && (
            <>
              <div className="flex flex-wrap items-end gap-4 rounded-2xl border-2 border-dashed border-slate-200 p-5">
                <div className="flex flex-1 items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
                    <FileSpreadsheet size={20} />
                  </div>
                  <div>
                    <button
                      disabled={subiendo}
                      onClick={() => inputArchivo.current?.click()}
                      className="text-sm font-semibold text-indigo-600 underline underline-offset-2 disabled:opacity-50"
                    >
                      Escoge el .xlsx del packing list
                    </button>
                    <p className="mt-0.5 text-xs text-slate-500">
                      Los <b>PL</b> no traen costo USD; solo los <b>CI&amp;PL</b> /{" "}
                      <b>INV&amp;PL</b>.
                    </p>
                  </div>
                </div>
                <label className="block">
                  <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Costo contenedor (MXN)
                  </span>
                  <input
                    value={costoContenedor}
                    onChange={(e) => setCostoContenedor(e.target.value)}
                    inputMode="decimal"
                    className="w-36 rounded-lg border border-slate-200 px-3 py-2 text-sm tabular-nums"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Tipo de cambio
                  </span>
                  <input
                    value={tipoCambio}
                    onChange={(e) => setTipoCambio(e.target.value)}
                    inputMode="decimal"
                    className="w-20 rounded-lg border border-slate-200 px-3 py-2 text-sm tabular-nums"
                  />
                </label>
                <button
                  disabled={subiendo}
                  onClick={() => inputArchivo.current?.click()}
                  className="flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60"
                  style={{ background: COLOR }}
                >
                  {subiendo ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Upload size={15} />
                  )}
                  Analizar
                </button>
                <input
                  ref={inputArchivo}
                  type="file"
                  accept=".xlsx,.xlsm"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) arrancar(() => analizarPackingArchivo(f, opciones()));
                    e.target.value = "";
                  }}
                />
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-3">
                <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  <Link2 size={14} /> o desde Drive
                </span>
                <input
                  value={urlDrive}
                  onChange={(e) => setUrlDrive(e.target.value)}
                  placeholder="https://drive.google.com/file/d/…/view"
                  className="min-w-[260px] flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm"
                />
                <button
                  disabled={subiendo || !urlDrive.trim()}
                  onClick={() =>
                    arrancar(() => analizarPackingUrl(urlDrive.trim(), opciones()))
                  }
                  className="rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm font-semibold text-indigo-700 disabled:opacity-50"
                >
                  Traer de Drive
                </button>
                <p className="w-full text-[11px] text-slate-400">
                  El archivo debe estar compartido como{" "}
                  <b>&ldquo;Cualquier persona con el enlace&rdquo;</b>.
                </p>
              </div>
            </>
          )}

          {/* ── Paso 2: procesando ── */}
          {jid && !listo && est?.paso !== "error" && (
            <div className="py-10 text-center">
              <Loader2 size={26} className="mx-auto mb-3 animate-spin text-indigo-500" />
              <div className="text-sm font-semibold text-slate-700">
                {est?.paso_label || "Procesando…"}
              </div>
              {!!est?.total && (
                <div className="mx-auto mt-3 w-64">
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-200">
                    <div
                      className="h-full rounded-full bg-indigo-500 transition-all"
                      style={{ width: `${Math.round((est.actual / est.total) * 100)}%` }}
                    />
                  </div>
                  <div className="mt-1 text-xs tabular-nums text-slate-400">
                    {est.actual} / {est.total}
                  </div>
                </div>
              )}
              <p className="mx-auto mt-4 max-w-md text-xs text-slate-400">
                Un contenedor grande son cientos de llamadas a la IA. No cierres
                esta ventana: el análisis vive en memoria y no se puede recuperar.
              </p>
            </div>
          )}

          {est?.paso === "error" && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
              <div className="mb-1 font-semibold">El análisis falló</div>
              <code className="text-xs">{est.error}</code>
            </div>
          )}

          {/* ── Paso 3: resultado ── */}
          {listo && est && (
            <>
              <div className="mb-4 flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                <div className="text-sm">
                  <span className="font-semibold text-slate-800">{est.contenedor}</span>
                  {est.contenedor_bd ? (
                    <span className="text-slate-500">
                      {" "}
                      · empatado con <b>{est.contenedor_bd}</b> ({res?.candidatos} SKUs
                      capturados)
                    </span>
                  ) : (
                    <span className="text-amber-600"> · sin costos previos</span>
                  )}
                </div>
                <div className="flex gap-2 text-xs">
                  <span className="rounded-full bg-emerald-50 px-2 py-1 font-semibold text-emerald-700">
                    {res?.iguales} iguales
                  </span>
                  <span className="rounded-full bg-amber-50 px-2 py-1 font-semibold text-amber-800">
                    {res?.revisar} a revisar
                  </span>
                  <span className="rounded-full bg-sky-50 px-2 py-1 font-semibold text-sky-700">
                    {res?.nuevos} nuevos
                  </span>
                </div>
                <div className="ml-auto text-xs text-slate-500">
                  {est.totales?.total_cbm} CBM · {mxn(est.totales?.costo_por_m3)}/m³ · TC{" "}
                  {est.totales?.tipo_cambio}
                </div>
              </div>

              {!!est.avisos?.length && (
                <ul className="mb-4 ml-5 list-disc space-y-0.5 text-xs text-amber-700">
                  {est.avisos.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              )}

              {/* Análisis del agente */}
              {est.analisis && (
                <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50/50 p-4">
                  <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-indigo-900">
                    <Sparkles size={15} /> Análisis
                  </div>
                  <div className="whitespace-pre-wrap text-xs leading-relaxed text-slate-700">
                    {est.analisis}
                  </div>
                </div>
              )}

              {/* Barra de acciones */}
              <div className="mb-3 flex flex-wrap items-center gap-2">
                {/* Tres vistas. "Sin empate" es la pantalla de trabajo: ahí se
                    resuelven a mano los que la IA no pudo, viendo las fotos. */}
                <div className="flex rounded-lg border border-slate-200 p-0.5">
                  {([
                    ["todos", `Todos (${filas.length})`],
                    ["revisar", `A revisar (${res?.revisar ?? 0})`],
                    ["sin_empate", `Sin empate (${res?.nuevos ?? 0})`],
                  ] as const).map(([v, label]) => (
                    <button
                      key={v}
                      onClick={() => setVista(v)}
                      className={[
                        "rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
                        vista === v
                          ? "bg-indigo-50 text-indigo-700"
                          : "text-slate-500 hover:text-slate-800",
                      ].join(" ")}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <button
                  onClick={copiar}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50"
                >
                  {copiado ? <Check size={13} /> : <ClipboardCopy size={13} />}
                  {copiado ? "Copiado" : "Copiar tabla (TSV)"}
                </button>
                <div className="ml-auto flex gap-2">
                  <button
                    onClick={() => guardar(true)}
                    disabled={guardando}
                    className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
                    style={{ background: COLOR }}
                  >
                    {guardando ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Save size={14} />
                    )}
                    Guardar aprobados
                  </button>
                  <button
                    onClick={() => guardar(false)}
                    disabled={guardando}
                    className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                  >
                    Guardar todo
                  </button>
                </div>
              </div>

              {/* ── Empate manual: foto y título de los dos lados ──
                  Todos los renglones deberían empatar salvo que de verdad sean
                  producto nuevo. Aquí se resuelven los que la IA no pudo: a la
                  izquierda el renglón del packing con su foto, a la derecha los
                  SKUs del contenedor que nadie reclamó, con la suya. */}
              {vista === "sin_empate" && (
                <div className="mb-4 space-y-3">
                  {visibles.length === 0 ? (
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-6 text-center text-sm text-emerald-800">
                      <Check size={20} className="mx-auto mb-1" />
                      Todos los renglones tienen empate.
                    </div>
                  ) : (
                    visibles.map((f) => {
                      const i = filas.indexOf(f);
                      return (
                        <div
                          key={i}
                          className="rounded-xl border border-slate-200 bg-white p-3"
                        >
                          <div className="flex gap-4">
                            {/* Lado packing list */}
                            <div className="w-56 shrink-0">
                              {f.imagen ? (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img
                                  src={f.imagen}
                                  alt=""
                                  className="h-28 w-28 rounded-lg border border-slate-200 object-cover"
                                />
                              ) : (
                                <div className="h-28 w-28 rounded-lg bg-slate-100" />
                              )}
                              <div className="mt-1.5 text-xs font-semibold text-slate-800">
                                {f.descripcion}
                              </div>
                              <div className="text-[11px] text-slate-400">
                                {f.producto_chn}
                              </div>
                              <div className="mt-1 text-[11px] text-slate-500">
                                pieza {f.nuevo.largo.toFixed(1)}×
                                {f.nuevo.ancho.toFixed(1)}×{f.nuevo.alto.toFixed(1)} cm
                                {!!f.nuevo.costo_usd && ` · USD ${f.nuevo.costo_usd.toFixed(2)}`}
                              </div>
                              {f.razon_empate && (
                                <div className="mt-1 text-[10px] italic text-amber-600">
                                  {f.razon_empate}
                                </div>
                              )}
                            </div>

                            {/* Candidatos libres del contenedor */}
                            <div className="min-w-0 flex-1 border-l border-slate-200 pl-4">
                              <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                                SKUs de este contenedor sin reclamar ({libres.length})
                                — clic para empatar
                              </div>
                              {/* El empate por contenedor tiene un punto ciego:
                                  si el SKU correcto quedó capturado con OTRO
                                  contenedor, no sale entre los candidatos y el
                                  renglón se queda huérfano sin remedio. Este
                                  buscador va contra todo el catálogo. */}
                              <BuscadorSku
                                jid={jid}
                                onElegir={(sku) => corregir(i, sku)}
                              />
                              {libres.length === 0 ? (
                                <p className="mt-2 text-xs text-slate-400">
                                  No quedan SKUs libres de este contenedor. Usa el
                                  buscador de arriba, o déjalo como producto nuevo.
                                </p>
                              ) : (
                                <div className="flex max-h-40 flex-wrap gap-2 overflow-y-auto">
                                  {libres.map((c) => (
                                    <button
                                      key={c.sku}
                                      onClick={() => corregir(i, c.sku)}
                                      title={`${c.sku} · ${c.nombre}`}
                                      className="flex w-32 shrink-0 flex-col items-center rounded-lg border border-slate-200 p-1.5 text-center hover:border-indigo-400 hover:bg-indigo-50"
                                    >
                                      {c.imagen ? (
                                        // eslint-disable-next-line @next/next/no-img-element
                                        <img
                                          src={c.imagen}
                                          alt=""
                                          className="h-16 w-16 rounded object-cover"
                                        />
                                      ) : (
                                        <div className="h-16 w-16 rounded bg-slate-100" />
                                      )}
                                      <div className="mt-1 w-full truncate font-mono text-[10px] font-semibold text-slate-700">
                                        {c.sku}
                                      </div>
                                      <div className="w-full truncate text-[10px] text-slate-500">
                                        {c.nombre}
                                      </div>
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              )}

              {/* Tabla comparativa: cada producto son DOS renglones apilados —
                  arriba lo que se va a guardar (editable), abajo el packing list
                  como referencia. Así se comparan peso, dimensiones y costo sin
                  tener que leer una fila de 12 columnas de izquierda a derecha. */}
              {vista !== "sin_empate" && (
              <div className="max-h-[52vh] overflow-auto rounded-xl border border-slate-200">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 z-10 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="w-12 px-2 py-2 text-left font-semibold">Foto</th>
                      <th className="px-2 py-2 text-left font-semibold">Origen</th>
                      <th className="px-2 py-2 text-left font-semibold">SKU / Producto</th>
                      <th className="px-1 py-2 text-right font-semibold">Piezas</th>
                      <th className="px-1 py-2 text-right font-semibold">Cajas</th>
                      <th
                        className="border-l border-slate-200 px-1 py-2 text-right font-semibold"
                        title="Dimensiones de la CAJA, en cm"
                      >
                        L
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">W</th>
                      <th className="px-1 py-2 text-right font-semibold">H</th>
                      <th className="px-1 py-2 text-right font-semibold">Vol caja</th>
                      <th
                        className="border-l border-slate-200 px-1 py-2 text-right font-semibold"
                        title="Volumen por PIEZA = vol_caja ÷ piezas por caja"
                      >
                        CBM/pz
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">Peso/pz</th>
                      <th className="border-l border-slate-200 px-1 py-2 text-right font-semibold">
                        USD
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">MXN</th>
                      <th
                        className="px-1 py-2 text-right font-semibold"
                        title="Flete prorrateado por pieza = CBM/pz × $/m³"
                      >
                        Import/pz
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">Import tot</th>
                      <th className="border-l border-slate-200 px-1 py-2 text-right font-semibold">
                        Unitario
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">Total</th>
                      <th className="px-2 py-2 text-center font-semibold">Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibles.map((f) => {
                      const i = filas.indexOf(f);
                      return (
                        <ParComparacion
                          key={i}
                          f={f}
                          indice={i}
                          candidatos={est.candidatos ?? []}
                          tipoCambio={est.totales?.tipo_cambio ?? 19}
                          onCorregir={corregir}
                          onCapturar={capturar}
                        />
                      );
                    })}
                  </tbody>
                </table>
              </div>
              )}

              {!!res?.sin_empatar?.length && (
                <p className="mt-3 text-xs text-slate-500">
                  <b>{res.sin_empatar.length} SKUs</b> del contenedor no fueron
                  reclamados por ningún renglón. O el packing list está incompleto, o
                  son de otro embarque del mismo contenedor.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Un producto = dos renglones apilados.
 *
 *   Arriba  → COSTOS: lo que se va a escribir. Editable, con selector de SKU.
 *   Abajo   → PACKING LIST: lo que dice el Excel, con su foto. Solo referencia.
 *
 * El packing list no trae SKU y costos sí, así que el empate se hace aquí: el
 * selector ofrece los SKUs de ESE contenedor, no los 15,000 del catálogo.
 */
function ParComparacion({
  f,
  indice,
  candidatos,
  tipoCambio,
  onCorregir,
  onCapturar,
}: {
  f: ResolverFila;
  indice: number;
  candidatos: ResolverCandidato[];
  tipoCambio: number;
  onCorregir: (indice: number, sku: string) => void;
  onCapturar: (indice: number, campo: string, v: number) => Promise<void>;
}) {
  const est = ESTADO_ESTILO[f.estado] ?? ESTADO_ESTILO.igual;
  const n = f.nuevo;
  const editado = (c: string) => f.campos_editados?.includes(c);

  // Derivados que se muestran pero no se capturan: se calculan aquí para que la
  // tabla cuadre a la vista sin esperar el viaje al servidor.
  const volCaja = (n.largo_caja ?? 0) * (n.ancho_caja ?? 0) * (n.alto_caja ?? 0) / 1_000_000;
  const importTot = n.costo_cbm * n.unidades;

  const cel = (campo: string, valor: number | null | undefined, dec = 2) => (
    <td className="px-1 py-1.5">
      <CeldaNum
        valor={valor}
        decimales={dec}
        editado={editado(campo)}
        onCambio={(x) => onCapturar(indice, campo, x)}
      />
    </td>
  );
  const ro = (valor: number | null | undefined, fmt = (x: number) => x.toFixed(2),
              extra = "") => (
    <td className={`px-1 py-1.5 text-right tabular-nums ${extra}`}>
      {valor ? fmt(valor) : "—"}
    </td>
  );

  return (
    <>
      {/* ── Fila 1: COSTOS — lo que se va a guardar. Editable. ── */}
      <tr className={`border-t-2 border-slate-200 ${
        f.estado === "revisar" ? "bg-amber-50/50" : "bg-indigo-50/30"}`}>
        <td rowSpan={2} className="px-2 py-1.5 align-middle">
          {f.imagen ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={f.imagen} alt="" className="h-12 w-12 rounded border border-slate-200 object-cover" />
          ) : (
            <div className="h-12 w-12 rounded bg-slate-100" />
          )}
        </td>
        <td className="px-2 py-1.5">
          <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-indigo-700">
            Costos
          </span>
        </td>
        <td className="px-2 py-1.5">
          <select
            value={f.sku || ""}
            onChange={(e) => onCorregir(indice, e.target.value)}
            className="w-full max-w-[210px] rounded border border-slate-300 bg-white px-1 py-1 font-mono text-[11px] focus:border-indigo-400 focus:outline-none"
          >
            <option value="">— sin empate (producto nuevo) —</option>
            {candidatos.map((c) => (
              <option key={c.sku} value={c.sku}>
                {c.sku}{c.nombre ? ` · ${c.nombre.slice(0, 36)}` : ""}
              </option>
            ))}
          </select>
        </td>
        {cel("unidades_totales", n.unidades, 0)}
        {cel("numero_cajas", n.cajas, 0)}
        <td className="border-l border-slate-200 p-0">{cel("largo_caja", n.largo_caja, 1)}</td>
        {cel("ancho_caja", n.ancho_caja, 1)}
        {cel("alto_caja", n.alto_caja, 1)}
        {ro(volCaja, (x) => x.toFixed(6), "text-slate-500")}
        <td className="border-l border-slate-200 p-0">{cel("cbm_por_pieza", n.cbm_por_pieza, 6)}</td>
        {cel("peso_unidad", n.peso, 3)}
        <td className="border-l border-slate-200 p-0">{cel("costo_usd", n.costo_usd, 2)}</td>
        {ro(n.costo_producto, (x) => x.toFixed(2), "text-slate-600")}
        {ro(n.costo_cbm, (x) => x.toFixed(2), "text-slate-600")}
        {ro(importTot, (x) => x.toFixed(2), "text-slate-500")}
        <td className="border-l border-slate-200 px-1 py-1.5 text-right font-semibold tabular-nums text-slate-900">
          {n.costo_total ? n.costo_total.toFixed(2) : "—"}
        </td>
        {ro(n.costo_total * n.unidades, (x) => x.toFixed(2), "font-semibold text-slate-700")}
        <td className="px-2 py-1.5 text-center">
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${est.chip}`}>
            {est.label}
          </span>
          {f.diferencia != null && (
            <div className={`text-[10px] font-semibold tabular-nums ${
              f.diferencia > 0 ? "text-rose-600" : "text-emerald-600"}`}>
              {(f.diferencia * 100).toFixed(0)}%
            </div>
          )}
        </td>
      </tr>

      {/* ── Fila 2: PACKING — referencia, no se toca ── */}
      <tr className="border-b border-slate-200 text-[11px] text-slate-500">
        <td className="px-2 py-1.5">
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-500">
            Packing
          </span>
        </td>
        <td className="max-w-[210px] px-2 py-1.5">
          <div className="truncate" title={f.descripcion}>{f.descripcion}</div>
          {f.razon_empate && (
            <div className="truncate text-[9px] text-slate-400" title={f.razon_empate}>
              {f.confianza} · {f.razon_empate}
            </div>
          )}
        </td>
        <td className="px-1 py-1.5 text-right tabular-nums">{n.unidades || "—"}</td>
        <td className="px-1 py-1.5 text-right tabular-nums">{n.cajas || "—"}</td>
        <td className="border-l border-slate-200 px-1 py-1.5 text-right tabular-nums" colSpan={4}>
          pieza {n.largo.toFixed(1)}×{n.ancho.toFixed(1)}×{n.alto.toFixed(1)} cm
        </td>
        <td className="border-l border-slate-200 px-1 py-1.5 text-right tabular-nums" colSpan={2}>
          {n.unidades && n.cajas ? `${(n.unidades / n.cajas).toFixed(1)} pz/caja` : "—"}
        </td>
        <td className="border-l border-slate-200 px-1 py-1.5 text-right tabular-nums" colSpan={3}>
          TC {tipoCambio}
        </td>
        <td className="px-1 py-1.5 text-right tabular-nums" />
        <td className="border-l border-slate-200 px-1 py-1.5 text-right tabular-nums" colSpan={2}>
          {f.actual ? `antes ${mxn(f.actual.costo_total)}` : "sin costo previo"}
        </td>
        <td className="px-2 py-1.5 text-center text-[10px]">
          {!!f.faltantes?.length && (
            <span className="text-rose-500" title={f.faltantes.join(", ")}>
              falta {f.faltantes.length}
            </span>
          )}
        </td>
      </tr>
    </>
  );
}

/**
 * Busca un SKU en todo el catálogo y lo asigna a un renglón.
 *
 * Existe porque los candidatos del contenedor no siempre bastan: un SKU
 * capturado con otro contenedor —o sin costo todavía— no aparece ahí, y sin
 * esto el renglón se queda huérfano aunque el producto sí exista.
 */
function BuscadorSku({
  jid,
  onElegir,
}: {
  jid: string | null;
  onElegir: (sku: string) => void;
}) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<ResolverSkuBuscado[]>([]);
  const [buscando, setBuscando] = useState(false);

  // Se busca al soltar la tecla, con pausa: sin esto cada letra dispara una
  // consulta al catálogo completo.
  useEffect(() => {
    if (!jid || q.trim().length < 2) {
      setRes([]);
      return;
    }
    const ac = new AbortController();
    const t = setTimeout(async () => {
      setBuscando(true);
      try {
        const r = await buscarSkuResolver(jid, q.trim(), ac.signal);
        setRes(r.resultados);
      } catch {
        /* búsqueda best-effort: no vale la pena romper la pantalla por esto */
      } finally {
        setBuscando(false);
      }
    }, 350);
    return () => {
      clearTimeout(t);
      ac.abort();
    };
  }, [jid, q]);

  return (
    <div className="mb-2">
      <div className="relative">
        <Search
          size={13}
          className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-400"
        />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="¿No está arriba? Busca cualquier SKU del catálogo…"
          className="w-full rounded-lg border border-slate-200 py-1.5 pl-7 pr-3 text-xs focus:border-indigo-400 focus:outline-none"
        />
        {buscando && (
          <Loader2
            size={13}
            className="absolute right-2 top-1/2 -translate-y-1/2 animate-spin text-slate-400"
          />
        )}
      </div>

      {res.length > 0 && (
        <div className="mt-1.5 max-h-44 overflow-y-auto rounded-lg border border-slate-200">
          {res.map((r) => {
            const yaUsado = r.usado_en_filas.length > 0;
            return (
              <button
                key={r.sku}
                onClick={() => {
                  onElegir(r.sku);
                  setQ("");
                }}
                className="flex w-full items-center gap-2 border-b border-slate-100 px-2 py-1.5 text-left last:border-0 hover:bg-indigo-50"
              >
                {r.imagen ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={r.imagen} alt="" className="h-8 w-8 rounded object-cover" />
                ) : (
                  <div className="h-8 w-8 rounded bg-slate-100" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-[11px] font-semibold text-slate-700">
                    {r.sku}
                  </div>
                  <div className="truncate text-[10px] text-slate-500">{r.nombre}</div>
                </div>
                <div className="shrink-0 text-right text-[10px]">
                  {/* Dos avisos distintos: "ya lo usaste aquí" (se consolidan
                      las cajas, no es error) y "vive en otro contenedor" (le
                      estás cambiando el embarque a ese SKU). */}
                  {yaUsado && (
                    <div className="font-semibold text-amber-600">
                      ya en fila {r.usado_en_filas.map((n) => n + 1).join(", ")}
                    </div>
                  )}
                  {r.contenedor ? (
                    <div className="text-slate-400">{r.contenedor}</div>
                  ) : (
                    <div className="text-sky-600">sin costo aún</div>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
