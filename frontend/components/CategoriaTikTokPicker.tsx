"use client";

/**
 * CategoriaTikTokPicker — la categoría de TikTok Shop, visible y editable.
 *
 * Hermano de `TipoAmazonPicker` y del picker de ML: muestra la que se usaría HOY
 * y permite elegir otra. La elección se guarda en el panel y **MANDA** sobre el
 * recomendador de TikTok, que falla el 49% de las veces (medido sobre 245
 * productos). Misma regla que aprendimos con `TEC-1812-NEG`.
 *
 * SOLO OFRECE HOJAS: las categorías intermedias rechazan la publicación con
 * `12052024 Category is not final category`.
 *
 * ⚠️ Lo que este picker TODAVÍA no puede avisar: si la categoría está
 * restringida (`INVITE_ONLY`). Ésas no rechazan — aceptan el producto y lo
 * dejan "en revisión" para siempre, sin error. El dato existe en TikTok pero no
 * hay dónde guardarlo hasta que se agregue la columna (pedida a Eduardo), así
 * que aquí se advierte en vez de fingir que se sabe.
 */

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, Search, Sparkles, Tag } from "lucide-react";

import {
  buscarCategoriasTikTok,
  categoriaTikTokActual,
  guardarCategoriaTikTok,
  sugerirCategoriaTikTok,
  type CategoriaTikTok,
  type SugerenciaCategoria,
} from "@/lib/api";

const ORIGEN: Record<string, { texto: string; clase: string }> = {
  panel: { texto: "elegida en el panel", clase: "bg-emerald-100 text-emerald-700" },
  canal: { texto: "la que tiene en TikTok", clase: "bg-slate-200 text-slate-600" },
};

export default function CategoriaTikTokPicker({ sku, titulo }: { sku: string; titulo?: string }) {
  const [actual, setActual] = useState<CategoriaTikTok | null>(null);
  const [origen, setOrigen] = useState<string | null>(null);
  const [sugerida, setSugerida] = useState<SugerenciaCategoria | null>(null);
  const [sugiriendo, setSugiriendo] = useState(false);
  const [q, setQ] = useState("");
  const [resultados, setResultados] = useState<CategoriaTikTok[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [guardando, setGuardando] = useState<string | null>(null);
  const [guardadoOk, setGuardadoOk] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    categoriaTikTokActual(sku, ctrl.signal)
      .then((r) => {
        setOrigen(r.origen);
        setActual(r.category_id ? { category_id: r.category_id, name: r.name, path: r.path } : null);
        // Se recomienda SIEMPRE que la categoría no la haya elegido una persona.
        // Si ya hay elección del panel no se gasta la llamada: para eso está el
        // botón "Recomendar otra".
        if (r.origen !== "panel") pedirSugerencia(ctrl.signal);
      })
      .catch(() => {});
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku]);

  function pedirSugerencia(signal?: AbortSignal) {
    setSugiriendo(true);
    sugerirCategoriaTikTok(sku, titulo, signal)
      .then(setSugerida)
      .catch(() => setSugerida(null))
      .finally(() => setSugiriendo(false));
  }

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const term = q.trim();
    if (term.length < 2) {
      setResultados([]);
      return;
    }
    timer.current = setTimeout(() => {
      setBuscando(true);
      buscarCategoriasTikTok(term)
        .then((r) => setResultados(r.resultados))
        .catch(() => setResultados([]))
        .finally(() => setBuscando(false));
    }, 400);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [q]);

  async function elegir(c: CategoriaTikTok) {
    setGuardando(c.category_id);
    setGuardadoOk(false);
    try {
      await guardarCategoriaTikTok(sku, c.category_id);
      setActual(c);
      setOrigen("panel");
      setResultados([]);
      setQ("");
      setGuardadoOk(true);
      setTimeout(() => setGuardadoOk(false), 2500);
    } catch {
      /* se conserva la anterior */
    } finally {
      setGuardando(null);
    }
  }

  const et = origen ? ORIGEN[origen] : null;
  const sinResultados = q.trim().length >= 2 && !buscando && resultados.length === 0;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-rose-100 text-rose-600">
          <Tag size={15} />
        </span>
        <span className="text-sm font-bold text-slate-800">Categoría (TikTok Shop)</span>
        {actual ? (
          <span className="rounded-full bg-slate-900 px-2.5 py-0.5 text-[11px] font-bold text-white">
            {actual.name ?? actual.category_id}
          </span>
        ) : (
          <span className="text-xs text-slate-400">sin categoría</span>
        )}
        {et && (
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${et.clase}`}>
            {et.texto}
          </span>
        )}
        {guardadoOk && (
          <span className="flex items-center gap-1 text-xs font-bold text-emerald-600">
            <Check size={13} /> guardado
          </span>
        )}
      </div>

      {actual?.path && (
        <p className="mt-1.5 text-xs text-slate-500">{actual.path}</p>
      )}

      {/* SIN categoría: el hueco se explica, no se deja en blanco. */}
      {!actual && !sugiriendo && !sugerida?.category_id && (
        <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Este producto <strong>no está publicado en TikTok</strong>, así que el canal
          todavía no le asignó categoría. Elígela aquí y se usará al publicar.
          {sugerida?.motivo ? ` ${sugerida.motivo}` : ""}
        </p>
      )}

      {/* LA RECOMENDACIÓN. Se muestra, no se guarda: guardarla sola la volvería
          indistinguible de una elección humana, y toda la precedencia del panel
          se apoya en esa diferencia. Se guarda cuando alguien aprieta "Usar". */}
      {sugiriendo && (
        <p className="mt-2 flex items-center gap-2 text-xs text-slate-500">
          <Loader2 size={13} className="animate-spin" /> Buscando una categoría recomendada…
        </p>
      )}
      {!sugiriendo && sugerida?.category_id && (
        <div className="mt-2 rounded-xl border border-rose-200 bg-rose-50/60 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Sparkles size={14} className="text-rose-500" />
            <span className="text-[11px] font-bold uppercase tracking-wide text-rose-600">
              Recomendada
            </span>
            <span className="text-sm font-semibold text-slate-800">{sugerida.name}</span>
            {sugerida.confianza != null && (
              <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-bold text-slate-500">
                confianza {Math.round(sugerida.confianza * 100)}%
              </span>
            )}
            <span className="text-[10px] uppercase tracking-wide text-slate-400">
              {sugerida.origen}
            </span>
          </div>
          {sugerida.path && <p className="mt-1 text-xs text-slate-500">{sugerida.path}</p>}
          {sugerida.motivo && <p className="mt-1 text-xs text-slate-600">{sugerida.motivo}</p>}
          <button
            type="button"
            onClick={() => elegir({ category_id: sugerida.category_id, name: sugerida.name, path: sugerida.path })}
            disabled={guardando !== null}
            className="mt-2 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-bold text-white transition-colors hover:bg-slate-700 disabled:opacity-60"
          >
            {guardando === sugerida.category_id ? "Guardando…" : "Usar esta categoría"}
          </button>
        </div>
      )}
      {!sugiriendo && origen === "panel" && (
        <button
          type="button"
          onClick={() => pedirSugerencia()}
          className="mt-2 text-xs font-semibold text-rose-600 hover:underline"
        >
          Recomendar otra categoría
        </button>
      )}

      <div className="relative mt-3">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Buscar categoría… (ej. termo, motosierra, auriculares)"
          className="w-full rounded-lg border border-slate-200 py-2 pl-9 pr-3 text-sm outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-rose-200"
        />
        {buscando && (
          <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-slate-400" />
        )}
        {resultados.length > 0 && (
          <div className="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-xl border border-slate-200 bg-white shadow-lg">
            {resultados.map((c) => (
              <button
                key={c.category_id}
                type="button"
                onClick={() => elegir(c)}
                disabled={guardando !== null}
                className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-rose-50"
              >
                <span className="text-sm font-medium text-slate-800">{c.name}</span>
                <span className="flex w-full items-center justify-between gap-2">
                  <span className="truncate text-[11px] text-slate-400">{c.path}</span>
                  {guardando === c.category_id && <Loader2 size={12} className="animate-spin" />}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* TikTok tiene su propio vocabulario y no siempre es el que uno teclea:
          no existe "Audífonos", existe "Auriculares". Decirlo evita concluir
          que la categoría no está en el catálogo. */}
      {sinResultados && (
        <p className="mt-2 text-xs text-slate-500">
          Sin resultados. TikTok usa su propio vocabulario — prueba con otra palabra
          (por ejemplo <strong>auriculares</strong> en vez de audífonos). Solo se
          muestran categorías finales, que son las únicas donde se puede publicar.
        </p>
      )}

      <p className="mt-2 text-[11px] text-slate-400">
        Algunas categorías están restringidas por TikTok y <strong>no avisan</strong>:
        aceptan el producto y lo dejan en revisión indefinidamente. Ese dato todavía
        no se puede consultar desde el panel.
      </p>
    </section>
  );
}
