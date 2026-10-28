"use client";

/**
 * SugerirPrecio — «Sugerir precio» del Estudio (v0.686.0; simplificado en v0.696.0).
 *
 * De dónde sale el precio:
 *   1. El CATÁLOGO MAESTRO (gratis, al abrir el SKU): la MEDIA de Mercado Libre
 *      con su mínimo y máximo, y lo que cobra Amazon por el mismo producto.
 *   2. Si el SKU no está en el catálogo, el botón COMPLETO ($0.01–0.10): busca en
 *      ML (juez por título y por FOTO) y en Amazon (SP-API, gratis) y queda
 *      guardado. Si ya hay una consulta completa, esa manda: es más reciente.
 *
 * La regla (Brandon, 28-oct): un poco abajo de la media de ML, nunca arriba de
 * la media ni del máximo → media × 0.97, hacia abajo al peso.
 *
 * Cada marketplace tiene su ventana con las publicaciones que se leyeron y cuáles
 * hicieron match. «Medidas de Amazon» trae el paquete de los que hicieron match.
 *
 * Solo sugiere. «Usar en Precio oferta» llena el campo y se guarda con
 * «Guardar precio y costo»; «Usar estas medidas» llena peso y medidas igual.
 */

import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, Loader2, Package, Sparkles, TrendingUp, X } from "lucide-react";

import {
  medidasAmazon,
  mensajeDeError,
  precioCatalogo,
  precioSugeridoCompleto,
  precioSugeridoTrabajo,
  precioSugeridoUltimo,
  type MedidasAmazon,
  type PrecioAmazon,
  type PrecioCatalogo,
  type PrecioSugeridoResp,
  type PrecioSugeridoTrabajo,
} from "@/lib/api";
import { useTrabajoJob } from "@/components/resolver/useTrabajoJob";

/** Un poco abajo de la media de ML (regla de Brandon, 28-oct). */
const FACTOR_BAJO_MEDIA = 0.97;

const mxn = (v: number | null | undefined) =>
  v == null ? "—" : `$${v.toLocaleString("es-MX", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
const usd = (v: number) => `$${v.toFixed(v < 0.1 ? 3 : 2)} USD`;

const FUENTES_ML: Record<string, string> = {
  banda_v3: "más vendidos de la categoría",
  keyword_amazon: "Amazon ÷ 1.443",
  keyword_ml: "búsqueda en ML",
};

const MATCH: Record<string, { texto: string; clase: string }> = {
  match: { texto: "Match", clase: "bg-emerald-100 text-emerald-800" },
  no_match: { texto: "No match", clase: "bg-slate-100 text-slate-500" },
  sin_dato: { texto: "Sin veredicto", clase: "bg-amber-50 text-amber-700" },
};

function haceCuanto(iso: string): string {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  if (min < 60) return `hace ${min} min`;
  if (min < 1440) return `hace ${Math.round(min / 60)} h`;
  return `hace ${Math.round(min / 1440)} d`;
}

const dia = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("es-MX", { day: "numeric", month: "short" }) : "";

type Stats = { min: number | null; media: number | null; max: number | null };

/** Una publicación en la ventana de «lo que se leyó», sea de ML o de Amazon. */
type Fila = {
  id: string;
  titulo: string;
  /** null en la ventana de medidas: ahí no importa el precio. */
  precio: number | null;
  url?: string | null;
  match: "match" | "no_match" | "sin_dato";
  nota?: string;
};

type Ventana = { titulo: string; resumen: string; filas: Fila[] };

/** El bloque de un marketplace: mínimo, media y máximo, de dónde salen y su ventana. */
function Tarjeta({
  nombre,
  stats,
  detalle,
  pie,
  ventana,
  onVer,
}: {
  nombre: string;
  stats: Stats | null;
  detalle: string;
  pie: string;
  ventana: Ventana | null;
  onVer: (v: Ventana) => void;
}) {
  return (
    <div className="rounded-lg bg-white px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-bold text-slate-700">{nombre}</span>
        {ventana && ventana.filas.length > 0 && (
          <button
            type="button"
            onClick={() => onVer(ventana)}
            className="rounded-md border border-slate-200 px-2 py-0.5 font-semibold text-slate-600 hover:bg-slate-50"
          >
            Ver publicaciones ({ventana.filas.length})
          </button>
        )}
      </div>
      {stats ? (
        <div className="mt-1.5 grid grid-cols-3 gap-2 text-center">
          {(["min", "media", "max"] as const).map((k) => (
            <div key={k}>
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                {k === "min" ? "Mín" : k === "media" ? "Media" : "Máx"}
              </div>
              <div className={`tabular-nums ${k === "media" ? "text-base font-black text-slate-800" : "text-sm font-bold text-slate-600"}`}>
                {mxn(stats[k])}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-1.5 text-slate-400">Sin precio.</div>
      )}
      {detalle && <div className="mt-1.5 text-slate-500">{detalle}</div>}
      <div className="mt-0.5 text-[11px] text-slate-400">{pie}</div>
    </div>
  );
}

function ventanaAmazon(a: PrecioAmazon): Ventana {
  const cuenta = (m: string) => a.publicaciones.filter((p) => p.match === m).length;
  const partes = [`${cuenta("match")} hicieron match`, `${cuenta("no_match")} no`];
  if (cuenta("sin_dato")) partes.push(`${cuenta("sin_dato")} sin veredicto guardado`);
  return {
    titulo: `Amazon · «${a.q}»`,
    resumen: partes.join(" · "),
    filas: a.publicaciones.map((p) => ({
      id: p.id,
      titulo: p.titulo,
      precio: p.precio,
      url: p.url,
      match: p.match,
      nota: [p.unidades ? `paquete de ${p.unidades}` : "", p.motivo ?? ""].filter(Boolean).join(" · "),
    })),
  };
}

function ventanaML(r: PrecioSugeridoResp): Ventana {
  const filas: Fila[] = [...r.rivales]
    .sort((a, b) => Number(b.en_referencia) - Number(a.en_referencia) || a.precio - b.precio)
    .map((x) => ({
      id: x.id,
      titulo: x.titulo ?? "",
      precio: x.precio,
      url: x.url,
      match: x.en_referencia ? "match" : "no_match",
      nota: [
        x.foto === "mismo_modelo" ? "mismo modelo por foto" : x.foto === "mismo_diseno" ? "mismo diseño por foto" : "",
        x.clase_titulo && x.clase_titulo !== "mismo" ? `título: ${x.clase_titulo.replace("_", " ")}` : "",
        x.extremo ? "precio fuera de rango" : "",
      ].filter(Boolean).join(" · "),
    }));
  const n = filas.filter((f) => f.match === "match").length;
  return {
    titulo: `Mercado Libre · «${(r.terminos ?? []).join("», «")}»`,
    resumen: `${n} hicieron match${r.referencia ? ` (${r.referencia.etiqueta.toLowerCase()})` : ""} · ${filas.length - n} no`,
    filas,
  };
}

function VentanaPublicaciones({ v, onCerrar }: { v: Ventana; onCerrar: () => void }) {
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && onCerrar();
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [onCerrar]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={onCerrar}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={v.titulo}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-bold text-slate-800">{v.titulo}</div>
            <div className="text-xs text-slate-500">{v.resumen}</div>
          </div>
          <button type="button" onClick={onCerrar} aria-label="Cerrar" className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
            <X size={18} />
          </button>
        </header>
        <ul className="divide-y divide-slate-100 overflow-auto text-xs">
          {v.filas.map((f) => (
            <li key={f.id} className="flex items-center gap-3 px-4 py-2">
              <span className={`w-24 shrink-0 rounded px-1.5 py-0.5 text-center font-semibold ${MATCH[f.match].clase}`}>
                {MATCH[f.match].texto}
              </span>
              {f.precio != null && <span className="w-20 shrink-0 text-right font-bold tabular-nums text-slate-800">{mxn(f.precio)}</span>}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-slate-700" title={f.titulo}>{f.titulo}</span>
                {f.nota && <span className="block truncate text-[11px] text-slate-400">{f.nota}</span>}
              </span>
              {f.url && (
                <a href={f.url} target="_blank" rel="noopener noreferrer" className="shrink-0 text-slate-400 hover:text-slate-700" title="Abrir publicación">
                  <ExternalLink size={14} />
                </a>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function SugerirPrecio({
  sku,
  titulo,
  mlCatId,
  imagen,
  disabled,
  onUsar,
  onUsarMedidas,
}: {
  sku: string;
  titulo: string;
  mlCatId: string | null;
  imagen: string | null;
  disabled?: boolean;
  /** Llena «Precio oferta» del Estudio; se guarda con el botón de siempre. */
  onUsar: (precio: number) => void;
  /** Llena peso y largo × ancho × alto del Estudio; se guardan con el botón de siempre. */
  onUsarMedidas: (m: { peso_kg: number; largo_cm: number; ancho_cm: number; alto_cm: number }) => void;
}) {
  const [cat, setCat] = useState<PrecioCatalogo | null>(null);
  const [res, setRes] = useState<PrecioSugeridoResp | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jid, setJid] = useState<string | null>(null);
  const [ventana, setVentana] = useState<Ventana | null>(null);
  const [medidas, setMedidas] = useState<MedidasAmazon | null>(null);
  const [midiendo, setMidiendo] = useState(false);
  const job = useTrabajoJob<PrecioSugeridoTrabajo>(jid, precioSugeridoTrabajo, 3000);

  // Al abrir el SKU: el catálogo maestro y la última consulta completa (no se vuelve a pagar).
  useEffect(() => {
    setCat(null);
    setRes(null);
    setError(null);
    setJid(null);
    setMedidas(null);
    setVentana(null);
    if (!sku) return;
    const ctl = new AbortController();
    setCargando(true);
    Promise.allSettled([precioCatalogo(sku, ctl.signal), precioSugeridoUltimo(sku, ctl.signal)])
      .then(([c, u]) => {
        if (ctl.signal.aborted) return;
        if (c.status === "fulfilled") setCat(c.value);
        if (u.status === "fulfilled" && u.value.resultado) setRes(u.value.resultado);
      })
      .finally(() => !ctl.signal.aborted && setCargando(false));
    return () => ctl.abort();
  }, [sku]);

  // El trabajo completo terminó: su resultado pasa a ser el que se ve.
  useEffect(() => {
    const e = job.est;
    if (!e) return;
    if (e.paso === "listo" && e.resultado) {
      setRes(e.resultado);
      setMedidas(null);
      setJid(null);
    } else if (e.paso === "error") {
      setError(e.error || "El análisis completo falló.");
      setJid(null);
    }
  }, [job.est]);

  async function correrCompleto() {
    setError(null);
    try {
      const t = await precioSugeridoCompleto({ sku, titulo, ml_cat_id: mlCatId, imagen });
      setJid(t.id);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo arrancar el análisis completo."));
    }
  }

  // Mercado Libre: la búsqueda completa manda si encontró comparables; si no, el catálogo maestro.
  const ref = res?.referencia ?? null;
  const ml: (Stats & { origen: "completo" | "catalogo" }) | null = ref
    ? { min: ref.min, media: ref.media, max: ref.max, origen: "completo" }
    : cat?.ml
      ? { min: cat.ml.min ?? cat.ml.media, media: cat.ml.media, max: cat.ml.max ?? cat.ml.media, origen: "catalogo" }
      : null;
  // Amazon: igual, la consulta completa si trajo Amazon; si no, el catálogo.
  const az = res?.amazon ?? cat?.amazon ?? null;
  const azDeCompleto = !!res?.amazon;
  const azStats: Stats | null = az?.media != null ? { min: az.min ?? null, media: az.media, max: az.max ?? null } : null;

  const sugerido = ml?.media ? Math.floor(ml.media * FACTOR_BAJO_MEDIA) : null;
  const corriendo = !!jid;

  async function traerMedidas() {
    if (!az) return;
    setError(null);
    setMidiendo(true);
    try {
      const asins = az.publicaciones.filter((p) => p.match === "match").map((p) => p.id).slice(0, 10);
      setMedidas(await medidasAmazon({ sku, asins, q: az.q }));
    } catch (e) {
      setError(mensajeDeError(e, "No se pudieron traer las medidas de Amazon."));
    } finally {
      setMidiendo(false);
    }
  }

  const fuentesML = cat?.ml
    ? Object.entries(cat.ml.fuentes)
        .map(([k, v]) => `${FUENTES_ML[k] ?? k} ${mxn(v)}`)
        .join(" · ") || (cat.ml.fuente === "exacto" ? "precio exacto revisado" : "")
    : "";
  const m = medidas?.sugeridas;

  return (
    <section className="overflow-hidden rounded-2xl border-2 border-amber-200 bg-amber-50/40">
      <header className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-100/60 px-4 py-2">
        <TrendingUp size={16} className="text-amber-600" />
        <span className="text-sm font-bold text-amber-900">Sugerir precio</span>
        <button
          type="button"
          onClick={correrCompleto}
          disabled={disabled || corriendo}
          title={
            "Busca el producto en Mercado Libre (por título y por FOTO) y en Amazon, y guarda el resultado. " +
            "~$0.01 y ~30 s si la búsqueda de ML ya está guardada; ~$0.05 y 1–1.5 min si hay que buscar; " +
            "hasta ~$0.10 si ML bloquea y se reintenta. Amazon no cuesta."
          }
          className="ml-auto flex items-center gap-1.5 rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-bold text-white shadow-sm hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {corriendo ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
          Completo · $0.01–0.10
        </button>
      </header>

      <div className="space-y-3 px-4 py-3">
        {corriendo && (
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

        {cargando ? (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Loader2 size={13} className="animate-spin" /> Leyendo el catálogo maestro…
          </div>
        ) : sugerido != null && ml ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="text-3xl font-black text-slate-800">{mxn(sugerido)}</span>
            <span className="text-xs text-slate-500">un poco abajo de la media de Mercado Libre ({mxn(ml.media)})</span>
            <span className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => onUsar(sugerido)}
                disabled={disabled}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                title="Llena «Precio oferta». Revisa el precio regular (tachado) y guarda con «Guardar precio y costo»."
              >
                Usar {mxn(sugerido)} en Precio oferta
              </button>
              <button
                type="button"
                onClick={traerMedidas}
                disabled={disabled || midiendo || !az}
                className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                title={az ? "Medidas del paquete de las publicaciones de Amazon que hicieron match (gratis)" : "Sin datos de Amazon: corre Completo"}
              >
                {midiendo ? <Loader2 size={13} className="animate-spin" /> : <Package size={13} />}
                Medidas de Amazon
              </button>
            </span>
          </div>
        ) : (
          <p className="text-sm text-slate-600">
            {cat?.ml ? "" : "Sin precio de Mercado Libre en el catálogo maestro. "}
            {res && !ref ? "La búsqueda completa no encontró comparables en Mercado Libre." : "Presiona Completo para buscarlo."}
          </p>
        )}

        {medidas && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-white px-3 py-2 text-xs text-slate-600">
            {m ? (
              <>
                <span>
                  Paquete sugerido:{" "}
                  <strong className="text-slate-800">
                    {m.largo_cm} × {m.ancho_cm} × {m.alto_cm} cm · {m.peso_kg} kg
                  </strong>
                </span>
                <span className="text-slate-400">
                  mediana de {medidas.n} {medidas.origen === "match" ? "que hicieron match" : `de la búsqueda «${medidas.q}», sin filtrar`} en Amazon
                </span>
                <button
                  type="button"
                  onClick={() => onUsarMedidas(m)}
                  disabled={disabled}
                  className="rounded-md border border-slate-300 px-2 py-0.5 font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  title="Llena Peso y Largo × Ancho × Alto del Estudio; se guardan con el botón de siempre"
                >
                  Usar estas medidas
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setVentana({
                      titulo: "Medidas de Amazon (paquete)",
                      resumen: `${medidas.n} publicaciones medidas`,
                      filas: medidas.publicaciones.map((p) => ({
                        id: p.asin,
                        titulo: p.titulo,
                        precio: null,
                        url: p.url,
                        match: medidas.origen === "match" ? "match" : "sin_dato",
                        nota: `${p.largo_cm} × ${p.ancho_cm} × ${p.alto_cm} cm · ${p.peso_kg} kg`,
                      })),
                    })
                  }
                  className="font-semibold text-slate-500 underline hover:text-slate-700"
                >
                  ver de cuáles
                </button>
              </>
            ) : (
              <span>Amazon no trae medidas de paquete para estas publicaciones.</span>
            )}
          </div>
        )}

        <div className="grid gap-2 sm:grid-cols-2">
          <Tarjeta
            nombre="Mercado Libre"
            stats={ml}
            detalle={
              ml?.origen === "completo"
                ? `${ref?.n ?? 0} comparables · ${ref?.etiqueta?.toLowerCase() ?? ""}` +
                  (cat?.ml ? ` · catálogo: media ${mxn(cat.ml.media)}` : "")
                : fuentesML
            }
            pie={
              ml?.origen === "completo" && res
                ? `Completo · ${haceCuanto(res.consultado)}`
                : cat?.ml
                  ? `Catálogo maestro · ${dia(cat.fecha_ml)}`
                  : ""
            }
            ventana={res && res.rivales.length ? ventanaML(res) : null}
            onVer={setVentana}
          />
          <Tarjeta
            nombre="Amazon"
            stats={azStats}
            detalle={az ? `${az.n ?? 0} comparables de ${az.vistos} leídas · «${az.q}»` : ""}
            pie={
              az ? (azDeCompleto && res ? `Completo · ${haceCuanto(res.consultado)}` : `Catálogo maestro · ${dia(cat?.fecha_amazon)}`) : ""
            }
            ventana={az ? ventanaAmazon(az) : null}
            onVer={setVentana}
          />
        </div>

        {res && (
          <p className="text-[11px] text-slate-400">
            Última consulta completa {haceCuanto(res.consultado)} · costó {usd(res.costo_consulta.total)}
            {res.costo_consulta.apify > 0 && res.costo_consulta.apify_final === false &&
              " (preliminar: Apify cobra el proxy unos minutos después)"}
            . Solo informativo: no cambia ningún precio.
          </p>
        )}
      </div>

      {ventana && <VentanaPublicaciones v={ventana} onCerrar={() => setVentana(null)} />}
    </section>
  );
}
