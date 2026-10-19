"use client";

// EditorPrecio.tsx — Cambiar a mano el precio de UNA publicación de ML desde la
// celda «Precio hoy» de PRICING (v0.665.0, pedido de Brandon 19-oct-2026).
//
// Dos pasos a propósito: en la celda se escribe el precio normal y el % de
// descuento; «Revisar en ML» abre una ventana que lee la publicación EN VIVO y
// dice qué va a pasar (qué se quita, qué sigue ganando, avisos). Solo ahí está
// «Publicar en ML». El cambio corre en el servidor (1 a 5 min: ML tarda en
// aceptar un descuento nuevo) y la ventana va mostrando los pasos. Al final:
// «Deshacer», que la regresa a como estaba.
//
// Solo lo ve un admin (el servidor también lo exige).

import { useEffect, useRef, useState } from "react";

import {
  pricingPrecioAplicar,
  pricingPrecioDeshacer,
  pricingPrecioTrabajo,
  pricingPrecioVista,
  type PricingEstadoML,
  type PricingTrabajoPrecio,
  type PricingVistaPrecio,
} from "@/lib/api";

import { pesos } from "./formato";

const fmt2 = (x: number | null | undefined) =>
  x == null ? "—" : "$" + x.toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function resumen(e: PricingEstadoML | null | undefined) {
  if (!e) return "—";
  const pct = e.tachado && e.cobra && e.tachado > e.cobra ? ` (tachado ${fmt2(e.tachado)}, −${Math.round((1 - e.cobra / e.tachado) * 100)} %)` : "";
  return `cobra ${fmt2(e.cobra)}${pct} · normal ${fmt2(e.precio)}`;
}

export default function EditorPrecio({
  id,
  cuenta,
  sku,
  titulo,
  cobra,
  normal,
}: {
  id: string;
  cuenta: string;
  sku: string | null;
  titulo: string;
  cobra: number | null;
  normal: number | null;
}) {
  const [abierto, setAbierto] = useState(false);
  const [precio, setPrecio] = useState<string>("");
  const [pct, setPct] = useState<string>("");
  // Se llena al ABRIR, con el precio de ese momento: la tabla cambia de precio cuando llega la lectura en vivo.
  const abrir = () => {
    const base = normal && cobra && normal > cobra ? normal : cobra ?? normal ?? 0;
    setPrecio(base ? String(Math.round(base * 100) / 100) : "");
    // Con 4 decimales, para que el punto de partida dé EXACTO lo que se cobra hoy (30 % de $220 daría $154, no $153).
    setPct(normal && cobra && normal > cobra ? String(Math.round((1 - cobra / normal) * 1e6) / 1e4) : "");
    setAbierto(true);
  };
  const [salir, setSalir] = useState(false);
  const [vista, setVista] = useState<PricingVistaPrecio | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trabajo, setTrabajo] = useState<PricingTrabajoPrecio | null>(null);
  const dlg = useRef<HTMLDialogElement>(null);

  const nPrecio = parseFloat(precio);
  const nPct = pct.trim() === "" ? 0 : parseFloat(pct);
  const valido = nPrecio > 0 && nPct >= 0 && nPct <= 80;
  const final = valido ? Math.round(nPrecio * (1 - nPct / 100) * 100) / 100 : null;

  // Pregunta cómo va el trabajo mientras corre.
  useEffect(() => {
    if (!trabajo || trabajo.estado !== "corriendo") return;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      pricingPrecioTrabajo(trabajo.id, ctl.signal)
        .then((x) => !ctl.signal.aborted && setTrabajo(x))
        .catch((e: Error) => !ctl.signal.aborted && setTrabajo({ ...trabajo, estado: "error", error: e.message }));
    }, 3000);
    return () => {
      ctl.abort();
      clearTimeout(t);
    };
  }, [trabajo]);

  const revisar = async (salirCampanas = salir) => {
    if (!valido) return;
    setError(null);
    setCargando(true);
    setTrabajo(null);
    dlg.current?.showModal();
    try {
      setVista(await pricingPrecioVista(id, { cuenta, sku, precio: nPrecio, pct: nPct || null, salir: salirCampanas }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCargando(false);
    }
  };

  const publicar = async () => {
    setError(null);
    try {
      setTrabajo(await pricingPrecioAplicar(id, { cuenta, sku, precio: nPrecio, pct: nPct || null, salir }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const deshacer = async () => {
    setError(null);
    try {
      setTrabajo(await pricingPrecioDeshacer(id, { cuenta, sku }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const corriendo = trabajo?.estado === "corriendo";
  const plan = vista?.plan;
  const vivas = vista?.estado.promos.filter((p) => p.estado === "started" || p.estado === "pending") ?? [];

  return (
    <div className="ed-precio" onClick={(e) => e.stopPropagation()}>
      {!abierto ? (
        <button type="button" className="ed-abrir" onClick={abrir} title="Cambiar el precio en Mercado Libre (solo admin)">
          Cambiar precio
        </button>
      ) : (
        <div className="ed-form">
          <label>
            Normal $
            <input type="number" min={1} step="0.01" value={precio} onChange={(e) => setPrecio(e.target.value)} aria-label="Precio normal" />
          </label>
          <label>
            Desc. %
            <input type="number" min={0} max={80} step="any" value={pct} onChange={(e) => setPct(e.target.value)} placeholder="0" aria-label="Descuento en porcentaje" />
          </label>
          <div className="ed-final">{final != null ? <>cobraría <b>{fmt2(final)}</b>{nPct ? <> · <s>{pesos(nPrecio)}</s></> : null}</> : "revisa los números"}</div>
          <div className="ed-botones">
            <button type="button" className="ed-revisar" disabled={!valido} onClick={() => void revisar()}>
              Revisar en ML
            </button>
            <button type="button" className="ed-cancelar" onClick={() => setAbierto(false)}>
              Cancelar
            </button>
          </div>
        </div>
      )}

      <dialog ref={dlg} className="ed-dlg" aria-label={`Cambiar precio de ${titulo}`}>
        <div className="ed-dlg-top">
          <button type="button" className="ed-cerrar" onClick={() => dlg.current?.close()} aria-label="Cerrar">
            ×
          </button>
          <div className="sm">Cambiar precio en Mercado Libre · {cuenta}</div>
          <h3>{titulo}</h3>
          <div className="sm mono">
            {sku} · {id}
          </div>
        </div>
        <div className="ed-dlg-cuerpo">
          {cargando && <p>Leyendo la publicación en Mercado Libre…</p>}
          {error && <p className="ed-error">{error}</p>}

          {vista && !trabajo && plan && (
            <>
              <section>
                <h4>Hoy en Mercado Libre</h4>
                <p>{resumen(vista.estado)}</p>
                {vivas.length > 0 && (
                  <ul className="ed-lista">
                    {vivas.map((p, i) => (
                      <li key={i}>
                        {p.nombre_tipo}
                        {p.nombre ? ` «${p.nombre}»` : ""} · {p.estado === "started" ? "cobrando" : "programada"} {fmt2(p.precio)}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section>
                <h4>Lo que se va a publicar</h4>
                <p>
                  Precio normal <b>{fmt2(plan.precio)}</b>
                  {plan.pct ? (
                    <>
                      {" "}
                      con descuento de <b>{plan.pct} %</b>: se ve <s>{fmt2(plan.precio)}</s> <b>{fmt2(plan.final)}</b>
                    </>
                  ) : (
                    " sin descuento"
                  )}
                  .
                </p>
                <p>
                  El comprador pagará <b>{fmt2(plan.cobrara)}</b>
                  {Math.abs(plan.cobrara - plan.final) > 0.5 ? " porque otra promoción cobra menos" : ""}.
                </p>
                {plan.quitar.length > 0 || vivas.some((p) => p.tipo === "SELLER_CAMPAIGN" || p.tipo === "DEAL") ? (
                  <label className="ed-chk">
                    <input
                      type="checkbox"
                      checked={salir}
                      onChange={(e) => {
                        setSalir(e.target.checked);
                        void revisar(e.target.checked);
                      }}
                    />
                    Sacarla de las campañas que cobran menos que el precio nuevo
                    {plan.quitar.length ? `: ${plan.quitar.map((p) => `«${p.nombre || p.id}» ${fmt2(p.precio)}`).join(", ")}` : ""}
                    <span className="sm"> (al deshacer se intenta volver a meterla; esto todavía no se ha probado en vivo)</span>
                  </label>
                ) : null}
                {plan.avisos.length > 0 && (
                  <ul className="ed-avisos">
                    {plan.avisos.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                )}
              </section>
              <p className="sm">
                Tarda de 1 a 5 minutos: Mercado Libre tarda en aceptar un descuento nuevo. Mientras tanto la publicación puede verse unos
                minutos con su descuento anterior o sin descuento. Queda en la bitácora y se puede deshacer.
              </p>
              <div className="ed-botones">
                <button type="button" className="ed-publicar" onClick={() => void publicar()}>
                  Publicar en ML
                </button>
                {vista.ultimo && (
                  <button type="button" className="ed-cancelar" onClick={() => void deshacer()} title={`Último cambio: ${vista.ultimo.cuando}`}>
                    Deshacer el último cambio{vista.ultimo.quien ? ` (${vista.ultimo.quien})` : ""}
                  </button>
                )}
              </div>
            </>
          )}

          {trabajo && (
            <section>
              <h4>
                {trabajo.tipo === "aplicar" ? "Publicando en Mercado Libre" : "Deshaciendo"}
                {corriendo ? "…" : trabajo.estado === "listo" ? " · listo" : " · no se pudo"}
              </h4>
              <ol className="ed-pasos">
                {trabajo.pasos.map((p, i) => (
                  <li key={i}>
                    {p.paso}
                    {p.http ? ` · ${p.http < 300 ? "ok" : "ML contestó " + p.http}` : ""}
                    {p.ml ? ` (${p.ml})` : ""}
                  </li>
                ))}
                {corriendo && <li className="sm">trabajando…</li>}
              </ol>
              {trabajo.error && <p className="ed-error">{trabajo.error}</p>}
              {trabajo.resultado && (
                <>
                  <p>
                    Antes: {resumen(trabajo.resultado.antes)}
                    <br />
                    <b>Ahora: {resumen(trabajo.resultado.despues)}</b>
                  </p>
                  {trabajo.resultado.avisos.length > 0 && (
                    <ul className="ed-avisos">
                      {trabajo.resultado.avisos.map((a, i) => (
                        <li key={i}>{a}</li>
                      ))}
                    </ul>
                  )}
                  {trabajo.tipo === "aplicar" && (
                    <div className="ed-botones">
                      <button type="button" className="ed-cancelar" onClick={() => void deshacer()}>
                        Deshacer
                      </button>
                    </div>
                  )}
                  <p className="sm">La tabla toma el precio nuevo en su próxima lectura (cada 5 min).</p>
                </>
              )}
            </section>
          )}
        </div>
      </dialog>
    </div>
  );
}
