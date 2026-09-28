"use client";

/**
 * CategoriaWalmartPicker — la categoría de Walmart del producto: la sugiere, la
 * muestra y deja cambiarla ANTES de publicar.
 *
 * Hermano de `CategoriaTemuPicker` y `CategoriaTikTokPicker`. Hasta el 28-sep
 * Walmart no tenía selector: el publicador decidía A CIEGAS con patrones de
 * título y prefijos de SKU, y lo que no casaba se rechazaba aunque tuviera
 * categoría con exención.
 *
 * DOS DIFERENCIAS CON TEMU, las dos del canal:
 *  · Walmart MX no tiene recomendador. La IA elige entre las 75 categorías del
 *    feed —descritas por los campos que cada una pide— con permiso de decir
 *    que ninguna encaja. No sabe cuáles tienen exención, a propósito: tiene que
 *    decir qué ES el producto, no dónde hay permiso.
 *  · La exención de UPC es POR CATEGORÍA. Elegir una sin exención se permite
 *    (es la verdad sobre el producto), pero el publicador no la manda y dice
 *    qué ticket falta — en vez de meterla en otra categoría que sí tenga.
 *
 * LA SUGERENCIA NO SE GUARDA SOLA (regla 2 de la casa): se propone y una
 * persona la acepta con "Usar esta". Recién entonces se escribe con
 * `source='panel'`, y desde ahí manda sobre las reglas en el publicador, en la
 * IA de contenido y en el semáforo.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Loader2, Search, Sparkles, Tag } from "lucide-react";

import {
  buscarCategoriasWalmart,
  categoriaWalmartActual,
  guardarCategoriaWalmart,
  sugerirCategoriaWalmart,
  type CategoriaWalmart,
  type CategoriaWalmartActual,
  type EstadoExencionWalmart,
  type SugerenciaWalmart,
} from "@/lib/api";

const ESTADOS: Record<EstadoExencionWalmart, { texto: string; clase: string; ayuda: string }> = {
  probada: {
    texto: "publicable",
    clase: "bg-emerald-100 text-emerald-700",
    ayuda: "Tiene exención de UPC y un artículo de esta categoría ya llegó a SUCCESS.",
  },
  por_ticket: {
    texto: "publicable · piloto",
    clase: "bg-sky-100 text-sky-700",
    ayuda:
      "Walmart la autorizó por escrito, pero ningún feed la ha confirmado: el primer " +
      "artículo es un piloto. Mándalo solo y revisa su veredicto antes de mandar más.",
  },
  negada: {
    texto: "sin exención · negada",
    clase: "bg-rose-100 text-rose-700",
    ayuda: "Walmart contestó \"not authorized\" a esta categoría. No se publica.",
  },
  sin_exencion: {
    texto: "sin exención",
    clase: "bg-slate-200 text-slate-600",
    ayuda: "Nadie ha pedido su exención de UPC en Seller Center. No se publica.",
  },
};

function Estado({ estado }: { estado?: EstadoExencionWalmart | null }) {
  const e = ESTADOS[estado || "sin_exencion"];
  return (
    <span title={e.ayuda}
      className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${e.clase}`}>
      {e.texto}
    </span>
  );
}

export default function CategoriaWalmartPicker({
  sku,
  titulo,
  onCambio,
}: {
  sku: string;
  titulo?: string | null;
  /** Avisa al Estudio que la categoría cambió: el semáforo mide otros campos. */
  onCambio?: () => void;
}) {
  const [actual, setActual] = useState<CategoriaWalmartActual | null>(null);
  const [sug, setSug] = useState<SugerenciaWalmart | null>(null);
  const [cargandoSug, setCargandoSug] = useState(false);
  const [q, setQ] = useState("");
  const [resultados, setResultados] = useState<CategoriaWalmart[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [guardando, setGuardando] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const recargar = useCallback(() => {
    categoriaWalmartActual(sku).then(setActual).catch(() => setActual(null));
  }, [sku]);

  useEffect(() => {
    setSug(null);
    setQ("");
    setResultados([]);
    setAviso(null);
    setError(null);
    // Se pide la sugerencia AL ABRIR, como Temu y TikTok, salvo que una persona
    // ya la haya elegido. También cuando la puso una REGLA: las reglas leen
    // palabras sueltas y la sugerencia es la segunda opinión que las corrige.
    categoriaWalmartActual(sku)
      .then((r) => {
        setActual(r);
        if (r?.origen !== "panel") pedirSugerencia();
      })
      .catch(() => setActual(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku]);

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
      buscarCategoriasWalmart(q.trim(), ctrl.signal)
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
      setSug(await sugerirCategoriaWalmart(sku, titulo || undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo pedir la sugerencia.");
    } finally {
      setCargandoSug(false);
    }
  };

  const elegir = async (categoriaId: string) => {
    setGuardando(categoriaId);
    setError(null);
    setAviso(null);
    try {
      const r = await guardarCategoriaWalmart(sku, categoriaId);
      setAviso(r.aviso || null);
      recargar();
      onCambio?.();
      setQ("");
      setResultados([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo guardar la categoría.");
    } finally {
      setGuardando(null);
    }
  };

  // Función que devuelve JSX, no un componente: uno declarado dentro del
  // render sería un tipo NUEVO en cada render y React lo re-montaría.
  const tarjeta = (c: CategoriaWalmart, etiqueta: string) => {
    const esLaActual = actual?.origen === "panel" && actual.category_id === c.category_id;
    return (
      <div key={`${etiqueta}-${c.category_id}`} className="rounded-xl border border-blue-200 bg-white p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-bold uppercase text-slate-400">{etiqueta}</span>
          <span className="text-sm font-semibold text-slate-800">{c.name || c.category_id}</span>
          <Estado estado={c.estado} />
        </div>
        {c.describe && (
          <p className="mt-0.5 text-[11px] text-slate-500">Pide: {c.describe}</p>
        )}
        {!c.autorizada && (
          <p className="mt-1 text-[11px] text-amber-700">
            Si la eliges se guarda —es lo que el producto es—, pero Walmart no la
            publica hasta que llegue su exención de UPC.
          </p>
        )}
        <button
          type="button"
          onClick={() => elegir(c.category_id)}
          disabled={esLaActual || guardando === c.category_id}
          className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-blue-300 px-2.5 py-1 text-xs font-semibold text-blue-700 hover:bg-blue-50 disabled:opacity-50"
        >
          {guardando === c.category_id
            ? <Loader2 size={12} className="animate-spin" />
            : <Check size={12} />}
          {esLaActual ? "Es la elegida" : "Usar esta"}
        </button>
      </div>
    );
  };

  return (
    <section className="overflow-hidden rounded-2xl border-2 border-blue-200 bg-blue-50/40">
      <header className="flex items-center gap-2 border-b border-blue-200 bg-blue-100/60 px-4 py-2">
        <Tag size={16} className="text-blue-700" />
        <span className="text-sm font-bold text-blue-900">Categoría de Walmart</span>
        {actual?.origen === "panel" && (
          <span className="rounded-full bg-blue-700 px-2 py-0.5 text-[10px] font-bold uppercase text-white">
            elegida aquí
          </span>
        )}
        {actual?.origen === "reglas" && (
          <span title="La decidieron los patrones del publicador (título y categorías de Woo). Una elección aquí manda sobre ellos."
            className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-600">
            por reglas
          </span>
        )}
      </header>

      <div className="space-y-3 p-4">
        {/* La actual: la MISMA que usaría el botón de publicar */}
        {actual?.category_id ? (
          <div className="space-y-1">
            <p className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
              <span className="font-semibold">{actual.name || actual.category_id}</span>
              <Estado estado={actual.estado} />
              {actual.folio && (
                <span className="text-[11px] text-slate-400">folio {actual.folio}</span>
              )}
            </p>
            {actual.origen === "reglas" && (
              <p className="text-[11px] text-slate-500">
                Nadie la ha confirmado: la eligieron las reglas por palabras del
                título. Confírmala o cámbiala — una elección aquí manda.
              </p>
            )}
            {!actual.autorizada && actual.motivo && (
              <p className="flex items-start gap-1.5 text-[11px] text-amber-700">
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                <span>{actual.motivo}</span>
              </p>
            )}
          </div>
        ) : actual ? (
          <p className="text-sm text-amber-700">
            Ninguna regla reconoce este producto, así que el botón de publicar lo
            rechazaría. Pide la sugerencia o búscala abajo: si su categoría tiene
            exención, se publica.
          </p>
        ) : null}

        {/* La sugerencia */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={pedirSugerencia}
            disabled={cargandoSug}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
          >
            {cargandoSug ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            {sug ? "Sugerir otra vez" : "Sugerir categoría"}
          </button>
          <span className="text-[11px] text-slate-500">
            La IA elige entre las 75 categorías del feed por lo que el producto ES
            — puede decir que ninguna encaja.
          </span>
        </div>

        {sug && !sug.ok && (
          <p className="text-xs text-amber-700">{sug.motivo}</p>
        )}
        {sug?.ok && sug.ninguna && (
          <p className="text-xs text-amber-700">
            La IA no encontró una categoría que encaje{sug.razon ? `: ${sug.razon}` : "."}{" "}
            Búscala a mano abajo.
          </p>
        )}
        {sug?.ok && sug.descartada && (
          <p className="text-[11px] text-slate-500">
            La IA contestó «{sug.descartada}», que no es una categoría del feed: se descartó.
          </p>
        )}
        {sug?.ok && sug.sugerida && (
          <div className="space-y-2">
            {tarjeta(sug.sugerida, "Sugerida")}
            {sug.razon && (
              <p className="-mt-1 px-1 text-[11px] text-slate-500">
                {sug.razon}
                {sug.confianza != null && ` · confianza ${Math.round(sug.confianza * 100)}%`}
              </p>
            )}
            {sug.alternativa && tarjeta(sug.alternativa, "Alternativa")}
          </div>
        )}

        {/* Cambiarla a mano */}
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar otra categoría… (ej. colchón, organizador, carriola, yoga)"
            className="w-full rounded-lg border border-slate-200 py-2 pl-8 pr-3 text-sm outline-none focus:border-blue-400"
          />
          {buscando && (
            <Loader2 size={14} className="absolute right-2.5 top-2.5 animate-spin text-slate-400" />
          )}
        </div>

        {resultados.length > 0 && (
          <ul className="max-h-64 space-y-1 overflow-y-auto">
            {resultados.map((c) => (
              <li key={c.category_id}>
                <button
                  type="button"
                  onClick={() => elegir(c.category_id)}
                  disabled={guardando === c.category_id}
                  className="w-full rounded-lg px-2 py-1.5 text-left text-xs text-slate-700 hover:bg-blue-50 disabled:opacity-50"
                >
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span className="font-semibold">{c.name || c.category_id}</span>
                    <Estado estado={c.estado} />
                  </span>
                  {c.describe && (
                    <span className="mt-0.5 block truncate text-[10px] text-slate-400">
                      {c.describe}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
        {q.trim().length >= 2 && !buscando && resultados.length === 0 && (
          <p className="text-xs text-slate-500">
            Ninguna categoría del feed coincide. Se busca por nombre y por los
            campos que pide cada una (ej. «firmeza» encuentra Blancos).
          </p>
        )}

        {aviso && (
          <p className="flex items-start gap-1.5 text-xs text-amber-700">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            <span>{aviso}</span>
          </p>
        )}
        {error && <p className="text-xs text-rose-600">{error}</p>}
      </div>
    </section>
  );
}
