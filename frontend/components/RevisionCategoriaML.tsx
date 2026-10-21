"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, HelpCircle, Loader2, Search, XCircle } from "lucide-react";
import CategoriaMLPicker from "./CategoriaMLPicker";
import type { CategoriaRevisada, RevisionCategoriaML } from "@/lib/types";

interface Props {
  revision: RevisionCategoriaML;
  /** La persona marcó «Revisé la categoría». Sin eso no se habilita Confirmar. */
  revisada: boolean;
  onRevisada: (v: boolean) => void;
  /** Guarda la categoría elegida (la del panel, que MANDA) y regenera la vista previa. */
  onCambiar: (categoryId: string) => void;
  cambiando: boolean;
  error?: string | null;
  /** Se cambió desde este modal: los atributos de la IA eran de la anterior. */
  cambiada?: boolean;
  /** No se puede cambiar la categoría aquí (vista agrupada, sin wc_id, o ya se publicó). */
  bloqueado?: boolean;
  acento: string;
}

const VEREDICTO: Record<RevisionCategoriaML["veredicto"], { tono: string; icono: typeof CheckCircle2; texto: string }> = {
  coincide: {
    tono: "bg-emerald-50 text-emerald-700",
    icono: CheckCircle2,
    texto: "Mercado Libre también la propone para este título.",
  },
  parecida: {
    tono: "bg-amber-50 text-amber-700",
    icono: AlertTriangle,
    texto: "Mercado Libre propone una categoría hermana: compáralas.",
  },
  distinta: {
    tono: "bg-red-50 text-red-700",
    icono: XCircle,
    texto: "Mercado Libre pondría este producto en OTRA categoría. Revísala con cuidado.",
  },
  sin_sugerencia: {
    tono: "bg-slate-100 text-slate-600",
    icono: HelpCircle,
    texto: "Mercado Libre no propuso nada para este título: revísala a ojo.",
  },
  no_publicable: {
    tono: "bg-red-50 text-red-700",
    icono: XCircle,
    texto: "Mercado Libre no deja publicar aquí (es una rama, no una categoría final). Elige otra.",
  },
  sin_categoria: {
    tono: "bg-red-50 text-red-700",
    icono: XCircle,
    texto: "Este producto no tiene categoría de Mercado Libre: elige una para poder publicar.",
  },
};

function Ruta({ c, acento }: { c: CategoriaRevisada; acento: string }) {
  const niveles = c.niveles?.length ? c.niveles.map((n) => n.name) : c.ruta ? c.ruta.split(" > ") : [c.name];
  return (
    <span className="flex flex-wrap items-center gap-1">
      {niveles.map((n, i, arr) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <ChevronRight size={12} className="text-slate-300" />}
          <span
            className={i === arr.length - 1 ? "font-semibold" : "text-slate-500"}
            style={i === arr.length - 1 ? { color: acento } : undefined}
          >
            {n}
          </span>
        </span>
      ))}
    </span>
  );
}

/**
 * La DOBLE VALIDACIÓN de la categoría de Mercado Libre antes de un alta (Brandon,
 * 21-oct-2026: ML acepta categorías equivocadas sin quejarse). Enseña la foto y la
 * categoría que va a salir, lo que propone ML para el título, y deja cambiarla ahí
 * mismo. Confirmar no se habilita hasta marcar «Revisé la categoría», y el backend
 * vuelve a comprobar que la confirmada sea la que sale (`ml_revision_categoria.py`).
 */
export default function RevisionCategoriaML({
  revision, revisada, onRevisada, onCambiar, cambiando, error, cambiada, bloqueado, acento,
}: Props) {
  const [buscar, setBuscar] = useState(false);
  const { actual, sugeridas, veredicto, origen_texto, imagen } = revision;
  const v = VEREDICTO[veredicto] ?? VEREDICTO.sin_sugerencia;
  const Icono = v.icono;
  const sePuedeConfirmar = !!actual && veredicto !== "no_publicable" && !cambiando;

  return (
    <div className="rounded-xl border-2 p-3" style={{ borderColor: revisada ? "#10b981" : acento }}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-500">
          Categoría en Mercado Libre · revísala antes de publicar
        </span>
        {actual?.category_id && <span className="font-mono text-[10px] text-slate-400">{actual.category_id}</span>}
      </div>

      <div className="flex gap-3">
        {imagen ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={imagen} alt="" className="h-16 w-16 shrink-0 rounded-lg border border-slate-200 object-contain" />
        ) : null}
        <div className="min-w-0 flex-1 text-sm">
          {actual ? (
            <Ruta c={actual} acento={acento} />
          ) : (
            <span className="text-slate-500">Sin categoría: elige una para poder publicar.</span>
          )}
          <div className="mt-0.5 text-[11px] text-slate-400">{origen_texto}</div>
          {revision.titulo && <div className="mt-1 truncate text-xs text-slate-500" title={revision.titulo}>{revision.titulo}</div>}
        </div>
      </div>

      <div className={`mt-2 flex items-start gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium ${v.tono}`}>
        <Icono size={14} className="mt-0.5 shrink-0" /> {v.texto}
      </div>

      {sugeridas.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-[11px] font-semibold text-slate-500">Lo que propone Mercado Libre para el título</div>
          <div className="space-y-1">
            {sugeridas.map((s) => {
              const esLaActual = s.category_id === actual?.category_id;
              return (
                <div key={s.category_id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs">
                  <div className="min-w-0">
                    <Ruta c={s} acento={acento} />
                    <span className="font-mono text-[10px] text-slate-400">{s.category_id}</span>
                  </div>
                  {esLaActual ? (
                    <span className="shrink-0 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700">LA QUE VA</span>
                  ) : (
                    <button
                      onClick={() => onCambiar(s.category_id)}
                      disabled={bloqueado || cambiando || s.publicable === false}
                      className="shrink-0 rounded-lg border border-slate-300 px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >
                      Usar esta
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="mt-2">
        {buscar ? (
          <fieldset disabled={bloqueado || cambiando} className="min-w-0">
            <CategoriaMLPicker
              value={actual?.category_id ?? ""}
              pathInicial={actual?.niveles?.map((n) => n.name)}
              onChange={(c) => { setBuscar(false); onCambiar(c.category_id); }}
              acento={acento}
            />
          </fieldset>
        ) : (
          <button
            onClick={() => setBuscar(true)}
            disabled={bloqueado || cambiando}
            className="flex items-center gap-1 text-xs font-semibold disabled:opacity-50"
            style={{ color: acento }}
          >
            <Search size={13} /> Buscar otra categoría
          </button>
        )}
      </div>

      {cambiando && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
          <Loader2 size={13} className="animate-spin" /> Guardando la categoría y regenerando la vista previa…
        </div>
      )}
      {error && <div className="mt-2 rounded-lg bg-red-50 px-2.5 py-1.5 text-xs text-red-600">{error}</div>}
      {cambiada && !cambiando && (
        <div className="mt-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700">
          Cambiaste la categoría: revisa los atributos de abajo. Los que generó la IA eran para la anterior; si faltan,
          usa «Mejorar con IA» antes de publicar.
        </div>
      )}

      <label className={`mt-3 flex cursor-pointer items-start gap-2 rounded-lg px-2.5 py-2 text-sm ${revisada ? "bg-emerald-50" : "bg-slate-50"} ${sePuedeConfirmar ? "" : "cursor-not-allowed opacity-60"}`}>
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4"
          checked={revisada}
          disabled={!sePuedeConfirmar}
          onChange={(e) => onRevisada(e.target.checked)}
        />
        <span className="font-semibold text-slate-700">
          {veredicto === "distinta"
            ? "Revisé la categoría: Mercado Libre sugiere otra, pero esta es la correcta"
            : "Revisé la categoría y es la correcta"}
        </span>
      </label>
    </div>
  );
}
