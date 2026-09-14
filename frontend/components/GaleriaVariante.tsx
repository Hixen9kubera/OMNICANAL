"use client";

/**
 * GaleriaVariante — las miniaturas de la sección IMÁGENES del Estudio cuando el
 * SKU abierto es una VARIACIÓN de WooCommerce y el backend lo dice
 * (`es_variante: true`, solo con GALERIA_VARIANTE encendido).
 *
 * Por qué existe: hasta la fase 2 el Estudio enseñaba y editaba la galería del
 * PADRE sobre cualquier variante. Agregar, quitar o "Procesar con IA" en
 * MASC-1022-ROS cambiaba las fotos de toda la familia, y la foto propia de la
 * variante (su `image`) no aparecía porque no está en la galería del padre.
 *
 * Aquí se parte en dos:
 *   - "Fotos de esta variante": lo único que se edita. Principal marcada,
 *     agregar (clic o arrastrar archivos al "+", como siempre), quitar, hacer
 *     principal, reordenar con ← / → y los flags de IA de siempre.
 *   - "Del padre": solo lectura. La galería del padre guarda fotos de las
 *     hermanas (ORG-0841-ROS-S heredaba 3 miniaturas de sus hermanas; en
 *     TEC-0664-ROS la portada es TEC-0664-AZL.png), así que cada una lleva la
 *     decisión del backend —se publica o no, y por qué— y un botón para
 *     ADOPTARLA (pasa a las propias, mismo archivo de Medios).
 *
 * No se reordena arrastrando a propósito: cada suelta es una escritura en la
 * tienda, y un arrastre sobre la zona de archivos se confundía con subir.
 *
 * El componente no escribe nada por sí mismo: avisa a ProductStudio, que
 * llama al backend y vuelve a pintar la galería QUE DEVUELVE EL SERVIDOR. La
 * vista grande, la barra de progreso de la IA y el botón "Procesar con IA"
 * siguen en ProductStudio, compartidos con la galería de siempre.
 */

import { useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Ban,
  Check,
  CheckCircle2,
  ImageIcon,
  ImagePlus,
  Info,
  Layers,
  Loader2,
  Plus,
  Star,
  Trash2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type {
  FlagsImagen,
  GaleriaHeredada,
  GaleriaImagen,
  ImagenProgreso,
  ReglaGaleria,
} from "@/lib/types";
import type { CanalTheme } from "@/lib/theme";

interface FlagDef {
  key: keyof FlagsImagen;
  label: string;
  Icon: LucideIcon;
}

interface Props {
  propias: GaleriaImagen[];
  principalId: number | null;
  heredadas: GaleriaHeredada[];
  regla: ReglaGaleria | undefined;
  /** `aviso_legible` del backend: la misma línea que ve quien publica. */
  aviso: string | null;
  tema: CanalTheme;

  /** Índice activo dentro de `propias` (el que se ve en grande). */
  idxActiva: number;
  onSeleccionar: (i: number) => void;
  /** Heredada que se está viendo en grande (null = se ve una propia). */
  heredadaActiva: number | null;
  onVerHeredada: (id: number) => void;

  // Flags de IA — los mismos del Estudio de siempre.
  flagsDef: FlagDef[];
  flagsImg: Record<number, FlagsImagen>;
  toggleFlag: (id: number, key: keyof FlagsImagen) => void;
  hasFlags: (id: number) => boolean;
  countFlags: (id: number) => number;
  progreso: Record<number, ImagenProgreso>;

  /** Foto con una escritura en vuelo (spinner en su botón). */
  accionId: number | null;
  /** Cualquier escritura o proceso de IA en vuelo: se bloquea todo para no
   *  cruzar dos respuestas del servidor sobre la misma galería. */
  ocupado: boolean;
  agregando: boolean;
  /** Vista previa de Agrupada: se ve pero no escribe. */
  bloqueado: boolean;
  tituloBloqueo: string;

  onAgregar: (files: FileList | null) => void;
  onEliminar: (img: GaleriaImagen) => void;
  onPrincipal: (id: number) => void;
  onReordenar: (ids: number[]) => void;
  onAdoptar: (id: number) => void;
}

const ROTULO = "text-[10px] font-bold uppercase tracking-[0.15em] text-slate-400";
const MINI = "h-[46px] w-[46px]";

/** Qué se publica, en llano. Mismas cuatro reglas que `para_publicar`. */
const TEXTO_REGLA: Record<ReglaGaleria, string> = {
  propias: "Se publican sólo las fotos de esta variante",
  principal_y_padre: "Se publican la foto de esta variante y las del padre marcadas",
  solo_padre: "Esta variante no tiene foto propia: se publican las del padre marcadas",
  sin_fotos: "No hay fotos que publicar",
};

function mover(ids: number[], de: number, a: number): number[] {
  const n = ids.slice();
  const [x] = n.splice(de, 1);
  n.splice(a, 0, x);
  return n;
}

export default function GaleriaVariante(p: Props) {
  const { tema, propias, heredadas } = p;
  const [dragArchivo, setDragArchivo] = useState(false);

  const deshabilitado = p.bloqueado || p.ocupado;
  const tituloDeshab = p.bloqueado
    ? p.tituloBloqueo
    : p.ocupado
      ? "Espera a que termine la operación en curso"
      : undefined;
  const ids = propias.map((x) => x.id).filter((x): x is number => !!x);

  // `se_publica` de una heredada NO depende de la regla (el backend la califica
  // foto por foto: ¿es de una hermana?). Con galería propia no sale ninguna del
  // padre aunque esté limpia, así que la insignia lo refleja: si no, diría
  // "se publica" de una foto que no va a salir.
  const soloPropias = p.regla === "propias";
  const efectiva = (h: GaleriaHeredada) => ({
    sale: soloPropias || p.regla === "sin_fotos" ? false : h.se_publica,
    motivo:
      soloPropias && h.se_publica
        ? "La variante tiene galería propia: solo se publican sus fotos"
        : h.motivo,
  });
  const nHeredadasSalen = heredadas.filter((h) => efectiva(h).sale).length;
  const textoRegla = p.regla ? TEXTO_REGLA[p.regla] : null;

  return (
    <div className="space-y-3">
      {/* Qué se va a publicar, en llano (+ el aviso del backend si viene) */}
      {(textoRegla || p.aviso) && (
        <div
          className={[
            "flex items-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold",
            p.regla === "sin_fotos"
              ? "border-amber-200 bg-amber-50 text-amber-800"
              : "border-violet-100 bg-violet-50/60 text-violet-800",
          ].join(" ")}
        >
          {p.regla === "sin_fotos"
            ? <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            : <Info size={12} className="mt-0.5 shrink-0" />}
          <span>
            {textoRegla && <>{textoRegla}.</>}
            {p.aviso && (
              <span className="block font-normal opacity-80">{p.aviso}</span>
            )}
          </span>
        </div>
      )}

      {/* ── Fotos de esta variante (editables) ───────────────────────── */}
      <div>
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <span className={ROTULO}>Fotos de esta variante</span>
          <span className="text-[10px] text-slate-400">
            {propias.length ? "pasa el mouse para ordenar o editar" : "sin fotos propias"}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {propias.map((img, i) => {
            const prog = img.id ? p.progreso[img.id] : undefined;
            const activa = p.heredadaActiva === null && i === p.idxActiva;
            const esPrincipal = !!img.id && img.id === p.principalId;
            const enVuelo = p.accionId === img.id;
            return (
              <div key={img.id || `u${i}`} className="group relative">
                <button
                  type="button"
                  onClick={() => p.onSeleccionar(i)}
                  className={[
                    "relative block overflow-hidden rounded-lg border-2 bg-white transition-colors",
                    MINI,
                    activa ? "" : "border-slate-200 hover:border-slate-300",
                  ].join(" ")}
                  style={activa ? { borderColor: tema.color } : undefined}
                  title={esPrincipal ? "Foto principal de esta variante" : undefined}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={prog?.nueva_url || img.src} alt="" className="h-full w-full object-contain" />
                  {(prog?.estado === "procesando" || enVuelo) && (
                    <div className="absolute inset-0 flex items-center justify-center bg-white/70">
                      <Loader2 size={14} className="animate-spin" style={{ color: tema.color }} />
                    </div>
                  )}
                  {prog?.estado === "error" && (
                    <div className="absolute inset-0 flex items-center justify-center bg-red-500/25" title={prog.error ?? "Error"}>
                      <AlertTriangle size={13} className="text-red-600" />
                    </div>
                  )}
                  {prog?.estado === "listo" && (
                    <div className="absolute right-0 top-0 rounded-bl-md bg-emerald-500 p-0.5">
                      <CheckCircle2 size={10} className="text-white" />
                    </div>
                  )}
                  {!!img.id && !prog && p.hasFlags(img.id) && (
                    <div className="absolute left-0 top-0 rounded-br-md px-1 text-[9px] font-bold text-white" style={{ backgroundColor: tema.color }}>
                      {p.countFlags(img.id)}
                    </div>
                  )}
                  {esPrincipal && (
                    <div className="absolute bottom-0 left-0 rounded-tr-md p-0.5" style={{ backgroundColor: tema.color }}>
                      <Star size={9} fill="currentColor" style={{ color: tema.texto }} />
                    </div>
                  )}
                </button>

                {/* Popover al hover: orden, principal, flags de IA y quitar */}
                {!!img.id && (
                  <div className="absolute bottom-full left-1/2 z-30 hidden -translate-x-1/2 pb-2 group-hover:block group-focus-within:block">
                    <div className="w-52 rounded-xl border border-slate-200 bg-white p-2 shadow-xl">
                      <div className="mb-1.5 flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => p.onReordenar(mover(ids, i, i - 1))}
                          disabled={deshabilitado || i === 0}
                          title={tituloDeshab ?? (i === 1 ? "Mover al frente (queda de principal)" : "Mover a la izquierda")}
                          className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-50 text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                        >
                          <ArrowLeft size={13} />
                        </button>
                        <button
                          type="button"
                          onClick={() => p.onPrincipal(img.id)}
                          disabled={deshabilitado || esPrincipal}
                          title={tituloDeshab ?? (esPrincipal ? "Ya es la principal" : "La principal anterior pasa al frente de la galería")}
                          className="flex h-7 flex-1 items-center justify-center gap-1 rounded-lg px-2 text-[11px] font-bold transition-colors disabled:opacity-60"
                          style={esPrincipal
                            ? { backgroundColor: tema.suave, color: tema.acento }
                            : { backgroundColor: tema.color, color: tema.texto }}
                        >
                          <Star size={11} fill={esPrincipal ? "currentColor" : "none"} />
                          {esPrincipal ? "Principal" : "Hacer principal"}
                        </button>
                        <button
                          type="button"
                          onClick={() => p.onReordenar(mover(ids, i, i + 1))}
                          disabled={deshabilitado || i === propias.length - 1}
                          title={tituloDeshab ?? "Mover a la derecha"}
                          className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-50 text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                        >
                          <ArrowRight size={13} />
                        </button>
                      </div>
                      <div className="mb-1 px-1 text-[10px] font-bold uppercase tracking-wide text-slate-400">Editar con IA</div>
                      <div className="space-y-1">
                        {p.flagsDef.map(({ key, label, Icon }) => {
                          const on = !!p.flagsImg[img.id]?.[key];
                          return (
                            <button
                              type="button"
                              key={key}
                              onClick={() => p.toggleFlag(img.id, key)}
                              disabled={p.bloqueado}
                              title={p.bloqueado ? p.tituloBloqueo : undefined}
                              className={["flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-xs font-semibold transition-colors disabled:opacity-50", on ? "" : "bg-slate-50 text-slate-600 hover:bg-slate-100"].join(" ")}
                              style={on ? { backgroundColor: tema.color, color: tema.texto } : undefined}
                            >
                              <Icon size={13} /> {label}
                              {on && <CheckCircle2 size={12} className="ml-auto" />}
                            </button>
                          );
                        })}
                      </div>
                      <button
                        type="button"
                        onClick={() => p.onEliminar(img)}
                        disabled={deshabilitado}
                        title={tituloDeshab ?? "Se quita de esta variante; el archivo sigue en Medios"}
                        className="mt-1.5 flex w-full items-center justify-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-2 py-1.5 text-xs font-bold text-red-600 transition-colors hover:bg-red-100 disabled:opacity-50"
                      >
                        {enVuelo ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                        Quitar de la variante
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {/* Agregar: clic o arrastrar archivos, como en el Estudio de siempre */}
          <label
            onDragOver={(e) => {
              e.preventDefault();
              if (!deshabilitado) setDragArchivo(true);
            }}
            onDragLeave={() => setDragArchivo(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragArchivo(false);
              if (!deshabilitado) p.onAgregar(e.dataTransfer.files);
            }}
            title={tituloDeshab ?? "Agregar fotos a esta variante (clic o arrastra aquí)"}
            className={[
              "flex flex-col items-center justify-center rounded-lg border-2 border-dashed transition-colors",
              MINI,
              deshabilitado ? "cursor-not-allowed opacity-50" : "cursor-pointer",
              dragArchivo ? "" : "border-slate-200 text-slate-300 hover:border-slate-300 hover:text-slate-400",
            ].join(" ")}
            style={dragArchivo ? { borderColor: tema.color, color: tema.color } : undefined}
          >
            {p.agregando ? <Loader2 size={16} className="animate-spin" style={{ color: tema.color }} /> : <Plus size={18} />}
            <input
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              disabled={deshabilitado}
              onChange={(e) => { p.onAgregar(e.target.files); e.currentTarget.value = ""; }}
            />
          </label>
        </div>
      </div>

      {/* ── Del padre (solo lectura) ─────────────────────────────────── */}
      {heredadas.length > 0 && (
        <div className="rounded-xl border border-violet-100 bg-violet-50/30 p-2.5">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.15em] text-violet-700">
                <Layers size={10} /> Del padre
              </span>
              <span className="text-[10px] text-slate-400">solo lectura</span>
            </span>
            <span className="text-[10px] text-slate-400">
              {nHeredadasSalen} de {heredadas.length} se publican
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {heredadas.map((h) => {
              const { sale, motivo } = efectiva(h);
              const activa = p.heredadaActiva === h.id;
              const yaPropia = propias.some((x) => x.id === h.id);
              const enVuelo = p.accionId === h.id;
              // Un adjunto borrado de Medios no se puede adoptar: la variante
              // quedaría apuntando a nada.
              const adoptable = !yaPropia && !!h.src;
              return (
                <div key={h.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => p.onVerHeredada(h.id)}
                    title={sale ? "Se publica" : `No se publica${motivo ? `: ${motivo}` : ""}`}
                    className={[
                      "relative flex items-center justify-center overflow-hidden rounded-lg border-2 bg-white transition-colors",
                      MINI,
                      activa ? "" : "border-violet-200 hover:border-violet-300",
                      sale ? "" : "opacity-60",
                    ].join(" ")}
                    style={activa ? { borderColor: tema.color } : undefined}
                  >
                    {h.src ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={h.src} alt="" className="h-full w-full object-contain" />
                    ) : (
                      <ImageIcon size={16} className="text-slate-300" />
                    )}
                    <div
                      className={["absolute bottom-0 right-0 rounded-tl-md p-0.5", sale ? "bg-emerald-500" : "bg-slate-500"].join(" ")}
                    >
                      {sale ? <Check size={9} className="text-white" /> : <Ban size={9} className="text-white" />}
                    </div>
                    {enVuelo && (
                      <div className="absolute inset-0 flex items-center justify-center bg-white/70">
                        <Loader2 size={14} className="animate-spin" style={{ color: tema.color }} />
                      </div>
                    )}
                  </button>

                  <div className="absolute bottom-full left-1/2 z-30 hidden -translate-x-1/2 pb-2 group-hover:block group-focus-within:block">
                    <div className="w-56 rounded-xl border border-slate-200 bg-white p-2 shadow-xl">
                      <span
                        title={!sale && motivo ? motivo : undefined}
                        className={[
                          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold",
                          sale ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600",
                        ].join(" ")}
                      >
                        {sale ? <Check size={10} /> : <Ban size={10} />}
                        {sale ? "se publica" : "no se publica"}
                      </span>
                      {!sale && motivo && (
                        <p className="mt-1 px-0.5 text-[11px] leading-snug text-slate-500">{motivo}</p>
                      )}
                      <button
                        type="button"
                        onClick={() => p.onAdoptar(h.id)}
                        disabled={deshabilitado || !adoptable}
                        title={
                          tituloDeshab
                          ?? (yaPropia
                            ? "Ya está entre las fotos de esta variante"
                            : !h.src
                              ? "El archivo ya no existe en Medios"
                              : "Pasa a las fotos de esta variante (mismo archivo de Medios)")
                        }
                        className="mt-1.5 flex w-full items-center justify-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 px-2 py-1.5 text-xs font-bold text-violet-700 transition-colors hover:bg-violet-100 disabled:opacity-50"
                      >
                        {enVuelo ? <Loader2 size={12} className="animate-spin" /> : <ImagePlus size={12} />}
                        {yaPropia ? "Ya es de esta variante" : "Usar en esta variante"}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
