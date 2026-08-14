"use client";

/**
 * CategoriaTemuPicker — la categoría de Temu del producto: la sugiere, la
 * muestra y deja cambiarla ANTES de publicar.
 *
 * Hermano de `CategoriaTikTokPicker` y del `TipoAmazonPicker`, y en Temu es el
 * que más falta hace: la categoría no es solo dónde aparece el producto, es la
 * que DETERMINA qué atributos existen. Sin hoja elegida no hay contenido que
 * generar ni alta que mandar — el publicador se detiene ahí a propósito.
 *
 * LA SUGERENCIA NO SE GUARDA SOLA. Se propone y una persona la acepta; recién
 * entonces se escribe con `source='panel'`. Guardarla sola la volvería
 * indistinguible de una elección humana, y toda la precedencia del panel se
 * apoya en esa diferencia (regla 2 de la casa).
 *
 * Cómo se arma la sugerencia: Temu propone candidatas con su recomendador y la
 * IA elige entre ellas **con permiso de decir que ninguna sirve**. Medido sobre
 * 89 productos: corrigió la primera opción de Temu en el 37% y apartó 11 que no
 * encajaban en ninguna (un palillo para cabello que iba a "Tenedores").
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Loader2, Search, Sparkles, Tag } from "lucide-react";

import {
  buscarCategoriasTemu,
  categoriaTemuActual,
  guardarCategoriaTemu,
  sugerirCategoriaTemu,
  type CategoriaTikTok,
  type SugerenciaTemu,
} from "@/lib/api";

export default function CategoriaTemuPicker({
  sku,
  titulo,
}: {
  sku: string;
  titulo?: string | null;
}) {
  const [actual, setActual] = useState<(CategoriaTikTok & { origen: string | null }) | null>(null);
  const [sug, setSug] = useState<SugerenciaTemu | null>(null);
  const [cargandoSug, setCargandoSug] = useState(false);
  const [q, setQ] = useState("");
  const [resultados, setResultados] = useState<CategoriaTikTok[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [guardando, setGuardando] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const recargar = useCallback(() => {
    categoriaTemuActual(sku).then(setActual).catch(() => setActual(null));
  }, [sku]);

  useEffect(() => {
    recargar();
    setSug(null);
    setQ("");
    setResultados([]);
  }, [sku, recargar]);

  // Búsqueda con freno: sin esto cada tecla dispara una consulta.
  useEffect(() => {
    if (q.trim().length < 2) {
      setResultados([]);
      return;
    }
    const t = setTimeout(() => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setBuscando(true);
      buscarCategoriasTemu(q.trim(), ctrl.signal)
        .then((r) => setResultados(r.resultados || []))
        .catch(() => setResultados([]))
        .finally(() => setBuscando(false));
    }, 350);
    return () => clearTimeout(t);
  }, [q]);

  const pedirSugerencia = async () => {
    setCargandoSug(true);
    setError(null);
    try {
      setSug(await sugerirCategoriaTemu(sku, titulo || undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo consultar el recomendador.");
    } finally {
      setCargandoSug(false);
    }
  };

  const elegir = async (categoriaId: string) => {
    setGuardando(categoriaId);
    setError(null);
    try {
      await guardarCategoriaTemu(sku, categoriaId);
      recargar();
      setQ("");
      setResultados([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo guardar la categoría.");
    } finally {
      setGuardando(null);
    }
  };

  return (
    <section className="overflow-hidden rounded-2xl border-2 border-orange-200 bg-orange-50/40">
      <header className="flex items-center gap-2 border-b border-orange-200 bg-orange-100/60 px-4 py-2">
        <Tag size={16} className="text-orange-600" />
        <span className="text-sm font-bold text-orange-800">Categoría de Temu</span>
        {actual?.origen === "panel" && (
          <span className="rounded-full bg-orange-600 px-2 py-0.5 text-[10px] font-bold uppercase text-white">
            elegida aquí
          </span>
        )}
        {actual?.origen === "canal" && (
          <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-600">
            la que tiene publicada
          </span>
        )}
      </header>

      <div className="space-y-3 p-4">
        {/* La actual */}
        {actual?.category_id ? (
          <p className="text-sm text-slate-700">
            <span className="font-mono text-xs text-slate-500">{actual.category_id}</span>
            <span className="mx-1.5 text-slate-300">·</span>
            {actual.path || actual.name}
          </p>
        ) : (
          <p className="text-sm text-amber-700">
            Sin categoría. En Temu la categoría decide qué atributos existen: sin
            elegirla no se puede generar contenido ni publicar.
          </p>
        )}

        {/* Recomendador */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={pedirSugerencia}
            disabled={cargandoSug}
            className="inline-flex items-center gap-1.5 rounded-lg bg-orange-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-orange-700 disabled:opacity-50"
          >
            {cargandoSug ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            Sugerir categoría
          </button>
          <span className="text-[11px] text-slate-500">
            Temu propone y la IA elige — puede decir que ninguna encaja.
          </span>
        </div>

        {sug && !sug.ok && (
          <p className="text-xs text-amber-700">{sug.motivo}</p>
        )}
        {sug?.ok && sug.ninguna && (
          <p className="text-xs text-amber-700">
            La IA descartó todas las candidatas{sug.razon ? `: ${sug.razon}` : "."} Búscala a mano abajo.
          </p>
        )}
        {sug?.ok && sug.sugerida && (
          <div className="rounded-xl border border-orange-200 bg-white p-3">
            <p className="text-sm font-semibold text-slate-800">{sug.sugerida.path || sug.sugerida.name}</p>
            {sug.razon && <p className="mt-0.5 text-[11px] text-slate-500">{sug.razon}</p>}
            <button
              type="button"
              onClick={() => elegir(sug.sugerida!.category_id)}
              disabled={guardando === sug.sugerida.category_id}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-orange-300 px-2.5 py-1 text-xs font-semibold text-orange-700 hover:bg-orange-50 disabled:opacity-50"
            >
              {guardando === sug.sugerida.category_id
                ? <Loader2 size={12} className="animate-spin" />
                : <Check size={12} />}
              Usar esta
            </button>
          </div>
        )}

        {/* Cambiarla a mano */}
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar otra categoría… (ej. casco, plastilina, organizador)"
            className="w-full rounded-lg border border-slate-200 py-2 pl-8 pr-3 text-sm outline-none focus:border-orange-400"
          />
          {buscando && (
            <Loader2 size={14} className="absolute right-2.5 top-2.5 animate-spin text-slate-400" />
          )}
        </div>

        {resultados.length > 0 && (
          <ul className="max-h-56 space-y-1 overflow-y-auto">
            {resultados.map((c) => (
              <li key={c.category_id}>
                <button
                  type="button"
                  onClick={() => elegir(c.category_id)}
                  disabled={guardando === c.category_id}
                  className="w-full rounded-lg px-2 py-1.5 text-left text-xs text-slate-700 hover:bg-orange-50 disabled:opacity-50"
                >
                  <span className="font-mono text-[10px] text-slate-400">{c.category_id}</span>{" "}
                  {c.path || c.name}
                </button>
              </li>
            ))}
          </ul>
        )}
        {q.trim().length >= 2 && !buscando && resultados.length === 0 && (
          <p className="text-xs text-slate-500">
            Sin hojas que coincidan. Solo se ofrecen HOJAS: las categorías
            intermedias no tienen plantilla de atributos y Temu las rechaza.
          </p>
        )}

        {error && <p className="text-xs text-rose-600">{error}</p>}
      </div>
    </section>
  );
}
