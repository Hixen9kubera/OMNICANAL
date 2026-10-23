"use client";

/**
 * SugerirPrecio — «Sugerir precio» del Estudio (v0.686.0).
 *
 * A cuánto se vende de verdad EL MISMO producto en Mercado Libre y qué precio
 * compite sin bajar del piso de rentabilidad. Dos modos:
 *   · Rápido (gratis): los más vendidos de la categoría + comisión real + piso.
 *   · Completo ($0.02–0.10): búsqueda en ML + juez por título + juez por FOTO.
 *     Lo que más pesa es Apify (cobra por cómputo): si ML bloquea un término, los
 *     reintentos lo encarecen. El costo real de cada consulta sale al pie.
 *
 * Solo sugiere. «Usar en Precio oferta» llena el campo y el precio se guarda con
 * «Guardar precio y costo», como cualquier precio tecleado.
 *
 * Reemplaza al «Precio de competencia sugerido» de Mejorar con IA, que dependía
 * de SerpApi (agotado) y caía a que la IA estimara sin datos: para TEC-0605 dijo
 * $399 cuando el mismo modelo se vende entre $96 y $245.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ExternalLink, Loader2, Search, Sparkles, TrendingUp, Zap } from "lucide-react";

import {
  mensajeDeError,
  precioSugeridoCompleto,
  precioSugeridoRapido,
  precioSugeridoTrabajo,
  precioSugeridoUltimo,
  utilidadAPrecio,
  type PrecioEstado,
  type PrecioRival,
  type PrecioSugeridoResp,
  type PrecioSugeridoTrabajo,
} from "@/lib/api";
import { useTrabajoJob } from "@/components/resolver/useTrabajoJob";

const mxn = (v: number | null | undefined) =>
  v == null ? "—" : `$${v.toLocaleString("es-MX", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
const usd = (v: number) => `$${v.toFixed(v < 0.1 ? 3 : 2)} USD`;

const ESTADO: Record<PrecioEstado, { etiqueta: string; clase: string }> = {
  compite: { etiqueta: "Compite", clase: "bg-emerald-100 text-emerald-800" },
  arriba_mediana: { etiqueta: "Arriba de la mediana", clase: "bg-amber-100 text-amber-800" },
  no_compite: { etiqueta: "No compite con este margen", clase: "bg-red-100 text-red-700" },
  sin_mercado: { etiqueta: "Sin comparables", clase: "bg-slate-100 text-slate-700" },
  sin_piso: { etiqueta: "Sin piso", clase: "bg-slate-100 text-slate-700" },
};

const FOTO: Record<string, { texto: string; clase: string }> = {
  mismo_modelo: { texto: "Mismo modelo (foto)", clase: "bg-emerald-100 text-emerald-800" },
  mismo_diseno: { texto: "Mismo diseño (foto)", clase: "bg-teal-100 text-teal-800" },
  diferente: { texto: "Otro producto (foto)", clase: "bg-slate-100 text-slate-500" },
  error: { texto: "Foto sin revisar", clase: "bg-slate-100 text-slate-400" },
};

const CLASE_TITULO: Record<string, string> = {
  mismo: "mismo producto",
  otro_paquete: "otra cantidad",
  otra_gama: "otra gama",
  refaccion: "refacción",
  otro_producto: "otro producto",
  dudoso: "dudoso",
};

function haceCuanto(iso: string): string {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  if (min < 60) return `hace ${min} min`;
  if (min < 1440) return `hace ${Math.round(min / 60)} h`;
  return `hace ${Math.round(min / 1440)} d`;
}

export default function SugerirPrecio({
  sku,
  titulo,
  mlCatId,
  costo,
  imagen,
  pesoKg,
  disabled,
  onUsar,
}: {
  sku: string;
  titulo: string;
  mlCatId: string | null;
  costo: number | null;
  imagen: string | null;
  /** «Peso (kg)» del Estudio (el que guarda y usa costos). Vacío → el backend usa el atributo «Peso» de Woo. */
  pesoKg: number | null;
  disabled?: boolean;
  /** Llena «Precio oferta» del Estudio; se guarda con el botón de siempre. */
  onUsar: (precio: number) => void;
}) {
  const [res, setRes] = useState<PrecioSugeridoResp | null>(null);
  const [margen, setMargen] = useState("30");
  const [terminos, setTerminos] = useState("");
  const [rapidoCargando, setRapidoCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jid, setJid] = useState<string | null>(null);
  const [prueba, setPrueba] = useState("");
  const job = useTrabajoJob<PrecioSugeridoTrabajo>(jid, precioSugeridoTrabajo, 3000);

  // Al abrir el SKU: la última consulta guardada (no se vuelve a pagar).
  useEffect(() => {
    setRes(null);
    setError(null);
    setJid(null);
    setPrueba("");
    setTerminos("");
    if (!sku) return;
    const ctl = new AbortController();
    precioSugeridoUltimo(sku, ctl.signal)
      .then((r) => {
        if (r.resultado) {
          setRes(r.resultado);
          setTerminos((r.resultado.terminos ?? []).join(", "));
          setMargen(String(Math.round((r.resultado.margen ?? 0.3) * 100)));
        }
      })
      .catch(() => { /* sin consulta previa: no es error */ });
    return () => ctl.abort();
  }, [sku]);

  // El trabajo completo terminó: su resultado pasa a ser el que se ve.
  useEffect(() => {
    const e = job.est;
    if (!e) return;
    if (e.paso === "listo" && e.resultado) {
      setRes(e.resultado);
      setTerminos((e.resultado.terminos ?? []).join(", "));
      setJid(null);
    } else if (e.paso === "error") {
      setError(e.error || "El análisis completo falló.");
      setJid(null);
    }
  }, [job.est]);

  const entrada = () => ({
    sku,
    titulo,
    ml_cat_id: mlCatId,
    costo,
    imagen,
    peso_kg: pesoKg,
    margen: Math.max(0, Number(margen) || 0) / 100,
    terminos: terminos.split(",").map((t) => t.trim()).filter(Boolean),
  });

  async function correrRapido() {
    setError(null);
    setRapidoCargando(true);
    try {
      setRes(await precioSugeridoRapido(entrada()));
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo sugerir el precio."));
    } finally {
      setRapidoCargando(false);
    }
  }

  async function correrCompleto() {
    setError(null);
    try {
      const t = await precioSugeridoCompleto(entrada());
      setJid(t.id);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo arrancar el análisis completo."));
    }
  }

  const corriendo = rapidoCargando || !!jid;
  const precioPrueba = Number(prueba) || 0;
  const utilPrueba = res && precioPrueba > 0 ? utilidadAPrecio(res, precioPrueba) : null;

  const rivales = useMemo(() => {
    if (!res) return [] as PrecioRival[];
    return [...res.rivales].sort(
      (a, b) => Number(b.en_referencia) - Number(a.en_referencia) || a.precio - b.precio,
    );
  }, [res]);

  const s = res?.sugerido;
  const d = s?.desglose;

  return (
    <section className="overflow-hidden rounded-2xl border-2 border-amber-200 bg-amber-50/40">
      <header className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-100/60 px-4 py-2">
        <TrendingUp size={16} className="text-amber-600" />
        <span className="text-sm font-bold text-amber-900">Sugerir precio</span>
        <span className="text-xs text-amber-700">Mercado Libre · lo que cobra la competencia del MISMO producto</span>
      </header>

      <div className="space-y-3 px-4 py-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs font-semibold text-slate-600">
            Margen mínimo sobre el costo
            <span className="mt-1 flex items-center gap-1">
              <input
                type="number"
                min={0}
                max={300}
                value={margen}
                onChange={(e) => setMargen(e.target.value)}
                className="w-20 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm"
              />
              <span className="text-sm text-slate-500">%</span>
            </span>
          </label>
          <button
            type="button"
            onClick={correrRapido}
            disabled={disabled || corriendo}
            title="Más vendidos de la categoría + comisión real + piso. No cuesta nada."
            className="flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm font-bold text-amber-800 hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {rapidoCargando ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}
            Rápido · gratis
          </button>
          <button
            type="button"
            onClick={correrCompleto}
            disabled={disabled || corriendo}
            title="Busca el producto en ML, separa por título y compara las FOTOS. Tarda alrededor de 1 minuto. Cuesta ~$0.02; más (hasta ~$0.10) si ML bloquea un término y Apify reintenta."
            className="flex items-center gap-1.5 rounded-lg bg-amber-600 px-3 py-2 text-sm font-bold text-white shadow-sm hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {jid ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
            Completo · $0.02–0.10
          </button>
          <label className="min-w-[240px] flex-1 text-xs font-semibold text-slate-600">
            Términos de búsqueda (completo; vacío = los propone la IA)
            <span className="mt-1 flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2">
              <Search size={13} className="text-slate-400" />
              <input
                value={terminos}
                onChange={(e) => setTerminos(e.target.value)}
                placeholder="p. ej. microfono lavalier inalambrico 2 pack"
                className="w-full bg-transparent py-1.5 text-sm outline-none"
              />
            </span>
          </label>
        </div>

        {jid && (
          <div className="flex items-center gap-2 rounded-lg bg-white px-3 py-2 text-sm text-amber-800">
            <Loader2 size={15} className="animate-spin" />
            {job.est?.paso_label ?? "Arrancando…"}
          </div>
        )}
        {(error || job.error) && (
          <div className="flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertTriangle size={14} /> {error || job.error}
          </div>
        )}

        {res && s && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <span className="text-3xl font-black text-slate-800">{mxn(s.precio)}</span>
              <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${ESTADO[s.estado].clase}`}>
                {ESTADO[s.estado].etiqueta}
              </span>
              {s.precio != null && (
                <button
                  type="button"
                  onClick={() => onUsar(s.precio as number)}
                  disabled={disabled}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  title="Llena «Precio oferta». Revisa el precio regular (tachado) y guarda con «Guardar precio y costo»."
                >
                  Usar {mxn(s.precio)} en Precio oferta
                </button>
              )}
            </div>
            <p className="text-sm text-slate-700">{s.texto}</p>

            {d && (
              <p className="text-sm text-slate-700">
                A {mxn(s.precio)} te quedan{" "}
                <strong className={d.utilidad >= 0 ? "text-emerald-700" : "text-red-700"}>{mxn(d.utilidad)}</strong> por
                pieza ({d.pct_sobre_costo} % sobre el costo)
                <span className="block text-xs text-slate-500">
                  − IVA {mxn(d.iva)} · − comisión ML {Math.round(d.pct * 1000) / 10} % {mxn(d.comision)} · − envío{" "}
                  {mxn(d.envio)} · − costo {mxn(d.costo)}
                </span>
              </p>
            )}

            <div className="grid gap-2 text-xs sm:grid-cols-2">
              <div className="rounded-lg bg-white px-3 py-2">
                <div className="font-semibold text-slate-500">Piso para ganar {Math.round(res.margen * 100)} % sobre el costo</div>
                <div className="text-base font-bold text-slate-800">{mxn(res.piso)}</div>
                <div className="text-slate-400">
                  costo {mxn(res.costo)} · envío{" "}
                  {res.envio_origen === "peso" ? `por peso (${res.peso_kg} kg)` : res.envio_origen === "panel" ? "del panel" : "sin dato"}
                </div>
              </div>
              <div className="rounded-lg bg-white px-3 py-2">
                <div className="font-semibold text-slate-500">
                  Mercado · {res.referencia ? `${res.referencia.etiqueta} (${res.referencia.n})` : "sin comparables"}
                </div>
                {res.referencia ? (
                  <>
                    <div className="text-base font-bold text-slate-800">mediana {mxn(res.referencia.mediana)}</div>
                    <div className="text-slate-400">
                      normal {mxn(res.referencia.p25)}–{mxn(res.referencia.p75)} · todo {mxn(res.referencia.min)}–
                      {mxn(res.referencia.max)}
                      {res.referencia.pocos && " · pocos datos"}
                    </div>
                  </>
                ) : res.categoria ? (
                  // Sin comparables, al menos lo que cobra la categoría: si el piso queda muy arriba, salta a la vista.
                  <>
                    <div className="text-base font-bold text-slate-800">
                      categoría: mediana {mxn(res.categoria.mediana)}
                    </div>
                    <div className="text-slate-400">
                      los {res.categoria.n} más vendidos, sin filtrar por producto · {mxn(res.categoria.min)}–
                      {mxn(res.categoria.max)}. Solo de referencia.
                    </div>
                  </>
                ) : (
                  <div className="text-slate-400">No hubo publicaciones comparables.</div>
                )}
              </div>
            </div>

            <label className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
              Probar otro precio:
              <input
                type="number"
                value={prueba}
                onChange={(e) => setPrueba(e.target.value)}
                placeholder="$"
                className="w-24 rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm"
              />
              {utilPrueba != null && res.costo ? (
                <span>
                  te quedan <strong className={utilPrueba >= 0 ? "text-emerald-700" : "text-red-700"}>{mxn(Math.round(utilPrueba * 100) / 100)}</strong>{" "}
                  ({Math.round((utilPrueba / res.costo) * 1000) / 10} % sobre el costo)
                </span>
              ) : null}
            </label>

            {!!res.avisos.length && (
              <ul className="space-y-0.5 text-[11px] text-amber-700">
                {res.avisos.map((a, i) => (
                  <li key={i}>⚠ {a}</li>
                ))}
              </ul>
            )}

            <details className="rounded-lg bg-white text-xs" open={res.modo === "completo"}>
              <summary className="cursor-pointer px-3 py-2 font-semibold text-slate-600">
                {res.modo === "completo" ? "Rivales de la búsqueda" : "Más vendidos de la categoría"} ({rivales.length}
                {res.referencia ? ` · ${rivales.filter((r) => r.en_referencia).length} en la referencia` : ""})
              </summary>
              <ul className="max-h-[420px] divide-y divide-slate-100 overflow-auto">
                {rivales.map((r) => (
                  <li key={r.id} className={`flex items-center gap-3 px-3 py-1.5 ${r.en_referencia ? "bg-emerald-50/50" : ""}`}>
                    {r.imagen ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={r.imagen} alt="" className="h-11 w-11 shrink-0 rounded object-contain" loading="lazy" />
                    ) : (
                      <div className="h-11 w-11 shrink-0 rounded bg-slate-100" />
                    )}
                    <span className="w-16 shrink-0 text-right font-bold text-slate-800">{mxn(r.precio)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-slate-700" title={r.titulo ?? ""}>{r.titulo}</span>
                      <span className="flex flex-wrap gap-1 pt-0.5">
                        {r.foto && FOTO[r.foto] && (
                          <span className={`rounded px-1.5 py-0.5 ${FOTO[r.foto].clase}`} title={r.foto_razon}>
                            {FOTO[r.foto].texto}
                            {r.foto_piezas ? ` · ${r.foto_piezas} pzs` : ""}
                          </span>
                        )}
                        {r.clase_titulo && (
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500" title={r.motivo_titulo}>
                            título: {CLASE_TITULO[r.clase_titulo] ?? r.clase_titulo}
                          </span>
                        )}
                        {r.parecido != null && (
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">parecido {Math.round(r.parecido * 100)} %</span>
                        )}
                        {r.extremo && (
                          <span className="rounded bg-red-50 px-1.5 py-0.5 text-red-600" title="Su precio cae muy lejos de la mediana de su grupo">
                            precio fuera de rango
                          </span>
                        )}
                        {(r.mas_vendido || r.fuente === "mas_vendidos") && (
                          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">más vendido</span>
                        )}
                      </span>
                    </span>
                    {r.url && (
                      <a href={r.url} target="_blank" rel="noopener noreferrer" className="shrink-0 text-slate-400 hover:text-slate-700">
                        <ExternalLink size={14} />
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </details>

            <p className="text-[11px] text-slate-400">
              {res.modo === "completo" ? "Completo" : "Rápido"} · {haceCuanto(res.consultado)}
              {res.terminos?.length ? ` · buscó «${res.terminos.join("», «")}»` : ""} · costó {usd(res.costo_consulta.total)}
              {res.costo_consulta.total > 0 &&
                ` (búsqueda ${usd(res.costo_consulta.apify)}, título ${usd(res.costo_consulta.juez)}, fotos ${usd(res.costo_consulta.fotos)})`}
              . Solo informativo: no cambia ningún precio.
            </p>
          </div>
        )}

        {!res && !corriendo && !error && (
          <p className="text-xs text-slate-500">
            <strong>Rápido</strong>: los más vendidos de la categoría, sin costo. <strong>Completo</strong>: busca el
            producto en Mercado Libre, separa los que son el mismo por título y por FOTO, y calcula el precio que compite
            sin bajar de tu margen.
          </p>
        )}
      </div>
    </section>
  );
}
