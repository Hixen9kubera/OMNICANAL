"use client";

/**
 * La confirmación de la planeación: la vista previa EXACTA de lo que se crearía en
 * Odoo POR TIENDA (con el libre releído ahora y ML verificado en vivo), el MODO
 * PRUEBA y, ya creadas, la GUÍA del marketplace de cada orden (Brandon, 24-sep:
 * "registrarlos en Odoo según la cuenta y el marketplace y después se adjunta la
 * guía del marketplace solicitado").
 */

import { useEffect, useState } from "react";
import { CheckCircle2, Download, FileUp, FlaskConical } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import { BotonCerrar, Ceja, Ventana, num } from "./ui";
import type { OrdenCreada, ParametrosFull, ResultadoCrear, ResultadoGuia, Rol, Tienda } from "./tipos";

const ESTADO_ODOO: Record<string, string> = {
  draft: "borrador", sent: "cotización enviada", sale: "confirmada", done: "bloqueada", cancel: "cancelada",
};

export interface PedidoTienda { tienda: Tienda; lineas: { sku: string; cantidad: number; sugerido: number }[] }

export default function ConfirmarFull({ pedidos, semana, params, rol, onDescargar, onCerrar, onCreado }: {
  pedidos: PedidoTienda[]; semana: string; params: ParametrosFull; rol: Rol;
  onDescargar: () => void; onCerrar: () => void; onCreado: () => void;
}) {
  // La clave nace UNA vez por confirmación: reintentar con ella no duplica.
  const [clave] = useState(() => (typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID() : `${Date.now()}${Math.random()}`).replace(/[^0-9a-z]/gi, "").slice(0, 12));
  const [prueba, setPrueba] = useState(true);
  const [previa, setPrevia] = useState<ResultadoCrear | null>(null);
  const [resultado, setResultado] = useState<ResultadoCrear | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creando, setCreando] = useState(false);
  const cuerpo = (p: boolean) => JSON.stringify({ tiendas: pedidos, clave, parametros: params, prueba: p });

  useEffect(() => {
    let vivo = true;
    // El Content-Type va en `extra`: `fetchSesion` arma las cabeceras de cero.
    fetchSesion(`${API_BASE}/api/fulfillment/crear-full/vista-previa`, { method: "POST", body: cuerpo(true) },
                { "Content-Type": "application/json" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
        return r.json() as Promise<ResultadoCrear>;
      })
      .then((d) => { if (vivo) setPrevia(d); })
      .catch((e: unknown) => { if (vivo) setError(e instanceof Error ? e.message : String(e)); });
    return () => { vivo = false; };
    // La vista previa es de ESTA confirmación: no se repite al re-renderizar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const crear = async () => {
    setCreando(true);
    setError(null);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full`, { method: "POST", body: cuerpo(prueba) },
                                  { "Content-Type": "application/json" });
      const d = await r.json().catch(() => ({})) as ResultadoCrear & { detail?: string };
      if (!r.ok) throw new Error(d.detail ?? `HTTP ${r.status}`);
      setResultado(d);
      if (d.accion === "creada") onCreado();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreando(false);
    }
  };

  const v = resultado ?? previa;
  const encendido = !!previa?.interruptor?.encendido;
  const puede = encendido && rol === "admin" && !!previa?.ok && !resultado;
  const porQueNo = !previa ? "" : !previa.ok ? (previa.motivo ?? "No hay nada que crear.")
    : rol !== "admin" ? "Crear en Odoo es de admin. Puedes descargar la planeación o pedir que la creen."
    : !encendido ? "La creación en Odoo está apagada: esto es exactamente lo que se crearía. Enciéndela en «Crear en Odoo»."
    : "";

  return (
    <Ventana etiqueta="Crear FULL por tienda" onCerrar={onCerrar} ancho="max-w-[920px]">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
        <div>
          <Ceja>Planeación semanal · {semana}</Ceja>
          <h3 className="mt-1 text-lg font-extrabold text-slate-900">Crear las órdenes en Odoo</h3>
          <p className="mt-0.5 text-[12.5px] text-slate-500">
            {v ? <>{num(v.piezas ?? 0)} piezas de {num(v.piezas_pedidas ?? 0)} pedidas · una cotización en borrador por
              tienda y almacén · precio 0, sin impuestos · nadie confirma: eso lo hace la KAM en Odoo</>
               : "Releyendo lo libre en Odoo y las publicaciones en Mercado Libre…"}
          </p>
        </div>
        <BotonCerrar onClick={onCerrar} />
      </div>

      <div className="max-h-[62vh] overflow-y-auto px-6 py-4">
        {error && <p className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[12.5px] text-rose-800">{error}</p>}
        {!v && !error && <p className="py-8 text-center text-sm text-slate-400">Armando la vista previa…</p>}

        {resultado?.tiendas && resultado.accion !== "apagado" && (
          <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-[13px] text-emerald-900">
            <div className="flex items-center gap-2 font-bold">
              <CheckCircle2 className="h-4 w-4" />
              {resultado.accion === "ya_existia" ? "Ya estaban creadas: no se duplicó." : "Creadas en Odoo, en borrador."}
              {resultado.prueba && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">PRUEBA</span>}
            </div>
            <p className="mt-1 text-[12px]">
              Siguiente paso: crea el envío en el marketplace y adjunta aquí su número y su guía en PDF.
              {resultado.solicitud_guardada === false && " La solicitud original NO se guardó (falta aplicar la migración 0054)."}
            </p>
          </div>
        )}

        {v?.tiendas?.map((t) => (
          <div key={t.tienda} className="mb-4">
            <div className="flex items-baseline justify-between">
              <span className="text-[14px] font-extrabold text-slate-800">{t.nombre}</span>
              <span className="text-[12px] text-slate-500">socio «{t.socio}» · {num(t.piezas)} de {num(t.piezas_pedidas)} pzs</span>
            </div>
            {t.partes.map((p) => {
              const orden = t.ordenes?.find((o) => o.almacen === p.almacen);
              return (
                <div key={p.almacen_id} className="mt-1.5 rounded-xl border border-slate-200">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/60 px-3 py-1.5">
                    <span className="text-[12.5px] font-bold text-slate-700">
                      Desde {p.almacen} · {p.lineas.length} SKUs · {num(p.piezas)} pzs
                    </span>
                    {orden && <OrdenLink o={orden} />}
                  </div>
                  <div className="max-h-[160px] overflow-y-auto">
                    <table className="w-full text-[12px]">
                      <tbody>
                        {p.lineas.map((l) => (
                          <tr key={l.sku} className="border-t border-slate-100 first:border-t-0">
                            <td className="px-3 py-1 font-mono font-bold text-slate-800">{l.sku}</td>
                            <td className="max-w-[440px] truncate px-3 py-1 text-slate-500">{l.nombre}</td>
                            <td className="px-3 py-1 text-right font-mono font-bold tabular-nums">{num(l.cantidad)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {orden && !orden.ya_existia && rol === "admin" && <GuiaOrden orden={orden} />}
                </div>
              );
            })}
            {t.recortes.length > 0 && (
              <ul className="mt-1.5 list-inside list-disc rounded-lg bg-amber-50 px-3 py-1.5 text-[11.5px] text-amber-900">
                {t.recortes.map((x) => (
                  <li key={`${x.sku}-${x.porque}`}><span className="font-mono">{x.sku}</span>: pediste {num(x.pedidas)}, van {num(x.van)} — {x.porque}</li>
                ))}
              </ul>
            )}
          </div>
        ))}

        {!!v?.avisos?.length && (
          <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-900">
            <b>Mercado Libre en vivo:</b>
            <ul className="mt-1 list-inside list-disc">
              {v.avisos.map((a) => <li key={`${a.tienda}${a.sku}`}><span className="font-mono">{a.sku}</span>: {a.aviso}</li>)}
            </ul>
          </div>
        )}
        {!!v?.no_en_odoo?.length && (
          <p className="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-800">
            No están en Odoo y no se incluyen: <span className="font-mono">{v.no_en_odoo.join(", ")}</span>
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-b-2xl border-t border-slate-100 bg-slate-50/60 px-6 py-3">
        <div className="max-w-[480px]">
          {!resultado && (
            <label className="flex cursor-pointer items-start gap-2 text-[12px] text-slate-700">
              <input type="checkbox" checked={prueba} onChange={(ev) => setPrueba(ev.target.checked)} className="mt-0.5" />
              <span>
                <b className="inline-flex items-center gap-1"><FlaskConical className="h-3.5 w-3.5 text-amber-600" /> Orden de prueba</b>
                {" "}— lleva «PRUEBA · NO CONFIRMAR NI SURTIR» en la referencia y la nota, y no se resta de la siguiente planeación.
              </span>
            </label>
          )}
          <p className="mt-1 text-[11.5px] text-slate-500">{porQueNo}</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={onDescargar}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            <Download className="h-4 w-4" /> Descargar Excel
          </button>
          {resultado ? (
            <button type="button" onClick={onCerrar}
                    className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white hover:bg-indigo-700">
              Cerrar
            </button>
          ) : (
            <button type="button" onClick={() => void crear()} disabled={!puede || creando} title={porQueNo || undefined}
                    className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-300">
              {creando ? "Creando…" : prueba ? "Crear órdenes de prueba en Odoo" : "Crear órdenes en Odoo"}
            </button>
          )}
        </div>
      </div>
    </Ventana>
  );
}

function OrdenLink({ o }: { o: OrdenCreada }) {
  return (
    <span className="text-[12px] text-emerald-800">
      <a href={o.url} target="_blank" rel="noreferrer" className="font-mono font-bold underline">{o.orden}</a>
      {" "}· {ESTADO_ODOO[o.estado] ?? o.estado}{o.ya_existia ? " · ya existía" : ""}
    </span>
  );
}

/**
 * El número del envío del marketplace y su guía en PDF, en la orden de Odoo
 * («Subir guía», la convención de la casa). Sólo en órdenes que creó el panel y
 * que siguen en borrador: el backend lo vuelve a revisar.
 */
export function GuiaOrden({ orden, compacta }: { orden: { id: number; orden: string }; compacta?: boolean }) {
  const [numero, setNumero] = useState("");
  const [pdf, setPdf] = useState<File | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [res, setRes] = useState<ResultadoGuia | null>(null);
  const [error, setError] = useState<string | null>(null);

  const enviar = async () => {
    setEnviando(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("orden_id", String(orden.id));
      fd.append("numero", numero.trim());
      if (pdf) fd.append("pdf", pdf);
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/guia`, { method: "POST", body: fd });
      const d = await r.json().catch(() => ({})) as ResultadoGuia & { detail?: string };
      if (!r.ok) throw new Error(d.detail ?? `HTTP ${r.status}`);
      setRes(d);
      if (!d.ok) setError(d.motivo ?? "no quedó");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setEnviando(false);
    }
  };

  if (res?.ok) {
    return (
      <p className={`${compacta ? "" : "border-t border-slate-100 px-3 py-2"} text-[12px] text-emerald-800`}>
        <CheckCircle2 className="mr-1 inline h-3.5 w-3.5" />
        Guía adjuntada en {res.orden}{res.referencia ? ` · referencia «${res.referencia}»` : ""}{res.pdf ? ` · ${res.pdf}` : ""}
      </p>
    );
  }
  return (
    <div className={`${compacta ? "" : "border-t border-slate-100 px-3 py-2"} flex flex-wrap items-center gap-2 text-[12px]`}>
      <span className="font-semibold text-slate-600">Guía del marketplace:</span>
      <input value={numero} onChange={(ev) => setNumero(ev.target.value)} placeholder="Número del envío"
             className="w-40 rounded-md border border-slate-200 px-2 py-1 font-mono text-[12px]" />
      <label className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-600 hover:bg-slate-50">
        <FileUp className="h-3.5 w-3.5" /> {pdf ? pdf.name.slice(0, 24) : "PDF de la guía"}
        <input type="file" accept="application/pdf" className="hidden"
               onChange={(ev) => setPdf(ev.target.files?.[0] ?? null)} />
      </label>
      <button type="button" onClick={() => void enviar()} disabled={enviando || (!numero.trim() && !pdf)}
              className="rounded-md bg-indigo-600 px-2.5 py-1 font-bold text-white hover:bg-indigo-700 disabled:opacity-40">
        {enviando ? "Adjuntando…" : "Adjuntar"}
      </button>
      {error && <span className="text-rose-700">{error}</span>}
    </div>
  );
}
