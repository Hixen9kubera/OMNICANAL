"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, HelpCircle, Loader2, Search, XCircle } from "lucide-react";
import CategoriaMLPicker from "./CategoriaMLPicker";
import type { CategoriaRevisada, RevisionCategoriaML, RevisionVectores } from "@/lib/types";

/** Igual que `categoria_vectores.MOTIVO_MIN` en el backend. */
const MOTIVO_MIN = 10;

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
  /** Por qué se publica aquí aunque la revisión por texto y fotos salió en rojo. */
  motivo: string;
  onMotivo: (v: string) => void;
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

export const SEMAFORO: Record<RevisionVectores["color"], { tono: string; icono: typeof CheckCircle2; texto: (n: number | null) => string }> = {
  verde: {
    tono: "bg-emerald-50 text-emerald-700",
    icono: CheckCircle2,
    texto: (n) => `El título, la descripción y las fotos se parecen a esta categoría (lugar ${n} de ~10,700).`,
  },
  ambar: {
    tono: "bg-amber-50 text-amber-700",
    icono: AlertTriangle,
    texto: (n) => `Se parece, pero hay categorías más parecidas (lugar ${n}): compáralas.`,
  },
  rojo: {
    tono: "bg-red-50 text-red-700",
    icono: XCircle,
    texto: (n) => `El producto NO se parece a esta categoría (lugar ${n} de ~10,700). Revisa las propuestas.`,
  },
  sin_senal: {
    tono: "bg-slate-100 text-slate-600",
    icono: HelpCircle,
    texto: () => "No se pudo revisar con el texto y las fotos: revísala a ojo.",
  },
};

export const FUENTE: Record<string, string> = { texto_y_fotos: "Texto y fotos", fotos: "Fotos", texto: "Texto" };

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
  revision, revisada, onRevisada, onCambiar, cambiando, error, cambiada, bloqueado, motivo, onMotivo, acento,
}: Props) {
  const [buscar, setBuscar] = useState(false);
  const { actual, sugeridas, veredicto, origen_texto, imagen, vectores } = revision;
  const v = VEREDICTO[veredicto] ?? VEREDICTO.sin_sugerencia;
  const Icono = v.icono;
  const sem = vectores ? SEMAFORO[vectores.color] ?? SEMAFORO.sin_senal : null;
  const IconoSem = sem?.icono;
  const rojo = vectores?.color === "rojo";
  // Con el candado, en rojo no se confirma sin un motivo que diga algo (el backend lo vuelve a exigir).
  const faltaMotivo = !!vectores?.candado && rojo && motivo.trim().length < MOTIVO_MIN;
  const sePuedeConfirmar = !!actual && veredicto !== "no_publicable" && !cambiando && !faltaMotivo;
  const lugar = vectores ? (vectores.rango_fusion ?? vectores.rango_texto) : null;
  // Lo que proponen ML Y el texto con las fotos se marca arriba y no se repite abajo:
  // que las dos señales coincidan es la pista más fuerte de cuál es la buena.
  const idsVec = new Set(vectores && vectores.color !== "verde" ? vectores.propuestas.map((p) => p.category_id) : []);
  const idsPredictor = new Set(sugeridas.map((s) => s.category_id));
  const propuestasVec = vectores ? vectores.propuestas.filter((p) => !idsPredictor.has(p.category_id)) : [];

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
                    {idsVec.has(s.category_id) && (
                      <span className="ml-1.5 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold text-violet-700">
                        también por texto y fotos
                      </span>
                    )}
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

      {vectores && sem && IconoSem && (
        <div className="mt-3 border-t border-slate-100 pt-2">
          <div className="mb-1 text-[11px] font-semibold text-slate-500">
            Revisión por texto y fotos{vectores.n_fotos ? ` (${vectores.n_fotos} foto${vectores.n_fotos > 1 ? "s" : ""})` : ""}
            {vectores.solo_fotos && " · solo con las fotos: el texto no contestó a tiempo"}
          </div>
          <div className={`flex items-start gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium ${sem.tono}`}>
            <IconoSem size={14} className="mt-0.5 shrink-0" /> {sem.texto(lugar)}
          </div>
          {vectores.color !== "verde" && propuestasVec.length > 0 && (
            <div className="mt-1.5 space-y-1">
              {propuestasVec.map((p) => (
                <div key={p.category_id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs">
                  <div className="min-w-0">
                    <Ruta c={p} acento={acento} />
                    <span className="font-mono text-[10px] text-slate-400">
                      {p.category_id} · {FUENTE[p.fuente] ?? p.fuente}
                    </span>
                  </div>
                  <button
                    onClick={() => onCambiar(p.category_id)}
                    disabled={bloqueado || cambiando}
                    className="shrink-0 rounded-lg border border-slate-300 px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    Usar esta
                  </button>
                </div>
              ))}
            </div>
          )}
          {rojo && (
            <label className="mt-2 block text-xs">
              <span className="font-semibold text-slate-600">
                ¿Por qué esta categoría es la correcta?
                {vectores.candado ? " (obligatorio para publicar aquí)" : " (opcional por ahora: ayuda a calibrar la revisión)"}
              </span>
              <textarea
                value={motivo}
                onChange={(e) => {
                  onMotivo(e.target.value);
                  if (vectores.candado && e.target.value.trim().length < MOTIVO_MIN && revisada) onRevisada(false);
                }}
                disabled={bloqueado || cambiando}
                rows={2}
                maxLength={1000}
                placeholder="Ej.: es una herramienta de taller, no una refacción del auto"
                className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs text-slate-700 disabled:opacity-50"
              />
            </label>
          )}
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
          {rojo
            ? "Revisé la categoría: el texto y las fotos sugieren otra, pero esta es la correcta"
            : veredicto === "distinta"
              ? "Revisé la categoría: Mercado Libre sugiere otra, pero esta es la correcta"
              : "Revisé la categoría y es la correcta"}
        </span>
      </label>
    </div>
  );
}
