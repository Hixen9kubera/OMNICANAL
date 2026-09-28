"use client";

/**
 * Los campos de atributos de un canal, editables — el mismo control en el
 * cajón del Catálogo Maestro y en el detalle del Checklist de almacén.
 *
 * El control depende del tipo que declara ML: sí/no, número con su unidad, o
 * texto con sugerencias (en ML los valores son SUGERENCIAS casi siempre: se
 * deja escribir otro). Tres cosas que antes faltaban:
 *
 *  · La unidad por omisión es la que ML ASUME (`unidad_default`), nunca la
 *    primera de la lista: en «Peso máximo soportado» la primera es `g`, y «20»
 *    de un corral son 20 kg.
 *  · El valor POR OMISIÓN (BRAND → Ferrahome) va entre las opciones, con un
 *    botón para usarlo. ML solo sugiere marcas de terceros.
 *  · Lo que ya trae la publicación viva de ML se ENSEÑA (en verde) pero no se
 *    escribe en el campo: si se guardara solo por estar ahí, los errores de
 *    una publicación clonada acabarían en kubera.
 *
 * Los ids de las <datalist> llevan `useId()`: con dos editores en pantalla,
 * `lista-COLOR` chocaba.
 */
import { useId, useState } from "react";

export interface CampoEditable {
  campo: string;
  etiqueta: string;
  tipo: string | null;
  valores: string[];
  unidades: string[];
  unidad_default?: string | null;
  /** Lo que el publicador pone si se deja vacío (BRAND → Ferrahome). */
  por_omision?: string | null;
  /** Lo que trae HOY la publicación viva de ML (no está en kubera). */
  publicado?: string | null;
  obligatorio: boolean;
}

/** ¿Falta de verdad? Un obligatorio vacío que ni la publicación ni el
 *  publicador (valor por omisión) van a llenar. */
export function faltaCampo(c: CampoEditable, valor: string | undefined): boolean {
  return c.obligatorio && !(valor ?? "").trim() && !c.publicado && !c.por_omision;
}

/** Un número+unidad escrito SIN unidad cuando ML acepta varias y no dice cuál
 *  asume: «20» de un peso ¿son gramos o kilos? No se debe guardar así. */
export function sinUnidad(c: CampoEditable, valor: string | undefined): boolean {
  const v = (valor ?? "").trim();
  return c.tipo === "number_unit" && c.unidades.length > 0 && !!v && !v.includes(" ");
}

export function CamposAtributos({
  titulo, campos, valores, setValores, soloLectura, errores,
}: {
  titulo?: string;
  campos: CampoEditable[];
  valores: Record<string, string>;
  setValores: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  soloLectura?: boolean;
  /** Un motivo por campo, tal como lo devolvió el backend. */
  errores?: Record<string, string>;
}) {
  const base = useId();
  const poner = (campo: string, v: string) => setValores((p) => ({ ...p, [campo]: v }));
  // La unidad elegida ANTES de escribir el número: sin esto se perdía y el
  // número quedaba con la unidad por omisión («500» → «500 kg» en vez de g).
  const [unidadElegida, setUnidadElegida] = useState<Record<string, string>>({});
  const campoCss =
    "w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 outline-none focus:border-indigo-300 disabled:bg-slate-50";
  return (
    <div className="mt-2">
      {titulo && (
        <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">{titulo}</div>
      )}
      <div className="mt-1 space-y-1.5">
        {campos.map((c) => {
          const v = valores[c.campo] ?? "";
          const falta = faltaCampo(c, v);
          const lista = `${base}-${c.campo}`;
          const opciones = c.por_omision && !c.valores.includes(c.por_omision)
            ? [c.por_omision, ...c.valores] : c.valores;
          const pista = c.publicado
            ? `en la publicación: ${c.publicado}`
            : c.por_omision ? `vacío = ${c.por_omision}` : "";
          let control: React.ReactNode;
          if (c.tipo === "boolean") {
            const ops = opciones.length ? opciones : ["Sí", "No"];
            control = (
              <select value={v} disabled={soloLectura}
                      onChange={(e) => poner(c.campo, e.target.value)} className={campoCss}>
                <option value="">{pista ? `— (${pista})` : "—"}</option>
                {ops.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            );
          } else if (c.tipo === "number_unit" && c.unidades.length) {
            // Se guarda como «120 kg», que es como ML espera el value_name.
            const [num, ...resto] = v.split(" ");
            const unidad = resto.join(" ")
              || unidadElegida[c.campo]
              || c.unidad_default
              || (c.unidades.length === 1 ? c.unidades[0] : "");
            control = (
              <div className="flex gap-1.5">
                <input type="number" value={num ?? ""} disabled={soloLectura}
                       placeholder={pista} className={campoCss}
                       onChange={(e) => poner(c.campo, e.target.value
                         ? `${e.target.value}${unidad ? ` ${unidad}` : ""}` : "")} />
                <select value={unidad} disabled={soloLectura} className={`${campoCss} w-24`}
                        onChange={(e) => {
                          setUnidadElegida((u) => ({ ...u, [c.campo]: e.target.value }));
                          if (num) poner(c.campo, `${num} ${e.target.value}`.trim());
                        }}>
                  {!unidad && <option value="">unidad…</option>}
                  {c.unidades.map((u) => <option key={u} value={u}>{u}</option>)}
                </select>
              </div>
            );
          } else {
            control = (
              <>
                <input value={v} disabled={soloLectura} placeholder={pista}
                       onChange={(e) => poner(c.campo, e.target.value)}
                       list={opciones.length ? lista : undefined}
                       type={c.tipo === "number" ? "number" : "text"} className={campoCss} />
                {opciones.length > 0 && (
                  <datalist id={lista}>
                    {opciones.map((o) => <option key={o} value={o} />)}
                  </datalist>
                )}
              </>
            );
          }
          return (
            <div key={c.campo} className="grid grid-cols-[minmax(0,11rem)_1fr] items-start gap-2">
              <label className="min-w-0 pt-1.5" title={c.campo}>
                <span className={`block truncate text-xs font-semibold ${falta ? "text-rose-700" : "text-slate-600"}`}>
                  {c.etiqueta}{c.obligatorio && <span className="text-rose-500"> *</span>}
                </span>
                {c.etiqueta !== c.campo && (
                  <span className="block truncate font-mono text-[9px] text-slate-300">{c.campo}</span>
                )}
              </label>
              <div className="min-w-0">
                {control}
                {sinUnidad(c, v) && (
                  <div className="mt-0.5 text-[11px] font-semibold text-amber-700">
                    Elige la unidad: Mercado Libre acepta {c.unidades.join(", ")}.
                  </div>
                )}
                {(c.publicado || (c.por_omision && !soloLectura) || errores?.[c.campo]) && (
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
                    {c.publicado && (
                      <span className="rounded bg-emerald-50 px-1.5 text-emerald-800"
                            title="Lo trae hoy la publicación viva de Mercado Libre. No se copia a kubera hasta que lo guardes.">
                        En ML: {c.publicado}
                        {!soloLectura && v !== c.publicado && (
                          <button type="button" onClick={() => poner(c.campo, c.publicado!)}
                                  className="ml-1 font-semibold underline decoration-dotted hover:text-emerald-950">
                            usar
                          </button>
                        )}
                      </span>
                    )}
                    {c.por_omision && !soloLectura && v !== c.por_omision && (
                      <button type="button" onClick={() => poner(c.campo, c.por_omision!)}
                              className="rounded bg-indigo-50 px-1.5 font-semibold text-indigo-700 hover:bg-indigo-100"
                              title="El valor que pone el publicador si se deja vacío">
                        Usar {c.por_omision}
                      </button>
                    )}
                    {errores?.[c.campo] && (
                      <span className="font-semibold text-rose-600">{errores[c.campo]}</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
