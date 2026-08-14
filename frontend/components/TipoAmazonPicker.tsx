"use client";

/**
 * TipoAmazonPicker — la "categoría" de Amazon (product type), visible y
 * editable como el picker de categorías de ML.
 *
 * Muestra el tipo que se usaría HOY (panel > histórico > auto) y permite
 * buscar otro con el buscador de relevancia de Amazon. La elección se guarda
 * como meta `amz_product_type` en Woo y MANDA sobre el detector automático —
 * misma regla que aprendimos con las categorías de ML (caso TEC-1812-NEG).
 */

import { useEffect, useRef, useState } from "react";
import { Boxes, Check, Loader2, Search } from "lucide-react";

import {
  buscarTiposAmazon,
  guardarTipoAmazon,
  sugerirTipoAmazon,
  tipoAmazonActual,
  type SugerenciaTipoAmazon,
  type TipoAmazon,
} from "@/lib/api";

const ORIGEN_LABEL: Record<string, { texto: string; clase: string }> = {
  panel: { texto: "elegido en el panel", clase: "bg-emerald-100 text-emerald-700" },
  historial: { texto: "histórico de Amazon", clase: "bg-slate-200 text-slate-600" },
  auto: { texto: "se detecta al publicar", clase: "bg-amber-100 text-amber-700" },
};

export default function TipoAmazonPicker({
  sku,
  wcId,
  titulo,
}: {
  sku: string;
  wcId: number;
  titulo?: string | null;
}) {
  const [actual, setActual] = useState<string | null>(null);
  const [origen, setOrigen] = useState<string>("auto");
  const [q, setQ] = useState("");
  const [resultados, setResultados] = useState<TipoAmazon[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [guardando, setGuardando] = useState<string | null>(null);
  const [guardadoOk, setGuardadoOk] = useState(false);
  // LA SUGERENCIA. El backend sabía proponer un tipo desde v0.137.0 y nadie se
  // lo pedía: el picker solo buscaba, así que quien abría el Estudio tenía que
  // adivinar el término —en inglés— o dejar que el detector automático eligiera
  // AL PUBLICAR, que es el peor momento para enterarse de que eligió mal.
  const [sugerido, setSugerido] = useState<SugerenciaTipoAmazon | null>(null);
  const [sugiriendo, setSugiriendo] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    tipoAmazonActual(sku, wcId, ctrl.signal)
      .then((r) => {
        setActual(r.product_type);
        setOrigen(r.origen);
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [sku, wcId]);

  function pedirSugerencia(signal?: AbortSignal) {
    setSugiriendo(true);
    sugerirTipoAmazon(sku, titulo || undefined, signal)
      .then(setSugerido)
      .catch(() => setSugerido(null))
      .finally(() => setSugiriendo(false));
  }

  // Solo si NO hay elección humana: si alguien ya fijó el tipo, proponer otro
  // sería ruido — y su elección manda igual (regla 2 de la casa).
  useEffect(() => {
    if (origen === "panel") return;
    const ctrl = new AbortController();
    pedirSugerencia(ctrl.signal);
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku, origen]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const term = q.trim();
    if (term.length < 2) {
      setResultados([]);
      return;
    }
    timer.current = setTimeout(() => {
      setBuscando(true);
      buscarTiposAmazon(term)
        .then((r) => setResultados(r.tipos))
        .catch(() => setResultados([]))
        .finally(() => setBuscando(false));
    }, 450);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [q]);

  async function elegir(t: TipoAmazon) {
    setGuardando(t.name);
    setGuardadoOk(false);
    try {
      await guardarTipoAmazon(sku, wcId, t.name);
      setActual(t.name);
      setOrigen("panel");
      setResultados([]);
      setQ("");
      setGuardadoOk(true);
      setTimeout(() => setGuardadoOk(false), 2500);
    } catch {
      /* el chip conserva el valor anterior */
    } finally {
      setGuardando(null);
    }
  }

  const et = ORIGEN_LABEL[origen] ?? ORIGEN_LABEL.auto;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-orange-100 text-orange-600">
          <Boxes size={15} />
        </span>
        <span className="text-sm font-bold text-slate-800">Tipo de producto (Amazon)</span>
        {actual ? (
          <span className="rounded-full bg-slate-900 px-2.5 py-0.5 font-mono text-[11px] font-bold text-white">
            {actual}
          </span>
        ) : (
          <span className="text-xs text-slate-400">sin tipo fijado</span>
        )}
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${et.clase}`}>
          {et.texto}
        </span>
        {guardadoOk && (
          <span className="flex items-center gap-1 text-xs font-bold text-emerald-600">
            <Check size={13} /> guardado
          </span>
        )}
      </div>

      {sugiriendo && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-slate-500">
          <Loader2 size={13} className="animate-spin" /> Buscando un tipo recomendado…
        </p>
      )}
      {!sugiriendo && sugerido?.product_type && origen !== "panel" && (
        <div className="mt-3 rounded-xl border border-orange-200 bg-orange-50/60 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-orange-600 px-2 py-0.5 text-[10px] font-bold uppercase text-white">
              sugerido
            </span>
            <span className="font-mono text-xs font-bold text-slate-800">
              {sugerido.product_type}
            </span>
            {sugerido.origen && (
              <span className="text-[10px] uppercase tracking-wide text-slate-500">
                {sugerido.origen}
              </span>
            )}
          </div>
          {sugerido.motivo && (
            <p className="mt-1 text-xs text-slate-600">{sugerido.motivo}</p>
          )}
          <button
            type="button"
            onClick={() => elegir({ name: sugerido.product_type as string, label: sugerido.product_type as string })}
            disabled={guardando === sugerido.product_type}
            className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-orange-300 px-2.5 py-1 text-xs font-semibold text-orange-700 hover:bg-orange-100 disabled:opacity-50"
          >
            {guardando === sugerido.product_type ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Check size={12} />
            )}
            Usar este tipo
          </button>
        </div>
      )}

      <div className="relative mt-3">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Buscar otro tipo… (ej. guantes seguridad, lijadora, silla)"
          className="w-full rounded-lg border border-slate-200 py-2 pl-9 pr-3 text-sm outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-orange-200"
        />
        {buscando && (
          <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-slate-400" />
        )}
        {resultados.length > 0 && (
          <div className="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-xl border border-slate-200 bg-white shadow-lg">
            {resultados.map((t) => (
              <button
                key={t.name}
                type="button"
                onClick={() => elegir(t)}
                disabled={guardando !== null}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-orange-50"
              >
                <span className="font-medium text-slate-800">{t.label}</span>
                <span className="flex items-center gap-2">
                  <span className="font-mono text-[10px] text-slate-400">{t.name}</span>
                  {guardando === t.name && <Loader2 size={12} className="animate-spin" />}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        Tu elección manda sobre la detección automática y se usa al publicar/actualizar en Amazon.
      </p>
    </section>
  );
}
