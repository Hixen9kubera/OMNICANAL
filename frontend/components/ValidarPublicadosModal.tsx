"use client";

/**
 * ValidarPublicadosModal — "Validar costo · PUBLICADOS EN MERCADO LIBRE".
 *
 * Es el Resolver AL REVÉS. El de siempre es packing-list-primero: cargas un
 * xlsx y le buscas SKUs a sus renglones. Este es SKU-primero: seleccionas SKUs
 * en la tabla de Costos y a cada uno se le busca SU renglón en el packing list
 * que le toca, subiendo una escalera que para en el primer peldaño que resuelve:
 *
 *   0 · foto de Odoo contra las fotos embebidas del packing list
 *       — sha256 (mismo archivo, byte a byte) y luego dHash ≤8/64 con margen
 *   1 · léxico (se calcula, se muestra, NO decide)
 *   2 · foto de la PUBLICACIÓN DE MERCADO LIBRE + veredicto de la IA
 *
 * REGLA CRÍTICA DE BRANDON: el proceso aplica ÚNICAMENTE a productos publicados
 * en Mercado Libre. Este modal lo dice y lo enseña —cuáles entran, cuáles no y
 * por qué— pero la regla la impone el BACKEND: al arrancar el trabajo y otra vez
 * justo antes de escribir. Un filtro de pantalla se lo salta el primer `curl`.
 *
 * NADA se guarda hasta que el usuario confirma. El análisis vive ~3 h en memoria
 * del backend y no se puede recuperar: cerrar la ventana lo pierde.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  Clock,
  Cpu,
  Image as ImageIcon,
  Link2,
  Loader2,
  Lock,
  PackageSearch,
  Save,
  ShieldAlert,
  X,
} from "lucide-react";

import ChipRevision from "@/components/ChipRevision";
import useTrabajoJob from "@/components/resolver/useTrabajoJob";
import { COLOR, TARIFA_CBM, TIPO_CAMBIO_DEFAULT, mxn } from "@/components/resolver/comunes";
import {
  archivoPublicados,
  arrancarPublicados,
  corregirFilaPublicados,
  estadoPublicados,
  guardarPublicados,
  mensajeDeError,
  preflightPublicados,
} from "@/lib/api";
import type {
  EstadoPublicado,
  FilaPublicado,
  PreflightResp,
  PublicadoOmitido,
  PublicadosGuardado,
} from "@/lib/types";

interface Props {
  /** Los SKUs seleccionados en la tabla — TODOS, sin pre-filtrar. */
  skus: string[];
  onCerrar: () => void;
  /** Se llama tras un guardado exitoso, para que la tabla de atrás se refresque. */
  onGuardado?: () => void;
}

/** Cómo se pinta cada peldaño de la escalera. */
const PELDANO: Record<EstadoPublicado, { chip: string; label: string; ayuda: string }> = {
  sha256: {
    chip: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    label: "foto de Odoo · exacta",
    ayuda:
      "Peldaño 0 — la foto de Odoo y la del packing list son el MISMO archivo "
      + "(sha256 idéntico). Es el empate más fuerte que hay.",
  },
  dhash: {
    chip: "bg-teal-50 text-teal-700 ring-teal-200",
    label: "foto de Odoo · dHash",
    ayuda:
      "Peldaño 0 — las fotos se parecen lo suficiente (distancia ≤8 de 64 bits) "
      + "y con margen sobre el segundo candidato.",
  },
  ia: {
    chip: "bg-violet-50 text-violet-700 ring-violet-200",
    label: "foto de ML + IA",
    ayuda:
      "Peldaño 2 — la foto de Odoo no sirvió, así que se comparó la foto de la "
      + "PUBLICACIÓN de Mercado Libre contra las fotos candidatas del packing "
      + "list y dictaminó la IA. Míralo antes de aprobarlo.",
  },
  sin_match: {
    chip: "bg-amber-50 text-amber-800 ring-amber-200",
    label: "sin empate",
    ayuda:
      "Ningún peldaño resolvió. Abajo están los candidatos con sus fotos: "
      + "elige a mano o déjalo fuera.",
  },
  sin_insumo: {
    chip: "bg-slate-100 text-slate-600 ring-slate-200",
    label: "sin insumos",
    ayuda:
      "Faltó con qué trabajar: sin contenedor conocido, sin foto en Odoo o sin "
      + "packing list localizado. Puedes pegarle la liga de Drive a mano.",
  },
};

const CONFIANZA: Record<string, string> = {
  alta: "bg-emerald-50 text-emerald-700",
  media: "bg-amber-50 text-amber-800",
  baja: "bg-rose-50 text-rose-700",
};

/** Texto humano de por qué un SKU quedó fuera (del lote o del guardado). */
const MOTIVO: Record<string, string> = {
  no_publicado_ml: "sin publicación viva en Mercado Libre",
  sin_odoo: "no existe en Odoo: no hay foto ni contenedor de dónde partir",
  sin_contenedor: "sin contenedor en kubera ni en Odoo",
  duplicado: "repetido en la selección",
  ya_validado: "ya tiene COSTO VALIDADO y no se marcó para liberar el candado",
  sin_costo: "el packing list no dio costo",
  sin_dimensiones: "sin dimensiones de pieza",
  sku_provisional: "SKU provisional (no es un SKU real del catálogo)",
  confianza_baja: "confianza baja: hay que aprobarlo uno por uno",
};
const motivoTexto = (m: string) => MOTIVO[m] ?? m;

/**
 * Filas que se pueden aprobar EN LOTE: las que no exigen mirar las fotos.
 *
 * Solo los peldaños DETERMINISTAS. `sha256` es empate exacto de archivo y
 * `dhash` es una distancia medida: si se corre otra vez, dan lo mismo.
 *
 * El peldaño de IA queda FUERA a propósito, aunque venga con confianza "alta".
 * Medido el 27-ago-2026 con TEC-2162-NEG: dos corridas seguidas del mismo SKU
 * eligieron renglones distintos —la 123 y la 19— y en cada una la IA declaró
 * "alta" para el que escogía y "alta" para descartar al otro. Entre las dos
 * respuestas hay $54 de costo (11%) y dimensiones que no se parecen
 * (13×32×47 contra 52×35×15, 6 piezas por caja contra 4).
 *
 * Aprobar eso en lote sería escribir un costo distinto según a qué hora se
 * apretó el botón, y encima blindarlo con el candado de COSTO VALIDADO. La IA
 * está para ACOTAR candidatos y explicar por qué; quien decide es la persona
 * que mira las tres fotos, que es justo lo que pidió Brandon.
 */
const esSegura = (f: FilaPublicado) =>
  f.costo != null
  && (f.estado === "sha256" || f.estado === "dhash")
  && f.confianza !== "baja";

const num = (v: number | null | undefined, dec = 2) =>
  v == null ? "—" : v.toFixed(dec);
const lwh = (v: [number, number, number] | null) =>
  v ? `${v[0].toFixed(1)}×${v[1].toFixed(1)}×${v[2].toFixed(1)}` : "—";

export default function ValidarPublicadosModal({ skus, onCerrar, onGuardado }: Props) {
  /**
   * La página monta este modal con `skus={[...seleccion]}`: un arreglo NUEVO en
   * cada render del padre. Si el efecto del pronóstico dependiera de esa
   * referencia, cualquier re-render de la tabla de atrás dispararía otra
   * consulta. Se ancla al CONTENIDO, no a la identidad del arreglo.
   */
  const clave = skus.join(",");
  const lista = useMemo(() => (clave ? clave.split(",") : []), [clave]);

  const [pre, setPre] = useState<PreflightResp | null>(null);
  const [cargandoPre, setCargandoPre] = useState(false);
  const [errorPre, setErrorPre] = useState<string | null>(null);

  const [tarifa, setTarifa] = useState(String(TARIFA_CBM));
  const [tipoCambio, setTipoCambio] = useState(String(TIPO_CAMBIO_DEFAULT));
  const [usarIa, setUsarIa] = useState(true);

  const [jid, setJid] = useState<string | null>(null);
  const [arrancando, setArrancando] = useState(false);
  const { est, error, setError, releer } = useTrabajoJob(jid, estadoPublicados);

  const [aprobados, setAprobados] = useState<Set<string>>(new Set());
  const [liberar, setLiberar] = useState<Set<string>>(new Set());
  const [abierto, setAbierto] = useState<string | null>(null);
  const [vista, setVista] = useState<"todos" | "revisar" | "resueltos">("todos");
  const [guardando, setGuardando] = useState(false);
  const [urlManual, setUrlManual] = useState<Record<string, string>>({});
  // El aviso de "esto escribe en la base" y el resumen de lo que pasó viven
  // DENTRO de la pantalla, no en un `window.confirm`. El del navegador se ve
  // como un error del sitio, no se puede leer con calma —es una sola tira de
  // texto— y en algunos navegadores ni sale. Aquí lo importante es que se
  // entienda qué se va a pisar antes de apretar.
  const [confirmar, setConfirmar] = useState<{
    lista: FilaPublicado[]; total: number;
    conCandado: FilaPublicado[]; bloqueados: FilaPublicado[];
  } | null>(null);
  const [resultado, setResultado] = useState<PublicadosGuardado | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  // El sembrado de aprobados se hace UNA vez al terminar: si se repitiera en
  // cada poll, borraría las casillas que el usuario ya movió a mano.
  const sembrado = useRef(false);

  // ── Pronóstico: quién entra y quién no. No cuesta IA. ──
  useEffect(() => {
    if (!lista.length) return;
    const ac = new AbortController();
    setCargandoPre(true);
    setErrorPre(null);
    preflightPublicados(lista, ac.signal)
      .then(setPre)
      .catch((e) => {
        if ((e as { name?: string })?.name !== "AbortError")
          setErrorPre(mensajeDeError(e, "No se pudo revisar qué SKUs están publicados en Mercado Libre."));
      })
      .finally(() => setCargandoPre(false));
    return () => ac.abort();
  }, [lista]);

  const listo = est?.paso === "listo";
  const filas = useMemo(() => est?.filas ?? [], [est]);

  useEffect(() => {
    if (!listo || sembrado.current) return;
    sembrado.current = true;
    setAprobados(new Set(filas.filter(esSegura).map((f) => f.sku)));
  }, [listo, filas]);

  const arrancar = useCallback(async () => {
    const elegibles = pre?.elegibles.map((e) => e.sku) ?? [];
    if (!elegibles.length) return;
    setArrancando(true);
    setError(null);
    try {
      const r = await arrancarPublicados({
        skus: elegibles,
        tarifaMxnM3: Number(tarifa) || TARIFA_CBM,
        tipoCambio: Number(tipoCambio) || TIPO_CAMBIO_DEFAULT,
        usarIa,
      });
      setJid(r.id);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo arrancar la validación."));
    } finally {
      setArrancando(false);
    }
  }, [pre, tarifa, tipoCambio, usarIa, setError]);

  /** "No es ese renglón, es el 34." */
  const corregir = useCallback(
    async (sku: string, fileId: string | null, filaExcel: number) => {
      if (!jid || !fileId) return;
      setOcupado(sku);
      try {
        await corregirFilaPublicados(jid, sku, fileId, filaExcel);
        await releer();
      } catch (e) {
        setError(mensajeDeError(e, "No se pudo cambiar el renglón."));
      } finally {
        setOcupado(null);
      }
    },
    [jid, releer, setError],
  );

  /** Escape para los que no tienen contenedor: pegar la liga del packing list. */
  const mandarArchivo = useCallback(
    async (sku: string) => {
      const url = (urlManual[sku] ?? "").trim();
      if (!jid || !url) return;
      setOcupado(sku);
      try {
        await archivoPublicados(jid, sku, url);
        setUrlManual((u) => ({ ...u, [sku]: "" }));
        await releer();
      } catch (e) {
        setError(mensajeDeError(e, "No se pudo traer ese packing list."));
      } finally {
        setOcupado(null);
      }
    },
    [jid, urlManual, releer, setError],
  );

  /** Paso 1: enseñar qué se va a escribir. No toca la base. */
  const guardar = useCallback(() => {
    if (!jid || !aprobados.size) return;
    const lista = filas.filter((f) => aprobados.has(f.sku));
    setConfirmar({
      lista,
      total: lista.reduce((a, f) => a + (f.costo ?? 0), 0),
      conCandado: lista.filter((f) => f.revisado_at && liberar.has(f.sku)),
      bloqueados: lista.filter((f) => f.revisado_at && !liberar.has(f.sku)),
    });
  }, [jid, aprobados, liberar, filas]);

  /** Paso 2: escribir de verdad, ya con el visto bueno. */
  const escribir = useCallback(async () => {
    if (!jid) return;
    setConfirmar(null);
    setGuardando(true);
    try {
      const r = await guardarPublicados(jid, [...aprobados], [...liberar]);
      setResultado(r);
      setError(null);
      // Refresca la tabla de atrás, pero la ventana SE QUEDA: el resumen dice
      // qué se saltó y por qué, y cerrarla aquí lo tiraba antes de leerlo.
      if (r.escritos > 0) onGuardado?.();
      if (r.escritos > 0) await releer();
    } catch (e) {
      setError(mensajeDeError(e, "No se pudieron guardar los costos."));
    } finally {
      setGuardando(false);
    }
  }, [jid, aprobados, liberar, onGuardado, releer, setError]);

  const toggleAprobado = (sku: string) =>
    setAprobados((p) => {
      const n = new Set(p);
      if (n.has(sku)) n.delete(sku);
      else n.add(sku);
      return n;
    });
  const toggleLiberar = (sku: string) =>
    setLiberar((p) => {
      const n = new Set(p);
      if (n.has(sku)) n.delete(sku);
      else n.add(sku);
      return n;
    });

  const res = est?.resumen;
  const visibles =
    vista === "revisar"
      ? filas.filter((f) => !esSegura(f))
      : vista === "resueltos"
        ? filas.filter(esSegura)
        : filas;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/50 p-4">
      <div className="my-6 w-full max-w-[1500px] rounded-2xl bg-white shadow-2xl">
        {/* ── Cabecera ── */}
        <div className="flex items-center gap-3 border-b border-slate-200 px-6 py-4">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-lg text-white"
            style={{ background: COLOR }}
          >
            <PackageSearch size={18} />
          </div>
          <div className="flex-1">
            <h2 className="text-base font-bold text-slate-900">
              Validar costo · <span className="text-indigo-600">PRODUCTOS PUBLICADOS EN MERCADO LIBRE</span>
            </h2>
            <p className="text-xs text-slate-500">
              A cada SKU se le busca su renglón en el packing list: foto de Odoo →
              dHash → título → foto de la publicación de ML + IA. Nada se guarda
              hasta que tú lo apruebes.
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
          {(error || errorPre) && (
            <div className="mb-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span className="flex-1">{error ?? errorPre}</span>
              <button onClick={() => { setError(null); setErrorPre(null); }}>✕</button>
            </div>
          )}

          {/* ══ Paso 1: a qué le vas a dar ══ */}
          {!jid && (
            <Preflight
              skus={lista}
              pre={pre}
              cargando={cargandoPre}
              tarifa={tarifa}
              setTarifa={setTarifa}
              tipoCambio={tipoCambio}
              setTipoCambio={setTipoCambio}
              usarIa={usarIa}
              setUsarIa={setUsarIa}
              arrancando={arrancando}
              onArrancar={arrancar}
            />
          )}

          {/* ══ Paso 2: la escalera corriendo ══ */}
          {jid && !listo && est?.paso !== "error" && (
            <div className="py-6">
              <div className="text-center">
                <Loader2 size={26} className="mx-auto mb-3 animate-spin text-indigo-500" />
                <div className="text-sm font-semibold text-slate-700">
                  {est?.paso_label || "Procesando…"}
                </div>
                {!!est?.total && (
                  <div className="mx-auto mt-3 w-72">
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-200">
                      <div
                        className="h-full rounded-full bg-indigo-500 transition-all"
                        style={{ width: `${Math.round((est.actual / est.total) * 100)}%` }}
                      />
                    </div>
                    <div className="mt-1 text-xs tabular-nums text-slate-400">
                      {est.actual} / {est.total} SKUs
                    </div>
                  </div>
                )}
                <p className="mx-auto mt-4 max-w-lg text-xs text-slate-400">
                  Bajar los packing lists, indexar sus fotos y —para los que no
                  empaten por foto— preguntarle a la IA toma minutos.{" "}
                  <b>No cierres esta ventana: el análisis vive en memoria del
                  backend y no se puede recuperar.</b>
                </p>
              </div>

              {/* Progreso POR SKU: se ve cuál va cayendo en qué peldaño. */}
              {filas.length > 0 && (
                <div className="mx-auto mt-6 max-h-64 max-w-3xl overflow-y-auto rounded-xl border border-slate-200">
                  {filas.map((f) => (
                    <div
                      key={f.sku}
                      className="flex items-center gap-2 border-b border-slate-100 px-3 py-1.5 text-xs last:border-0"
                    >
                      <span className="w-44 shrink-0 truncate font-mono text-[11px] text-slate-600">
                        {f.sku}
                      </span>
                      <ChipPeldano f={f} />
                      <span className="min-w-0 flex-1 truncate text-[11px] text-slate-400">
                        {f.detalle}
                      </span>
                      <span className="shrink-0 font-mono text-[11px] font-semibold text-slate-700">
                        {mxn(f.costo)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {est?.paso === "error" && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
              <div className="mb-1 font-semibold">La validación falló</div>
              <code className="text-xs">{est.error}</code>
            </div>
          )}

          {/* ══ Paso 3: resultado ══ */}
          {listo && est && (
            <>
              <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs">
                <span className="text-sm font-semibold text-slate-800">
                  {res?.total ?? filas.length} SKUs
                </span>
                <Pastilla tono="bg-emerald-50 text-emerald-700">
                  {res?.sha256 ?? 0} foto exacta
                </Pastilla>
                <Pastilla tono="bg-teal-50 text-teal-700">{res?.dhash ?? 0} dHash</Pastilla>
                <Pastilla tono="bg-violet-50 text-violet-700">{res?.ia ?? 0} por IA</Pastilla>
                <Pastilla tono="bg-amber-50 text-amber-800">
                  {res?.sin_match ?? 0} sin empate
                </Pastilla>
                <Pastilla tono="bg-slate-100 text-slate-600">
                  {res?.sin_insumo ?? 0} sin insumos
                </Pastilla>
                {!!res?.ya_validados && (
                  <Pastilla tono="bg-indigo-50 text-indigo-700">
                    {res.ya_validados} ya validados
                  </Pastilla>
                )}
                <span className="ml-auto text-slate-500">
                  flete = CBM/pieza × ${TARIFA_CBM.toLocaleString("es-MX")}/m³ · TC{" "}
                  {est.opciones?.tipo_cambio ?? tipoCambio}
                </span>
              </div>

              {!!est.omitidos?.length && <ListaOmitidos omitidos={est.omitidos} />}

              {!!est.avisos?.length && (
                <ul className="mb-4 ml-5 list-disc space-y-0.5 text-xs text-amber-700">
                  {est.avisos.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              )}

              {/* Filtros + guardado */}
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <div className="flex rounded-lg border border-slate-200 p-0.5">
                  {([
                    ["todos", `Todos (${filas.length})`],
                    ["revisar", `Necesitan tu ojo (${filas.filter((f) => !esSegura(f)).length})`],
                    ["resueltos", `Resueltos (${filas.filter(esSegura).length})`],
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
                  onClick={() => setAprobados(new Set(filas.filter(esSegura).map((f) => f.sku)))}
                  className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50"
                  title="Marca solo los que empataron por foto con confianza alta o media. Los de confianza baja y los que no empataron se aprueban uno por uno."
                >
                  Marcar los seguros
                </button>
                <button
                  onClick={() => setAprobados(new Set())}
                  className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50"
                >
                  Desmarcar todos
                </button>
                <div className="ml-auto flex items-center gap-3">
                  {/* Los de IA no se marcan solos (ver `esSegura`). Si no se
                      dijera aquí, se leerían como "no encontrados" y se irían
                      sin costo por omisión, que es la peor de las salidas. */}
                  {(() => {
                    const pendientes = filas.filter(
                      (f) => f.estado === "ia" && f.costo != null && !aprobados.has(f.sku),
                    ).length;
                    return pendientes ? (
                      <span className="rounded-lg bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-amber-200">
                        {pendientes} {pendientes === 1 ? "espera" : "esperan"} tu
                        visto bueno · compara las fotos
                      </span>
                    ) : null;
                  })()}
                  <span className="text-xs text-slate-500">
                    <b className="text-indigo-600">{aprobados.size}</b> aprobados ·{" "}
                    {mxn(
                      filas
                        .filter((f) => aprobados.has(f.sku))
                        .reduce((a, f) => a + (f.costo ?? 0), 0),
                    )}{" "}
                    en costos
                  </span>
                  <button
                    onClick={guardar}
                    disabled={guardando || aprobados.size === 0}
                    className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                    style={{ background: COLOR }}
                    title={
                      aprobados.size === 0
                        ? "Marca al menos un SKU para poder guardar"
                        : "Escribe el costo y marca COSTO VALIDADO"
                    }
                  >
                    {guardando ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Save size={14} />
                    )}
                    Guardar {aprobados.size} aprobados
                  </button>
                </div>
              </div>

              {/* Scroll HORIZONTAL, no `overflow-hidden`. Con las fotos a 72 px
                  la tabla ya no cabe en el modal, y `overflow-hidden` no la
                  recorta: hace que las columnas se repartan el ancho a la
                  fuerza y aplasten lo que no quepa —las miniaturas salían de
                  34×71 en vez de 72×72—. Con `min-w` la tabla conserva su
                  tamaño y se desliza. */}
              <div className="overflow-x-auto rounded-xl border border-slate-200">
                <table className="w-full min-w-[1400px] text-xs">
                  <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="w-9 px-2 py-2" />
                      <th className="w-12 px-2 py-2 text-left font-semibold">Foto</th>
                      <th className="px-2 py-2 text-left font-semibold">SKU / Publicación</th>
                      <th className="px-2 py-2 text-left font-semibold">Cómo empató</th>
                      <th className="px-2 py-2 text-left font-semibold">Renglón</th>
                      <th className="px-1 py-2 text-right font-semibold" title="Arriba, lo que dice el packing list: piezas del renglón entre cartones. Abajo, las piezas que comparten el cartón, que es entre las que se reparte el flete.">
                        Piezas
                        <div className="font-normal normal-case text-slate-400">del PL / grupo</div>
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">CBM/pz</th>
                      <th className="px-1 py-2 text-right font-semibold" title="Medidas del CARTÓN, tal como vienen en el packing list.">
                        Caja cm
                        <div className="font-normal normal-case text-slate-400">del PL</div>
                      </th>
                      <th className="px-1 py-2 text-right font-semibold" title="Medidas por PIEZA: el volumen del cartón repartido entre sus piezas. Es lo que se guarda.">
                        Pieza cm
                        <div className="font-normal normal-case text-slate-400">derivada</div>
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">
                        Peso/pz
                        <div className="font-normal normal-case text-slate-400">kg</div>
                      </th>
                      <th className="px-1 py-2 text-right font-semibold" title="Precio del packing list en USD por el tipo de cambio.">
                        Producto
                        <div className="font-normal normal-case text-slate-400">USD → MXN</div>
                      </th>
                      <th className="px-1 py-2 text-right font-semibold" title="CBM por pieza por la tarifa.">
                        Flete
                        <div className="font-normal normal-case text-slate-400">MXN</div>
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">
                        Costo nuevo
                        <div className="font-normal normal-case text-slate-400">MXN</div>
                      </th>
                      <th className="px-1 py-2 text-right font-semibold">
                        Costo hoy
                        <div className="font-normal normal-case text-slate-400">MXN</div>
                      </th>
                      <th className="px-2 py-2 text-center font-semibold">Candado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibles.length === 0 ? (
                      <tr>
                        <td colSpan={14} className="px-4 py-10 text-center text-slate-400">
                          Nada en esta vista.
                        </td>
                      </tr>
                    ) : (
                      visibles.map((f) => (
                        <FilaResultado
                          key={f.sku}
                          f={f}
                          aprobado={aprobados.has(f.sku)}
                          liberado={liberar.has(f.sku)}
                          abierto={abierto === f.sku}
                          ocupado={ocupado === f.sku}
                          urlManual={urlManual[f.sku] ?? ""}
                          onUrlManual={(v) => setUrlManual((u) => ({ ...u, [f.sku]: v }))}
                          onMandarArchivo={() => mandarArchivo(f.sku)}
                          onToggleAprobado={() => toggleAprobado(f.sku)}
                          onToggleLiberar={() => toggleLiberar(f.sku)}
                          onAbrir={() => setAbierto(abierto === f.sku ? null : f.sku)}
                          onElegirFila={(fila) => corregir(f.sku, f.file_id, fila)}
                        />
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              <p className="mt-3 text-[11px] text-slate-400">
                Los de <b>confianza baja</b> y los que quedaron <b>sin empate</b> no
                entran en &ldquo;Marcar los seguros&rdquo;: se aprueban uno por uno,
                después de mirar las fotos. Un empate visual malo no se queda en la
                pantalla — sale al mercado en cinco canales.
              </p>
            </>
          )}
        </div>
      </div>

      {/* ── Los dos avisos que antes eran del NAVEGADOR ─────────────────────
          `window.confirm` y `window.alert` no servían aquí: se ven como un
          error del sitio, no dejan formatear nada —lo que se va a pisar es una
          tabla, no una tira de texto— y hay navegadores que los suprimen. Van
          encima del modal (z-60) y NO lo cierran: el resumen tiene que poder
          leerse con las filas todavía a la vista. */}
      {confirmar && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/60 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl">
            <div className="mb-3 flex items-center gap-2">
              <ShieldAlert size={20} className="text-amber-500" />
              <h3 className="text-base font-bold text-slate-800">
                Vas a escribir {confirmar.lista.length} costo
                {confirmar.lista.length === 1 ? "" : "s"}
              </h3>
            </div>
            <p className="mb-3 text-sm text-slate-600">
              Reemplaza el costo actual de esos productos y mueve el precio
              sugerido en <b>Mercado Libre, Amazon, TikTok, Temu y Walmart</b>.
              Cada uno queda marcado como <b>COSTO VALIDADO</b>.
            </p>
            <div className="mb-3 rounded-lg bg-slate-50 p-3 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-500">SKUs</span>
                <b className="text-slate-800">{confirmar.lista.length}</b>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Suma de los costos nuevos</span>
                <b className="text-slate-800">{mxn(confirmar.total)}</b>
              </div>
            </div>
            {confirmar.conCandado.length > 0 && (
              <p className="mb-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800">
                <Lock size={12} className="mr-1 inline" />
                A <b>{confirmar.conCandado.length}</b> se les va a{" "}
                <b>liberar el candado</b> antes de escribir:{" "}
                {confirmar.conCandado.map((f) => f.sku).join(", ")}
              </p>
            )}
            {confirmar.bloqueados.length > 0 && (
              <p className="mb-2 rounded-lg bg-slate-100 p-2.5 text-xs text-slate-600">
                <b>{confirmar.bloqueados.length}</b> tienen COSTO VALIDADO sin
                liberar y <b>se van a saltar</b>:{" "}
                {confirmar.bloqueados.map((f) => f.sku).join(", ")}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setConfirmar(null)}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50"
              >
                Cancelar
              </button>
              <button
                onClick={escribir}
                className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white"
                style={{ background: COLOR }}
              >
                <Save size={14} /> Escribir {confirmar.lista.length}
              </button>
            </div>
          </div>
        </div>
      )}

      {resultado && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/60 p-4">
          <div className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-2xl bg-white p-6 shadow-2xl">
            <div className="mb-3 flex items-center gap-2">
              {resultado.escritos > 0 ? (
                <Check size={20} className="text-emerald-500" />
              ) : (
                <AlertTriangle size={20} className="text-amber-500" />
              )}
              <h3 className="text-base font-bold text-slate-800">
                {resultado.escritos} costo
                {resultado.escritos === 1 ? "" : "s"} escrito
                {resultado.escritos === 1 ? "" : "s"}
              </h3>
            </div>
            {(resultado.saltados.length > 0 || resultado.errores.length > 0) && (
              <div className="mb-3 overflow-y-auto rounded-lg border border-slate-200">
                <table className="w-full text-xs">
                  <tbody>
                    {resultado.saltados.map((x) => (
                      <tr key={`s-${x.sku}`} className="border-b border-slate-100 last:border-0">
                        <td className="px-3 py-2 font-mono text-slate-600">{x.sku}</td>
                        <td className="px-3 py-2 text-amber-700">
                          se saltó — {x.detalle || motivoTexto(x.motivo)}
                        </td>
                      </tr>
                    ))}
                    {resultado.errores.map((x) => (
                      <tr key={`e-${x.sku}`} className="border-b border-slate-100 last:border-0">
                        <td className="px-3 py-2 font-mono text-slate-600">{x.sku}</td>
                        <td className="px-3 py-2 text-rose-700">error — {x.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mb-4 text-xs text-slate-500">
              La ventana sigue abierta a propósito: el análisis vive en memoria y
              los que se saltaron se pueden corregir aquí mismo.
            </p>
            <div className="flex justify-end">
              <button
                onClick={() => setResultado(null)}
                className="rounded-lg px-4 py-2 text-sm font-semibold text-white"
                style={{ background: COLOR }}
              >
                Entendido
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function Pastilla({ tono, children }: { tono: string; children: React.ReactNode }) {
  return (
    <span className={`rounded-full px-2 py-1 font-semibold ${tono}`}>{children}</span>
  );
}

function ChipPeldano({ f }: { f: FilaPublicado }) {
  const p = PELDANO[f.estado] ?? PELDANO.sin_insumo;
  return (
    <span
      title={p.ayuda}
      className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ${p.chip}`}
    >
      {p.label}
    </span>
  );
}

/** Los SKUs que quedan fuera, agrupados por motivo. Nunca en silencio. */
function ListaOmitidos({ omitidos }: { omitidos: PublicadoOmitido[] }) {
  const porMotivo = omitidos.reduce<Record<string, PublicadoOmitido[]>>((acc, o) => {
    (acc[o.motivo] ??= []).push(o);
    return acc;
  }, {});
  return (
    <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3">
      <div className="flex items-center gap-2 text-xs font-semibold text-amber-900">
        <ShieldAlert size={14} />
        {omitidos.length} SKU(s) de tu selección quedan FUERA
      </div>
      <ul className="mt-1.5 space-y-1 text-[11px] text-amber-800">
        {Object.entries(porMotivo).map(([motivo, lista]) => (
          <li key={motivo}>
            <b>{lista.length}</b> · {motivoTexto(motivo)}
            <div className="mt-0.5 font-mono text-[10px] text-amber-700/80">
              {lista.slice(0, 25).map((o) => o.sku).join(", ")}
              {lista.length > 25 ? ` … y ${lista.length - 25} más` : ""}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── Paso 1: el pronóstico ───────────────────────────────────────────────── */

function Preflight({
  skus, pre, cargando,
  tarifa, setTarifa, tipoCambio, setTipoCambio,
  usarIa, setUsarIa,
  arrancando, onArrancar,
}: {
  skus: string[];
  pre: PreflightResp | null;
  cargando: boolean;
  tarifa: string; setTarifa: (v: string) => void;
  tipoCambio: string; setTipoCambio: (v: string) => void;
  usarIa: boolean; setUsarIa: (v: boolean) => void;
  arrancando: boolean;
  onArrancar: () => void;
}) {
  if (!skus.length) {
    return (
      <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
        No hay SKUs seleccionados. Marca las casillas de la tabla de Costos y vuelve
        a abrir esta ventana — el proceso corre sobre lo que selecciones, y solo
        sobre lo que esté publicado en Mercado Libre.
      </div>
    );
  }
  if (cargando || !pre) {
    return (
      <div className="py-10 text-center">
        <Loader2 size={24} className="mx-auto mb-3 animate-spin text-indigo-500" />
        <div className="text-sm text-slate-600">
          Revisando cuáles de tus {skus.length} SKUs están publicados en Mercado Libre…
        </div>
      </div>
    );
  }

  const r = pre.resumen;
  const frescura = r.listings_ml_actualizado ? new Date(r.listings_ml_actualizado) : null;
  const horas = frescura ? (Date.now() - frescura.getTime()) / 3_600_000 : null;

  return (
    <>
      {/* El recuento: a qué le vas a dar */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        <Casilla rotulo="Seleccionados" valor={r.pedidos} />
        <Casilla rotulo="Publicados en ML" valor={r.elegibles} tono="text-indigo-700" />
        <Casilla rotulo="Quedan fuera" valor={r.omitidos} tono={r.omitidos ? "text-amber-700" : undefined} />
        <Casilla rotulo="Se van a costear" valor={r.expandidos} nota="padres expandidos a variantes" />
        <Casilla rotulo="Con contenedor" valor={r.con_contenedor} />
        <Casilla rotulo="Con foto en Odoo" valor={r.con_foto_odoo} nota="el resto cae al peldaño de IA" />
        <Casilla rotulo="Ya validados" valor={r.ya_validados} nota="tienen candado" />
      </div>

      {horas != null && horas > 1 && (
        <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
          <Clock size={14} className="mt-0.5 shrink-0" />
          <span>
            El censo de publicaciones de ML no se actualiza desde hace{" "}
            <b>{horas.toFixed(1)} h</b> ({frescura?.toLocaleString("es-MX")}). Lo
            alimenta el sync de 15 minutos: si está detenido, este filtro contesta
            con una foto vieja.
          </span>
        </div>
      )}

      {!!pre.omitidos.length && <div className="mt-3"><ListaOmitidos omitidos={pre.omitidos} /></div>}

      {/* Los que SÍ entran, con lo que se sabe de cada uno */}
      {pre.elegibles.length > 0 && (
        <div className="mt-4 max-h-72 overflow-y-auto rounded-xl border border-slate-200">
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-2 py-2 text-left font-semibold">SKU</th>
                <th className="px-2 py-2 text-left font-semibold">Publicación ML</th>
                <th className="px-2 py-2 text-left font-semibold">Contenedor</th>
                <th className="px-2 py-2 text-left font-semibold">Insumos</th>
              </tr>
            </thead>
            <tbody>
              {pre.elegibles.map((e) => (
                <tr key={e.sku} className="border-t border-slate-100">
                  <td className="px-2 py-1.5">
                    <div className="font-mono text-[11px] font-semibold text-slate-700">{e.sku}</div>
                    {e.padre && (
                      <div className="text-[10px] text-sky-700">
                        padre → {e.variantes.length} variante(s):{" "}
                        <span className="font-mono">{e.variantes.slice(0, 4).join(", ")}</span>
                        {e.variantes.length > 4 ? "…" : ""}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-[11px] text-slate-500">
                    {e.cuentas.join(" · ") || "—"}
                    <span className="ml-1 text-slate-400">{e.situaciones.join("/")}</span>
                  </td>
                  <td className="px-2 py-1.5 text-[11px]">
                    {e.contenedor ? (
                      <>
                        <span className="font-mono text-slate-600">{e.contenedor}</span>
                        <span className="ml-1 text-slate-400">({e.fuente_contenedor})</span>
                        {e.fuentes_en_desacuerdo && (
                          <span
                            className="ml-1 text-amber-600"
                            title="Odoo y kubera dicen contenedores distintos: se prueban los dos y desempata la imagen."
                          >
                            ⚠ fuentes en desacuerdo
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-amber-600">sin contenedor</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-[11px] text-slate-500">
                    <span className={e.foto_odoo ? "text-emerald-600" : "text-amber-600"}>
                      {e.foto_odoo ? "con foto de Odoo" : "sin foto de Odoo"}
                    </span>
                    <span className="text-slate-400"> · {e.archivos} archivo(s)</span>
                    {e.revisado_at && (
                      <span className="ml-1">
                        <ChipRevision revisadoAt={e.revisado_at} />
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Parámetros + arranque */}
      <div className="mt-4 flex flex-wrap items-end gap-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <label className="block">
          <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Tarifa de flete (MXN/m³)
          </span>
          <input
            value={tarifa}
            onChange={(e) => setTarifa(e.target.value)}
            inputMode="decimal"
            className="w-28 rounded-lg border border-slate-200 px-3 py-2 text-sm tabular-nums"
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
        <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-slate-600">
          <input
            type="checkbox"
            checked={usarIa}
            onChange={(e) => setUsarIa(e.target.checked)}
            className="h-4 w-4 accent-indigo-600"
          />
          <Cpu size={13} /> Usar IA cuando la foto de Odoo no alcance
        </label>
        <button
          onClick={onArrancar}
          disabled={arrancando || pre.elegibles.length === 0}
          className="ml-auto flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
          style={{ background: COLOR }}
          title={
            pre.elegibles.length === 0
              ? "Ninguno de los SKUs seleccionados tiene publicación viva en Mercado Libre"
              : "Arranca la escalera de empate"
          }
        >
          {arrancando ? <Loader2 size={15} className="animate-spin" /> : <PackageSearch size={15} />}
          Validar {pre.elegibles.length} SKUs publicados en ML
        </button>
      </div>

      <p className="mt-2 text-[11px] text-slate-400">
        El costo se arma con la fórmula avalada: <b>flete = CBM por pieza × tarifa</b>{" "}
        y <b>producto = USD × tipo de cambio</b>. El CBM por pieza sale de repartir
        el cartón entre TODAS las piezas que lo comparten — mezclarlo con las piezas
        de una sola caja multiplica el flete por el número de cajas.
      </p>
    </>
  );
}

function Casilla({ rotulo, valor, nota, tono }: {
  rotulo: string; valor: number; nota?: string; tono?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
      <div className="text-[9px] font-semibold uppercase tracking-wide text-slate-400">{rotulo}</div>
      <div className={`text-lg font-bold tabular-nums ${tono ?? "text-slate-800"}`}>{valor}</div>
      {nota && <div className="text-[9px] leading-tight text-slate-400">{nota}</div>}
    </div>
  );
}

/* ── Paso 3: una fila de resultado + su panel de fotos ───────────────────── */

function FilaResultado({
  f, aprobado, liberado, abierto, ocupado, urlManual,
  onUrlManual, onMandarArchivo, onToggleAprobado, onToggleLiberar, onAbrir, onElegirFila,
}: {
  f: FilaPublicado;
  aprobado: boolean;
  liberado: boolean;
  abierto: boolean;
  ocupado: boolean;
  urlManual: string;
  onUrlManual: (v: string) => void;
  onMandarArchivo: () => void;
  onToggleAprobado: () => void;
  onToggleLiberar: () => void;
  onAbrir: () => void;
  onElegirFila: (fila: number) => void;
}) {
  const dudoso = !esSegura(f);
  const delta =
    f.costo != null && f.costo_viejo != null && f.costo_viejo !== 0
      ? (f.costo - f.costo_viejo) / f.costo_viejo
      : null;

  return (
    <>
      <tr
        className={[
          "border-t border-slate-100",
          aprobado ? "bg-indigo-50/40" : dudoso ? "bg-amber-50/30" : "",
        ].join(" ")}
      >
        <td className="px-2 py-2 align-top">
          <input
            type="checkbox"
            checked={aprobado}
            onChange={onToggleAprobado}
            disabled={f.costo == null}
            title={
              f.costo == null
                ? "No hay costo que guardar para este SKU"
                : "Marcar para escribir su costo"
            }
            className="h-4 w-4 cursor-pointer accent-indigo-600 disabled:cursor-not-allowed disabled:opacity-40"
          />
        </td>
        {/* LAS TRES, no la primera que haya. Antes esta celda pintaba
            `img_pl || img_odoo || img_ml` y enseñaba UNA sola: justo la
            comparación que hay que hacer —¿es el mismo producto?— quedaba
            escondida detrás de un clic. Cada miniatura lleva su letra debajo
            para saber cuál es cuál sin pasar el mouse. */}
        <td className="w-[248px] min-w-[248px] px-3 py-3 align-top">
          <button
            onClick={onAbrir}
            title="Ver las tres fotos en grande y el veredicto"
            className="flex items-start gap-2"
          >
            {([
              ["O", f.img_odoo, "Odoo"],
              ["ML", f.img_ml, "publicación de Mercado Libre"],
              ["PL", f.img_pl, "renglón del packing list"],
            ] as const).map(([letra, src, que]) => (
              <span key={letra} className="flex shrink-0 flex-col items-center gap-0.5">
                {src ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={src}
                    alt={que}
                    title={`Foto de ${que}`}
                    className="h-[72px] w-[72px] shrink-0 rounded-lg border border-slate-200 bg-slate-50 object-contain p-0.5"
                  />
                ) : (
                  <span
                    title={`Sin foto de ${que}`}
                    className="flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 text-slate-300"
                  >
                    <ImageIcon size={12} />
                  </span>
                )}
                <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">
                  {letra}
                </span>
              </span>
            ))}
          </button>
        </td>
        <td className="max-w-[220px] px-2 py-2 align-top">
          <div className="font-mono text-[11px] font-semibold text-slate-700">{f.sku}</div>
          {f.padre && (
            <div className="text-[10px] text-sky-700">variante de {f.padre}</div>
          )}
          <div className="truncate text-[10px] text-slate-500" title={f.titulo_ml ?? f.nombre ?? ""}>
            {f.titulo_ml ?? f.nombre ?? "—"}
          </div>
          <div className="text-[10px] text-slate-400">
            {f.cuenta_ml ?? "—"}
            {f.situacion_ml ? ` · ${f.situacion_ml}` : ""}
          </div>
        </td>
        <td className="max-w-[190px] px-2 py-2 align-top">
          <ChipPeldano f={f} />
          {f.confianza && (
            <span
              className={`ml-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${
                CONFIANZA[f.confianza] ?? "bg-slate-100 text-slate-600"
              }`}
            >
              {f.confianza}
            </span>
          )}
          <div className="truncate text-[10px] text-slate-500" title={f.detalle}>
            {f.detalle}
          </div>
          <button
            onClick={onAbrir}
            className="mt-0.5 text-[10px] font-semibold text-indigo-600 hover:underline"
          >
            {abierto ? "ocultar fotos" : "ver fotos y decidir"}
          </button>
        </td>
        <td className="max-w-[170px] px-2 py-2 align-top text-[10px] text-slate-500">
          {f.archivo ? (
            <>
              <div className="truncate" title={f.archivo}>{f.archivo}</div>
              {/* El número de fila es el dato con el que se vuelve al Excel a
                  comprobar: va grande, no perdido entre lo demás. */}
              <div className="mt-0.5 flex items-baseline gap-1">
                <span className="text-[9px] uppercase text-slate-400">fila</span>
                <span className="text-[15px] font-bold leading-none text-slate-700">
                  {f.fila_excel ?? "—"}
                </span>
              </div>
              {f.grupo?.length > 1 && (
                <div
                  className="mt-0.5 text-amber-700"
                  title={`El cartón lo comparten los renglones ${f.grupo.join(", ")}: el flete se reparte entre las piezas de todos.`}
                >
                  cartón compartido · filas {f.grupo.join(", ")}
                </div>
              )}
              {f.fuente && <div className="text-slate-400">ruteo: {f.fuente}</div>}
            </>
          ) : (
            <span className="text-amber-600">sin packing list</span>
          )}
        </td>
        <td className="px-1 py-2 text-right align-top tabular-nums text-slate-600">
          {/* Lo CRUDO primero: "300 pz en 40 cajas" deja ver de dónde sale el
              7.5, y es lo que permite cachar un packing list mal capturado. */}
          {f.piezas_fila != null && f.cajas ? (
            <div className="text-[10px] text-slate-500">
              {num(f.piezas_fila, 0)} pz &divide; {num(f.cajas, 0)} cajas
              <div className="text-slate-400">
                = {num(f.piezas_fila / f.cajas, 1)} / caja
              </div>
            </div>
          ) : (
            <div className="text-[10px] text-slate-400">&mdash;</div>
          )}
          <div className="mt-1 border-t border-slate-100 pt-1 font-semibold text-slate-700">
            {num(f.piezas_grupo, 1)}
          </div>
        </td>
        <td className="px-1 py-2 text-right align-top tabular-nums text-slate-600">
          {num(f.cbm_pieza, 6)}
        </td>
        <td className="px-1 py-2 text-right align-top tabular-nums text-slate-500">
          {lwh(f.caja_lwh)}
        </td>
        <td className="px-1 py-2 text-right align-top font-semibold tabular-nums text-slate-700">
          {lwh(f.pieza_lwh)}
        </td>
        <td className="px-1 py-2 text-right align-top tabular-nums text-slate-600">
          {num(f.peso_pieza, 3)}
        </td>
        <td className="px-1 py-2 text-right align-top tabular-nums text-slate-600">
          {mxn(f.producto_mxn)}
          {f.precio_usd != null && f.precio_usd > 0 && (
            <div
              className="text-[10px] text-slate-400"
              title="Precio unitario tal como viene en el packing list."
            >
              {f.precio_usd.toFixed(2)} USD
            </div>
          )}
          {f.origen_prod === "kubera" && (
            <div className="text-[9px] text-amber-600" title="El packing list no trae precio unitario: se conservó el costo de producto que ya estaba guardado.">
              conservado
            </div>
          )}
        </td>
        <td className="px-1 py-2 text-right align-top tabular-nums text-slate-600">
          {mxn(f.flete)}
        </td>
        <td className="px-1 py-2 text-right align-top font-semibold tabular-nums text-slate-900">
          {mxn(f.costo)}
        </td>
        <td className="px-1 py-2 text-right align-top tabular-nums text-slate-500">
          {mxn(f.costo_viejo)}
          {delta != null && (
            <div
              className={`text-[10px] font-semibold ${
                Math.abs(delta) < 0.1
                  ? "text-slate-400"
                  : delta > 0
                    ? "text-rose-600"
                    : "text-emerald-600"
              }`}
            >
              {delta > 0 ? "+" : ""}
              {(delta * 100).toFixed(0)}%
            </div>
          )}
        </td>
        <td className="px-2 py-2 text-center align-top">
          {f.revisado_at ? (
            <>
              <ChipRevision revisadoAt={f.revisado_at} />
              <label
                className="mt-1 flex items-center justify-center gap-1 text-[9px] font-semibold text-rose-700"
                title="Con el candado puesto, el UPDATE se descarta EN SILENCIO y creerías que guardaste. Marca esto para liberarlo antes de escribir."
              >
                <input
                  type="checkbox"
                  checked={liberado}
                  onChange={onToggleLiberar}
                  className="h-3 w-3 accent-rose-600"
                />
                <Lock size={9} /> liberar
              </label>
            </>
          ) : (
            <span className="text-[10px] text-slate-300">—</span>
          )}
        </td>
      </tr>

      {abierto && (
        <tr className="border-t border-slate-100 bg-slate-50/70">
          <td />
          <td colSpan={13} className="px-3 pb-4 pt-2">
            <PanelFotos
              f={f}
              ocupado={ocupado}
              urlManual={urlManual}
              onUrlManual={onUrlManual}
              onMandarArchivo={onMandarArchivo}
              onElegirFila={onElegirFila}
            />
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Las TRES fotos lado a lado —Odoo, la publicación de Mercado Libre y el
 * renglón del packing list— con el veredicto de la IA y su confianza.
 *
 * Es el corazón de la revisión humana y lo que pidió Brandon explícitamente:
 * para los que cayeron al peldaño de IA, poder ver de un vistazo si de verdad
 * es el mismo producto físico. Las fotos de fábrica tienen otro fondo y otro
 * ángulo: nunca son idénticas, y por eso la decisión no puede ser solo del
 * modelo.
 */
function PanelFotos({
  f, ocupado, urlManual, onUrlManual, onMandarArchivo, onElegirFila,
}: {
  f: FilaPublicado;
  ocupado: boolean;
  urlManual: string;
  onUrlManual: (v: string) => void;
  onMandarArchivo: () => void;
  onElegirFila: (fila: number) => void;
}) {
  const filasCand = Object.keys(f.cands_img ?? {});
  const veredictoDe = (fila: number) => f.veredicto?.find((v) => v.fila === fila);
  const razonDe = (fila: number) => f.cands_ia?.find((c) => c.fila === fila);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="flex flex-wrap gap-6">
        <Foto
          src={f.img_odoo}
          rotulo="Odoo"
          nota="la foto del maestro — de Odoo solo se toma esto y el contenedor"
        />
        <Foto
          src={f.img_ml}
          rotulo="Publicación de Mercado Libre"
          nota={f.titulo_ml ?? f.item_id_ml ?? ""}
        />
        <Foto
          src={f.img_pl}
          rotulo={`Packing list${f.fila_excel ? ` · fila ${f.fila_excel}` : ""}`}
          nota={f.producto_chn ?? ""}
        />

        <div className="min-w-[240px] flex-1 border-l border-slate-200 pl-5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            Veredicto
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <ChipPeldano f={f} />
            {f.confianza && (
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                  CONFIANZA[f.confianza] ?? "bg-slate-100 text-slate-600"
                }`}
              >
                confianza {f.confianza}
              </span>
            )}
            {f.lexico != null && (
              <span
                className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500"
                title="Cuántos renglones del packing list comparten palabras con el título. Es diagnóstico: NO decide el empate."
              >
                léxico {f.lexico}
              </span>
            )}
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-slate-600">{f.detalle}</p>
          {!!f.veredicto?.length && (
            <ul className="mt-1.5 space-y-1 text-[11px] text-slate-600">
              {f.veredicto.slice(0, 4).map((v) => (
                <li key={v.fila} className="flex gap-1.5">
                  <span className={v.mismo_producto ? "text-emerald-600" : "text-slate-300"}>
                    {v.mismo_producto ? <Check size={12} /> : "·"}
                  </span>
                  <span>
                    <b>fila {v.fila}</b> ({v.confianza}) — {v.por_que}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {f.confianza === "baja" && (
            <div className="mt-2 flex items-start gap-1.5 rounded-lg bg-rose-50 px-2 py-1.5 text-[10px] text-rose-700">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              Confianza baja. Una malla de sombra y un estante metálico son dos
              rejillas grises a 150 px: confirma con el título antes de aprobarlo.
            </div>
          )}
        </div>
      </div>

      {/* Candidatos del packing list: la corrección a mano */}
      {filasCand.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            Renglones candidatos del packing list — clic para usar ese
            {ocupado && <Loader2 size={12} className="animate-spin text-indigo-500" />}
          </div>
          <div className="flex flex-wrap gap-2">
            {filasCand.map((k) => {
              const fila = Number(k);
              const v = veredictoDe(fila);
              const r = razonDe(fila);
              const elegido = f.fila_excel === fila;
              return (
                <button
                  key={k}
                  onClick={() => onElegirFila(fila)}
                  disabled={ocupado || !f.file_id}
                  title={r?.por_que || v?.por_que || `Usar la fila ${fila}`}
                  className={[
                    "flex w-36 shrink-0 flex-col items-center rounded-lg border p-1.5 text-center transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                    elegido
                      ? "border-indigo-400 bg-indigo-50"
                      : "border-slate-200 hover:border-indigo-400 hover:bg-indigo-50",
                  ].join(" ")}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={f.cands_img[k]}
                    alt=""
                    className="h-20 w-20 rounded object-cover"
                  />
                  <div className="mt-1 font-mono text-[10px] font-semibold text-slate-700">
                    fila {fila}
                    {elegido && <span className="ml-1 text-indigo-600">✓</span>}
                  </div>
                  <div className="line-clamp-2 w-full text-[9px] leading-tight text-slate-500">
                    {f.cands_txt?.[k] ?? ""}
                  </div>
                  {v && (
                    <div
                      className={`mt-0.5 text-[9px] font-semibold ${
                        v.mismo_producto ? "text-emerald-600" : "text-slate-400"
                      }`}
                    >
                      {v.mismo_producto ? "es el mismo" : "no es"} · {v.confianza}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Escape para los que no tienen packing list localizado */}
      {(!f.file_id || f.estado === "sin_insumo") && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            <Link2 size={12} /> ¿Sabes cuál es su packing list? Pega la liga de Drive
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={urlManual}
              onChange={(e) => onUrlManual(e.target.value)}
              placeholder="https://drive.google.com/file/d/…/view"
              className="min-w-[280px] flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs"
            />
            <button
              onClick={onMandarArchivo}
              disabled={ocupado || !urlManual.trim()}
              className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 disabled:opacity-50"
            >
              {ocupado ? "Trayendo…" : "Traer y re-empatar"}
            </button>
            <span className="text-[10px] text-slate-400">
              El archivo debe estar compartido como &ldquo;Cualquier persona con el enlace&rdquo;.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function Foto({ src, rotulo, nota }: { src: string | null; rotulo: string; nota?: string }) {
  return (
    <div className="w-40 shrink-0">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
        {rotulo}
      </div>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={rotulo}
          className="mt-1 h-36 w-36 rounded-lg border border-slate-200 object-cover"
        />
      ) : (
        <div className="mt-1 flex h-36 w-36 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 text-[10px] text-slate-400">
          sin foto
        </div>
      )}
      {nota && (
        <div className="mt-1 line-clamp-3 text-[10px] leading-tight text-slate-500" title={nota}>
          {nota}
        </div>
      )}
    </div>
  );
}
