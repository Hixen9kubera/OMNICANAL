"use client";

import { useEffect, useRef, useState } from "react";
import { HelpCircle, Loader2, RefreshCw } from "lucide-react";
import { revisarCategoriaML } from "@/lib/api";
import type { CategoriaMLResult, CategoriaRevisada, RevisionVectores } from "@/lib/types";
import { FUENTE, SEMAFORO } from "./RevisionCategoriaML";

interface Props {
  sku: string | null;
  wcId: number | null;
  /** La categoría elegida en el selector (la del panel, que MANDA al publicar). */
  categoriaId: string;
  titulo: string;
  descripcion: string;
  /** La galería en orden: solo cuentan las 2 primeras, como en la vista previa. */
  fotos: string[];
  /** El producto ya cargó (título y galería): antes de eso la revisión saldría de un texto vacío. */
  listo: boolean;
  /** «Usar esta»: la misma acción que elegirla en el selector. */
  onUsar: (c: CategoriaMLResult) => void;
  bloqueado?: boolean;
  acento: string;
}

/** Una propuesta de la lista: del predictor de ML o de los vectores. */
interface Propuesta extends CategoriaRevisada {
  /** De dónde sale y en qué lugar, p. ej. «n.º 1 de Mercado Libre» o «n.º 4 por texto y fotos». */
  etiqueta: string;
  /** La propone Mercado Libre Y está entre las más parecidas por texto y fotos. */
  tambien: boolean;
}

/** La ruta en una línea que se parte por palabras. Completa, sin recortar el medio: ahí está a
 *  veces la diferencia (Refacciones Autos y Camionetas contra Refacciones de Línea Pesada). */
function RutaEnLinea({ c, acento }: { c: CategoriaRevisada; acento: string }) {
  const niveles = c.niveles?.length ? c.niveles.map((n) => n.name) : (c.ruta || c.name).split(" > ");
  const antes = niveles.slice(0, -1);
  return (
    <span className="block leading-snug">
      {antes.length > 0 && <span className="text-slate-500">{antes.join(" › ")} › </span>}
      <span className="font-semibold" style={{ color: acento }}>{niveles[niveles.length - 1]}</span>
    </span>
  );
}

/**
 * Primero lo que propone Mercado Libre para el título (en su orden) y después lo de los vectores, como
 * en la tarjeta de la vista previa. Medido sobre los 50 errores de la prueba: así la correcta queda en
 * la lista 46 veces y es la 1.ª en 36; solo con los vectores, 30 y 18. Los vectores detectan la
 * categoría equivocada (casi siempre la gemela de otra rama); el predictor casi siempre trae la buena.
 */
function armarLista(rev: RevisionVectores | null, sugeridas: CategoriaRevisada[], actual: string): Propuesta[] {
  const vec = rev?.propuestas ?? [];
  const idsVec = new Set(vec.map((p) => p.category_id));
  const deMl: Propuesta[] = sugeridas
    .map((s, i) => ({ ...s, etiqueta: `n.º ${i + 1} de Mercado Libre`, tambien: idsVec.has(s.category_id) }))
    .filter((s) => s.category_id !== actual && s.publicable !== false);
  const vistos = new Set(deMl.map((s) => s.category_id));
  const deVec: Propuesta[] = vec
    .filter((p) => !vistos.has(p.category_id))
    .map((p) => ({
      ...p,
      etiqueta: p.rango ? `n.º ${p.rango} por ${(FUENTE[p.fuente] ?? p.fuente).toLowerCase()}` : FUENTE[p.fuente] ?? p.fuente,
      tambien: false,
    }));
  return [...deMl, ...deVec];
}

/** Propuestas a la vista; el resto tras «Ver más». */
const VISIBLES = 3;

/**
 * ¿La revisión está encendida? Lo que contestó el backend la última vez en esta pestaña; null = aún no se sabe.
 * Mientras no se sepa no se pinta nada, ni «cargando» ni errores: con la bandera apagada el Estudio debe verse
 * exactamente como antes.
 */
let encendida: boolean | null = null;

/** El backend solo usa ~600 caracteres limpios; recortar evita un 422 con descripciones enormes. */
const DESCRIPCION_MAX = 20_000;

/** Lo que se revisa. Si cambia, la revisión de la pantalla ya no es de lo que se ve. */
function entradaDe(categoriaId: string, titulo: string, descripcion: string, fotos: string[]) {
  return JSON.stringify([categoriaId, titulo.trim(), descripcion.trim(), fotos.slice(0, 2)]);
}

/**
 * El semáforo de la categoría de Mercado Libre JUNTO AL SELECTOR del Estudio (27-oct-2026):
 * el título con la descripción y las 2 primeras fotos contra todo el árbol de ML
 * (`services/categoria_vectores.py`), para corregir la categoría ANTES de llenar atributos
 * y abrir la vista previa. Se revisa al abrir el producto y al cambiar de categoría; si
 * después cambia el texto o las fotos, se ofrece revisar de nuevo (cada revisión nueva
 * cuesta ~US$0.001; repetir la misma no cuesta). Las propuestas suman lo que dice el predictor
 * de ML para el título (v0.691.0, ver `armarLista`). La vista previa vuelve a revisar con lo
 * que de verdad sale a ML: esta es el aviso temprano, no el candado.
 *
 * Con `CATEGORIA_VEC_ENABLED` apagada el backend contesta `vectores: null` y aquí no se
 * pinta nada (tampoco «cargando» ni un error si el POST falla: ver `encendida`). La primera
 * revisión de la pestaña llega sin «cargando», porque todavía no se sabe si está encendida.
 */
export default function SemaforoCategoriaML({
  sku, wcId, categoriaId, titulo, descripcion, fotos, listo, onUsar, bloqueado, acento,
}: Props) {
  const [rev, setRev] = useState<RevisionVectores | null>(null);
  const [sugeridas, setSugeridas] = useState<CategoriaRevisada[]>([]);
  const [entradaRev, setEntradaRev] = useState<string | null>(null);
  const [activa, setActiva] = useState<boolean | null>(encendida);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState(false);
  const [verParecidas, setVerParecidas] = useState(false);
  const [todas, setTodas] = useState(false);
  const pedido = useRef(0);
  const entrada = entradaDe(categoriaId, titulo, descripcion, fotos);
  // Lo último que se ve en pantalla, para que la revisión automática no se quede con un render viejo.
  const vigente = useRef({ sku, wcId, categoriaId, titulo, descripcion, fotos, entrada });
  vigente.current = { sku, wcId, categoriaId, titulo, descripcion, fotos, entrada };

  async function revisar() {
    const v = vigente.current;
    if (!v.sku || !v.categoriaId) return;
    const n = ++pedido.current;
    setCargando(true);
    setError(false);
    try {
      const r = await revisarCategoriaML({
        sku: v.sku, wc_id: v.wcId, categoria_id: v.categoriaId,
        titulo: v.titulo, descripcion: v.descripcion.slice(0, DESCRIPCION_MAX), fotos: v.fotos.slice(0, 2),
      });
      if (n !== pedido.current) return;           // llegó tarde: ya se pidió otra
      encendida = r.vectores != null;
      setActiva(encendida);
      setRev(r.vectores);
      setSugeridas(r.sugeridas_ml ?? []);
      setEntradaRev(v.entrada);
      setVerParecidas(false);
      setTodas(false);
    } catch {
      if (n === pedido.current) setError(true);
    } finally {
      if (n === pedido.current) setCargando(false);
    }
  }

  // Al abrir el producto y al cambiar de categoría. El texto y las fotos NO disparan solos:
  // escribir un título pagaría una revisión por cada pausa.
  useEffect(() => {
    setRev(null);
    setSugeridas([]);
    setEntradaRev(null);
    setError(false);
    if (!listo || !sku || !categoriaId) return;
    const t = setTimeout(revisar, 600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku, categoriaId, listo]);

  if (activa !== true || !sku || !categoriaId) return null;
  if (!rev && !cargando && !error) return null;

  const sem = rev ? SEMAFORO[rev.color] ?? SEMAFORO.sin_senal : null;
  const IconoSem = sem?.icono ?? HelpCircle;
  const lugar = rev ? (rev.rango_fusion ?? rev.rango_texto) : null;
  const cambio = !!rev && entradaRev !== entrada;
  const abiertas = rev?.color === "ambar" || rev?.color === "rojo" || verParecidas;
  const lista = armarLista(rev, sugeridas, categoriaId);

  return (
    <div className="mt-2 rounded-xl border border-slate-200 bg-slate-50/60 p-2.5">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-slate-500">
          Revisión de la categoría · título, descripción y{" "}
          {rev?.n_fotos ? `${rev.n_fotos} foto${rev.n_fotos > 1 ? "s" : ""}` : "fotos"}
          {rev?.solo_fotos && " · solo con las fotos: el texto no contestó a tiempo"}
        </span>
        {(cambio || error || rev?.color === "sin_senal") && !cargando && (
          <button
            onClick={revisar}
            className="flex shrink-0 items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
          >
            <RefreshCw size={11} /> Revisar de nuevo
          </button>
        )}
      </div>

      {cargando ? (
        <div className="flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1.5 text-xs text-slate-500">
          <Loader2 size={13} className="animate-spin" /> Comparando el producto con las ~10,700 categorías de Mercado Libre…
        </div>
      ) : error ? (
        <div className="rounded-lg bg-slate-100 px-2.5 py-1.5 text-xs text-slate-600">
          No se pudo revisar la categoría ahora. Se revisa otra vez al publicar.
        </div>
      ) : rev && sem ? (
        <>
          <div className={`flex items-start gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium ${sem.tono} ${cambio ? "opacity-60" : ""}`}>
            <IconoSem size={14} className="mt-0.5 shrink-0" />
            <span>
              {sem.texto(lugar)}
              {rev.color === "sin_senal" && rev.motivo_sin_senal ? ` (${rev.motivo_sin_senal})` : ""}
            </span>
          </div>
          {cambio && (
            <div className="mt-1 text-[11px] text-amber-700">
              Cambiaste el título, la descripción o las fotos desde esta revisión.
            </div>
          )}

          {lista.length > 0 && (
            abiertas ? (
              <div className="mt-1.5 space-y-1">
                <div className="text-[11px] font-semibold text-slate-500">
                  {rev.color === "verde" ? "Otras parecidas" : "Propuestas de Mercado Libre y del texto con las fotos"}
                </div>
                {(todas ? lista : lista.slice(0, VISIBLES)).map((p) => (
                  <div key={p.category_id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs">
                    <div className="min-w-0">
                      <RutaEnLinea c={p} acento={acento} />
                      <span className="font-mono text-[10px] text-slate-400">
                        {p.category_id} · {p.etiqueta}
                      </span>
                      {p.tambien && (
                        <span className="ml-1.5 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold text-violet-700">
                          también por texto y fotos
                        </span>
                      )}
                    </div>
                    <button
                      onClick={() => onUsar({ category_id: p.category_id, name: p.name, path: p.ruta ?? p.name, domain: "" })}
                      disabled={bloqueado}
                      className="shrink-0 rounded-lg border border-slate-300 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >
                      Usar esta
                    </button>
                  </div>
                ))}
                {!todas && lista.length > VISIBLES && (
                  <button onClick={() => setTodas(true)} className="text-[11px] font-semibold" style={{ color: acento }}>
                    Ver {lista.length - VISIBLES} más
                  </button>
                )}
              </div>
            ) : (
              <button
                onClick={() => setVerParecidas(true)}
                className="mt-1 text-[11px] font-semibold"
                style={{ color: acento }}
              >
                Ver otras parecidas
              </button>
            )
          )}

          {rev.color === "rojo" && (
            <div className="mt-1.5 text-[11px] text-slate-500">
              Si la elegida sí es la correcta, déjala:{" "}
              {rev.candado
                ? "al publicar se pedirá escribir por qué."
                : "al publicar podrás escribir por qué (ayuda a calibrar la revisión)."}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
