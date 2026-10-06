"use client";

/**
 * Los PDF de una orden (el comprobante, la factura, el envío a FULL): la
 * lista, bajar, quitar y subir.
 *
 * LO QUE AQUÍ NO SE GUARDA: guías ni etiquetas con la dirección del comprador
 * (decisión D5 del plan de Eduardo). Se piden al canal al imprimir y no se
 * quedan en kubera. La base no puede saber qué hay dentro de un PDF —el `tipo`
 * no lo impide—, así que la regla se dice AQUÍ, fija y a la vista, justo donde
 * alguien está a punto de arrastrar el archivo.
 *
 * Por qué el TIPO se elige antes de subir y no trae uno por omisión:
 * `ops.ov_archivos.tipo` no tiene default (comprobante / factura / envío a
 * FULL). Un valor preelegido acabaría etiquetando como «comprobante» todo lo
 * que alguien suba con prisa; sin elegirlo, la zona de subida no recibe nada.
 * Y se suelta después de cada subida: el siguiente PDF es otra decisión.
 *
 * Por qué la zona de subida puede estar APAGADA con todo y permisos: el binario
 * va a Storage, al bucket `ordenes-venta`, que la migración ya no crea. Mientras
 * no exista, `ov_archivos` no tiene escritor (`modulo.archivos.disponible`). No
 * se esconde: se pinta apagada y dice por qué, para que nadie crea que es su
 * usuario el que no puede.
 *
 * Por qué NADA aquí es un enlace: el bucket es privado y el archivo sólo sale
 * por el backend CON sesión. Un `<a href>` no manda el token; se baja con
 * `bajarArchivo`. Y BAJAR tiene su permiso (`permisos.bajar_archivo`): ver que
 * la orden tiene una factura adjunta es de cualquiera; abrirla, de operador o
 * admin. El botón apagado dice por qué. Si el permiso no llega (un backend
 * anterior), queda apagado: se falla cerrado.
 *
 * Por qué se valida ANTES de mandar: el bucket sólo acepta `application/pdf`
 * de hasta 15 MB. Subir 40 MB para que el servidor conteste 413 es hacer
 * esperar a almacén por un error que ya se sabía en el navegador.
 *
 * Con `orden = null` (la orden todavía no se crea) no hay dónde colgar el
 * archivo —la ruta es `<folio>/<sha256>.pdf`—, y eso es justo lo que dice.
 *
 * `Confirmacion` se exporta: es la pregunta de «¿seguro?» SIN motivo que
 * también usa el documento (salir sin guardar, «¿salió?»). Las que sí dejan
 * motivo en la bitácora van con `DialogoMotivo` de ui.tsx.
 */

import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Download, FileText, Loader2, Lock, ShieldAlert, Trash2, Upload } from "lucide-react";
import { mensajeDeError } from "@/lib/api";
import { BotonCerrar, Ventana } from "@/components/fulfillment/ui";
import { bajarArchivo, borrarArchivo, subirArchivo } from "./api";
import { Boton, fechaHora, pesoArchivo, quien } from "./ui";
import type { Archivo, EstadoModulo, Orden, TipoArchivo } from "./tipos";

/** El tope del bucket `ordenes-venta` (guía de la 0064/0065, §5.2 punto 13): 15 MB. */
const MAX_BYTES = 15 * 1024 * 1024;

/** El catálogo CERRADO de `ops.ov_archivos.tipo`, en el orden en que más se usan. */
export const TIPOS_ARCHIVO: { id: TipoArchivo; rotulo: string; ayuda: string }[] = [
  { id: "comprobante", rotulo: "Comprobante", ayuda: "Comprobante de pago o de entrega a la paquetería." },
  { id: "factura", rotulo: "Factura", ayuda: "La factura de la venta." },
  { id: "envio_full", rotulo: "Envío a FULL", ayuda: "El documento del envío al almacén del marketplace." },
];

/** Cómo se llama un tipo en pantalla. Uno que el catálogo no conoce se enseña tal cual, no se esconde. */
export function rotuloTipoArchivo(tipo: string | null | undefined): string {
  return TIPOS_ARCHIVO.find((t) => t.id === tipo)?.rotulo ?? (tipo || "Sin tipo");
}

const SIN_BUCKET = "Todavía no se pueden adjuntar PDF: falta crear el almacenamiento (bucket «ordenes-venta»).";

/** Por qué NO se puede subir ese archivo, o `null` si va. */
export function rechazo(f: Pick<File, "name" | "type" | "size">): string | null {
  if (!/\.pdf$/i.test(f.name)) return `«${f.name}» no es un PDF: sólo se adjuntan archivos .pdf.`;
  // Algunos navegadores no informan el tipo: sólo se rechaza si dicen OTRO.
  if (f.type && f.type !== "application/pdf") return `«${f.name}» no es un PDF de verdad (${f.type}).`;
  if (f.size === 0) return `«${f.name}» está vacío.`;
  if (f.size > MAX_BYTES) return `«${f.name}» pesa ${pesoArchivo(f.size)}: el máximo es 15 MB.`;
  return null;
}

/**
 * Si se puede subir un PDF a esta orden y, si no, POR QUÉ. Manda primero lo que
 * no depende de la persona: sin bucket no hay escritor, tenga el permiso que
 * tenga quien mira, y decirle «tu usuario no puede» sería mentirle.
 *
 * El `!== false` deja pasar a un backend que todavía no manda `archivos`: ahí
 * decide `permisos.subir_archivo`, que ya lo trae incluido.
 */
export function subidaDe(orden: Pick<Orden, "permisos">,
                         modulo: Pick<EstadoModulo, "archivos">): { puede: boolean; porque: string } {
  const hayDonde = modulo.archivos?.disponible !== false;
  if (!hayDonde) return { puede: false, porque: modulo.archivos?.motivo || SIN_BUCKET };
  if (!orden.permisos.subir_archivo) {
    return { puede: false,
             porque: orden.permisos.porque.subir_archivo || "Tu usuario no puede adjuntar PDF a esta orden." };
  }
  return { puede: true, porque: "" };
}

/**
 * Los botones de la confirmación. Es un componente APARTE por el foco: `Ventana`
 * pinta a sus hijos un render después de montarse, así que un efecto de
 * `Confirmacion` correría cuando los botones todavía no existen. Éste se monta
 * CON ellos, y su efecto sí los encuentra.
 *
 * Al abrir, el foco va al botón de la acción (el último): con teclado, quien
 * dio Enter en un botón confirma con otro Enter o se arrepiente con Esc, en
 * vez de tabular por toda la página que quedó detrás. La excepción es lo que
 * DESTRUYE o no tiene vuelta (`seguro`: salir sin guardar, quitar un PDF, «sí
 * salió»): ahí el foco nace en «volver» (el primero), para que un Enter de más
 * no pierda nada; la acción queda a un Tab.
 *
 * La guarda de `repeat` es la otra mitad: el Enter que ABRIÓ la ventana, si se
 * queda apretado, se repite ya sobre el botón enfocado y confirmaría solo.
 *
 * Se exporta porque el diálogo de la entrega (OrdenDocumento) tiene su propio
 * cuerpo pero el mismo pie.
 */
export function PieConfirmacion({ seguro, children }: { seguro: boolean; children: ReactNode }) {
  const pie = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const botones = pie.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
    botones?.[seguro ? 0 : botones.length - 1]?.focus();
    // Sólo al abrir: el foco no se le quita a la persona si la ventana se repinta.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div ref={pie} className="flex flex-wrap justify-end gap-2 border-t border-slate-100 px-5 py-3"
         onKeyDownCapture={(ev) => { if (ev.key === "Enter" && ev.repeat) ev.preventDefault(); }}>
      {children}
    </div>
  );
}

/**
 * La pregunta de «¿seguro?» sin motivo. Esc y clic fuera = no; el foco nace en
 * la acción, salvo en lo que no tiene vuelta (`seguro`), donde nace en «volver».
 */
export function Confirmacion({
  titulo, texto, accion, tono = "primario", volver = "No, regresar", ocupado, onConfirmar, onCerrar, extra,
  seguro,
}: {
  titulo: string;
  texto: ReactNode;
  /** El verbo del botón: «Sí salió», «Salir sin guardar». */
  accion: string;
  tono?: "primario" | "peligro" | "exito";
  volver?: string;
  ocupado?: boolean;
  onConfirmar: () => void;
  onCerrar: () => void;
  /** Un tercer botón entre «volver» y la acción (p. ej. «Guardar y salir»). */
  extra?: ReactNode;
  /**
   * El foco nace en «volver» y no en la acción. Por omisión lo decide el tono
   * (`peligro`); se da aparte cuando la acción NO es roja pero tampoco se puede
   * deshacer («Sí salió»: baja el físico de la bodega).
   */
  seguro?: boolean;
}) {
  return (
    <Ventana onCerrar={onCerrar} etiqueta={titulo} ancho="max-w-md">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <h2 className="text-base font-extrabold tracking-tight text-slate-900">{titulo}</h2>
        <BotonCerrar onClick={onCerrar} />
      </div>
      <div className="px-5 py-4 text-sm leading-relaxed text-slate-600">{texto}</div>
      <PieConfirmacion seguro={seguro ?? tono === "peligro"}>
        <Boton onClick={onCerrar} tono="fantasma">{volver}</Boton>
        {extra}
        <Boton onClick={onConfirmar} tono={tono} ocupado={ocupado}>{accion}</Boton>
      </PieConfirmacion>
    </Ventana>
  );
}

export function ArchivosOrden({ orden, modulo, onCambio }: {
  orden: Orden | null;
  /** De aquí sale si hay dónde guardar los PDF (`archivos.disponible`) y, si no, por qué. */
  modulo: EstadoModulo;
  /** Subir o quitar contesta la orden entera, ya con su lista de archivos. */
  onCambio: (o: Orden) => void;
}): JSX.Element {
  const [tipo, setTipo] = useState<TipoArchivo | "">("");
  const [subiendo, setSubiendo] = useState<{ nombre: string; i: number; n: number } | null>(null);
  const [bajando, setBajando] = useState<number | null>(null);
  const [porQuitar, setPorQuitar] = useState<Archivo | null>(null);
  const [quitando, setQuitando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [encima, setEncima] = useState(false);
  const selector = useRef<HTMLInputElement>(null);
  // dragenter/dragleave se disparan también al pasar por los hijos: se cuentan.
  const dentro = useRef(0);
  const vivo = useRef(true);
  useEffect(() => {
    vivo.current = true;
    return () => { vivo.current = false; };
  }, []);

  const ordenId = orden?.id ?? null;
  // Otra orden en la misma pantalla: lo que quedó a medias no es de ésta.
  useEffect(() => { setError(null); setPorQuitar(null); setTipo(""); }, [ordenId]);

  if (!orden) {
    // Sin bucket no tiene caso prometer «guarda y adjunta»: se dice lo que hay.
    const sinDonde = modulo.archivos?.disponible === false;
    return (
      <div className="rounded-xl border border-dashed border-slate-200 px-4 py-6 text-center">
        <FileText className="mx-auto h-6 w-6 text-slate-300" />
        <p className="mt-2 text-sm font-semibold text-slate-600">
          {sinDonde ? "Todavía no se pueden adjuntar PDF" : "Guarda la orden para poder adjuntar su PDF"}
        </p>
        <p className="mx-auto mt-0.5 max-w-md text-xs leading-snug text-slate-400">
          {sinDonde ? (modulo.archivos?.motivo || SIN_BUCKET)
            : "El comprobante o la factura se cuelgan del folio, y todavía no hay uno."}
        </p>
      </div>
    );
  }

  const subida = subidaDe(orden, modulo);
  const puedeQuitar = orden.permisos.borrar_archivo;
  // `=== true`: si el backend no manda el permiso, no se baja (falla cerrado).
  const puedeBajar = orden.permisos.bajar_archivo === true;
  const porqueNoBaja = orden.permisos.porque.bajar_archivo || "Tu usuario no puede bajar los PDF de una orden.";
  // El id viaja en una constante: las funciones de abajo terminan después de
  // un `await`, y para entonces la prop ya puede ser otra orden.
  const id = orden.id;

  const subir = async (lista: FileList | File[] | null) => {
    const archivos = Array.from(lista ?? []);
    if (!archivos.length || subiendo || !subida.puede) return;
    setError(null);
    // Sin tipo no se manda nada: la columna no tiene valor por omisión.
    if (!tipo) {
      setError("Elige primero qué es el PDF: comprobante, factura o envío a FULL.");
      return;
    }
    // Primero se revisan TODOS: si uno no pasa, no se sube la mitad.
    const malo = archivos.map(rechazo).find((r) => r !== null);
    if (malo) { setError(malo); return; }
    let subidos = 0;
    for (let i = 0; i < archivos.length; i += 1) {
      const f = archivos[i];
      setSubiendo({ nombre: f.name, i: i + 1, n: archivos.length });
      try {
        const r = await subirArchivo(id, f, tipo);
        subidos += 1;
        onCambio(r.orden);
      } catch (e: unknown) {
        if (vivo.current) setError(mensajeDeError(e, `No se pudo subir «${f.name}» (el servidor no contestó).`));
        break;
      }
    }
    if (vivo.current) {
      setSubiendo(null);
      // El siguiente PDF es otra decisión: el tipo no se queda puesto. Si falló
      // alguno, se conserva para reintentar lo mismo sin volver a elegir.
      if (subidos === archivos.length) setTipo("");
    }
    // Para que elegir el MISMO archivo otra vez vuelva a disparar el cambio.
    if (selector.current) selector.current.value = "";
  };

  const bajar = async (a: Archivo) => {
    if (bajando !== null || !puedeBajar) return;
    setBajando(a.id);
    setError(null);
    try {
      await bajarArchivo(id, a.id, a.nombre);
    } catch (e: unknown) {
      if (vivo.current) setError(mensajeDeError(e, `No se pudo bajar «${a.nombre}».`));
    } finally {
      if (vivo.current) setBajando(null);
    }
  };

  const quitar = async () => {
    if (!porQuitar || quitando) return;
    setQuitando(true);
    setError(null);
    try {
      const r = await borrarArchivo(id, porQuitar.id);
      onCambio(r.orden);
      if (vivo.current) setPorQuitar(null);
    } catch (e: unknown) {
      if (vivo.current) {
        setPorQuitar(null);
        setError(mensajeDeError(e, `No se pudo quitar «${porQuitar.nombre}».`));
      }
    } finally {
      if (vivo.current) setQuitando(false);
    }
  };

  const arrastre = {
    onDragEnter: (ev: DragEvent<HTMLDivElement>) => {
      ev.preventDefault();
      dentro.current += 1;
      setEncima(true);
    },
    onDragOver: (ev: DragEvent<HTMLDivElement>) => ev.preventDefault(),
    onDragLeave: () => {
      dentro.current = Math.max(0, dentro.current - 1);
      if (dentro.current === 0) setEncima(false);
    },
    onDrop: (ev: DragEvent<HTMLDivElement>) => {
      ev.preventDefault();
      dentro.current = 0;
      setEncima(false);
      void subir(ev.dataTransfer.files);
    },
  };

  const elegido = TIPOS_ARCHIVO.find((t) => t.id === tipo) ?? null;

  return (
    <div className="space-y-3">
      {orden.archivos.length > 0 ? (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200">
          {orden.archivos.map((a) => (
            <li key={a.id} className="flex items-center gap-3 px-3 py-2.5">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-rose-50 text-rose-500">
                <FileText className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="flex min-w-0 items-center gap-2">
                  <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10.5px] font-bold text-slate-600 ring-1 ring-slate-200">
                    {rotuloTipoArchivo(a.tipo)}
                  </span>
                  <span className="truncate text-sm font-semibold text-slate-800" title={a.nombre}>{a.nombre}</span>
                </p>
                <p className="mt-0.5 truncate text-xs text-slate-400">
                  {pesoArchivo(a.bytes)} · {quien(a.subido_nombre, a.subido_por)} · {fechaHora(a.subido_at)}
                </p>
              </div>
              {/* Sin permiso el botón NO se esconde: se apaga y dice por qué. */}
              <button type="button" onClick={() => void bajar(a)} disabled={!puedeBajar || bajando !== null}
                      title={puedeBajar ? "Bajar el PDF" : porqueNoBaja}
                      aria-label={puedeBajar ? `Bajar ${a.nombre}` : `Bajar ${a.nombre} (no disponible: ${porqueNoBaja})`}
                      className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-slate-400">
                {bajando === a.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              </button>
              {puedeQuitar && (
                <button type="button" onClick={() => setPorQuitar(a)} disabled={quitando}
                        title="Quitar el PDF" aria-label={`Quitar ${a.nombre}`}
                        className="rounded-lg p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50">
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : !subida.puede ? (
        // Con la zona de subida encendida, ella misma dice que está vacío.
        <p className="text-sm text-slate-400">Esta orden no tiene PDF adjuntos.</p>
      ) : null}

      {orden.archivos.length > 0 && !puedeBajar && (
        <p className="text-xs text-slate-400">No puedes bajar estos PDF. {porqueNoBaja}</p>
      )}

      {/* El aviso es FIJO: no se cierra y no depende de que hoy se pueda subir. */}
      <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[12.5px] leading-snug text-amber-900 ring-1 ring-amber-200">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
        <span>
          <b>No subas guías con la dirección del comprador: no se guardan aquí.</b>{" "}
          Sólo comprobantes, facturas y el documento del envío a FULL.
        </span>
      </p>

      {subida.puede ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span id="ov-tipo-pdf" className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
              ¿Qué es el PDF?
            </span>
            <div role="radiogroup" aria-labelledby="ov-tipo-pdf"
                 className="flex flex-wrap items-center gap-0.5 rounded-lg bg-white p-0.5 ring-1 ring-slate-200">
              {TIPOS_ARCHIVO.map((t) => (
                <button key={t.id} type="button" role="radio" aria-checked={tipo === t.id} title={t.ayuda}
                        disabled={!!subiendo} onClick={() => { setTipo(t.id); setError(null); }}
                        className={`rounded-md px-2.5 py-1 text-xs font-bold transition disabled:cursor-not-allowed disabled:opacity-60 ${
                          tipo === t.id ? "bg-indigo-600 text-white"
                            : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"}`}>
                  {t.rotulo}
                </button>
              ))}
            </div>
          </div>
          <div {...arrastre}
               className={`rounded-xl border border-dashed px-4 py-5 text-center transition ${
                 encima ? "border-indigo-400 bg-indigo-50"
                   : elegido ? "border-slate-300 bg-slate-50/50" : "border-slate-200 bg-slate-50/30"}`}>
            <input ref={selector} type="file" accept="application/pdf,.pdf" multiple className="hidden"
                   onChange={(ev) => void subir(ev.target.files)} />
            {subiendo ? (
              <p className="flex items-center justify-center gap-2 text-sm text-slate-600">
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-indigo-600" />
                <span className="truncate">
                  Subiendo «{subiendo.nombre}»{subiendo.n > 1 ? ` (${subiendo.i} de ${subiendo.n})` : ""}…
                </span>
              </p>
            ) : !elegido ? (
              <>
                <Upload className="mx-auto h-5 w-5 text-slate-300" />
                <p className="mt-1.5 text-sm text-slate-500">Elige arriba qué es el PDF y después arrástralo aquí.</p>
                <p className="mt-0.5 text-xs text-slate-400">Sólo PDF, hasta 15 MB cada uno.</p>
              </>
            ) : (
              <>
                <Upload className={`mx-auto h-5 w-5 ${encima ? "text-indigo-600" : "text-slate-400"}`} />
                <p className="mt-1.5 text-sm text-slate-600">
                  {encima ? "Suelta el PDF aquí"
                    : <>Arrastra aquí el PDF (<b className="font-semibold">{elegido.rotulo}</b>), o{" "}
                      <button type="button" onClick={() => selector.current?.click()}
                              className="font-semibold text-indigo-600 hover:underline">elige un archivo</button></>}
                </p>
                <p className="mt-0.5 text-xs text-slate-400">Sólo PDF, hasta 15 MB cada uno.</p>
              </>
            )}
          </div>
        </div>
      ) : (
        // Apagada, no escondida: quien mira tiene que saber POR QUÉ no puede.
        <div aria-disabled="true"
             className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-center">
          <Lock className="mx-auto h-5 w-5 text-slate-300" />
          <p className="mt-1.5 text-sm font-semibold text-slate-500">No se pueden adjuntar PDF</p>
          <p className="mx-auto mt-0.5 max-w-md text-xs leading-snug text-slate-400">{subida.porque}</p>
        </div>
      )}

      {error && <p role="alert" className="text-xs font-medium text-rose-600">{error}</p>}

      {porQuitar && (
        <Confirmacion
          titulo="Quitar el PDF"
          texto={<>Se quita <b className="text-slate-800">{porQuitar.nombre}</b> ({rotuloTipoArchivo(porQuitar.tipo)})
            de {orden.folio}. El archivo se borra del almacenamiento; en la bitácora de la orden queda quién lo quitó.</>}
          accion="Sí, quitar" tono="peligro" ocupado={quitando}
          onConfirmar={() => void quitar()} onCerrar={() => { if (!quitando) setPorQuitar(null); }}
        />
      )}
    </div>
  );
}
