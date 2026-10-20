"use client";

// Ofertas.tsx — «Ofertas de ML» en PRICING (v0.672.0, pedido de Brandon 20-oct-2026): qué relámpagos, Buen Fin,
// campañas propias y otras campañas de ML se pueden tomar, y tomarlas o quitarlas desde aquí.
//
// Ver lo puede quien ve PRICING; tomar y quitar, solo un admin (el servidor también lo exige). Cada clic es UN
// POST o UN DELETE a ML: el servidor relee la publicación antes, comprueba después y lo deja en la bitácora.
// Las reglas de ML que la pantalla explica (medidas en vivo el 20-oct):
//   · Relámpago: ML escoge el horario (12 h) y aparta las piezas; solo se quita mientras está programada.
//   · Campaña propia: ML no deja entrar a una publicación con descuento individual activo.
//   · Buen Fin: acepta aunque tenga descuento individual; se quita mientras no empiece.

import { useEffect, useMemo, useRef, useState } from "react";

import {
  mensajeDeError,
  pricingOfertaQuitar,
  pricingOfertas,
  pricingOfertaTomar,
  type PricingOfertaFila,
  type PricingOfertas,
} from "@/lib/api";

import { pesos } from "./formato";
import MotivoSelect, { motivoListo } from "./MotivoSelect";

const CUENTAS = ["BEKURA", "SANCORFASHION"] as const;
const NOMBRE_CUENTA: Record<string, string> = { BEKURA: "Kubera", SANCORFASHION: "San Corpe" };

type Pestana = "relampago" | "buenfin" | "propia" | "ml";
const PESTANAS: [Pestana, string][] = [
  ["relampago", "Relámpago"],
  ["buenfin", "Buen Fin"],
  ["propia", "Campaña propia"],
  ["ml", "Otras campañas de ML"],
];
type Vista = "pueden" | "inscritas" | "no";

const esBuenFin = (nombre: string) => /buen\s*fin/i.test(nombre);
const dePestana = (f: { tipo: string; promo: string }, p: Pestana) =>
  p === "relampago"
    ? f.tipo === "LIGHTNING"
    : p === "propia"
      ? f.tipo === "SELLER_CAMPAIGN"
      : p === "buenfin"
        ? f.tipo === "DEAL" && esBuenFin(f.promo)
        : f.tipo === "DEAL" && !esBuenFin(f.promo);

const AYUDA: Record<Pestana, string> = {
  relampago:
    "ML escoge el horario (bloques de 12 h) y aparta las piezas que pongas. Se puede quitar solo mientras está programada: ya activa, ML no deja quitarla.",
  buenfin:
    "Se inscribe con un precio dentro del rango que da ML. Acepta publicaciones con descuento individual y se puede quitar mientras no empiece.",
  propia:
    "Tus campañas (ALWAYS ON, Octubre…). ML NO deja entrar a una publicación con descuento individual activo: primero hay que quitárselo con «Cambiar precio» (Desc. 0 %).",
  ml: "Las demás campañas de ML (Activa tus descuentos, clearance de Full). Mismo trato que Buen Fin.",
};

const fmt2 = (x: number | null | undefined) =>
  x == null ? "—" : "$" + x.toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** «2026-10-22 00:00» → «22 oct 00:00». */
function cuando(s: string | null | undefined) {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T") + ":00");
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString("es-MX", { day: "numeric", month: "short" }) + " " + s.slice(11, 16);
}

const llave = (f: PricingOfertaFila) => [f.cuenta, f.item, f.promo_id, f.estado, f.inicio || ""].join("|");

type Carga = PricingOfertas | "cargando" | { error: string };

export default function Ofertas({ abierto, onCerrar, esAdmin }: { abierto: boolean; onCerrar: () => void; esAdmin: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [datos, setDatos] = useState<Record<string, Carga>>({});
  const [pestana, setPestana] = useState<Pestana>("relampago");
  const [cuenta, setCuenta] = useState<"todas" | (typeof CUENTAS)[number]>("todas");
  const [vista, setVista] = useState<Vista>("pueden");
  const [buscar, setBuscar] = useState("");
  const [lecturas, setLecturas] = useState(0);
  const fresco = useRef(false);
  const datosRef = useRef(datos);
  datosRef.current = datos;

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (abierto && !d.open) d.showModal();
    if (!abierto && d.open) d.close();
  }, [abierto]);

  // Se lee al abrir (solo lo que falta) y con «Volver a leer» (todo, pidiendo al servidor que relea ML); el
  // servidor recuerda 10 min. Cerrar a media lectura la cancela y al reabrir se vuelve a pedir.
  useEffect(() => {
    if (!abierto) return;
    const releyendo = fresco.current;
    fresco.current = false;
    const pendientes = CUENTAS.filter((c) => {
      const d = datosRef.current[c];
      return releyendo || !d || d === "cargando" || "error" in d;
    });
    if (!pendientes.length) return;
    const ctl = new AbortController();
    pendientes.forEach((c) => {
      setDatos((prev) => ({ ...prev, [c]: "cargando" }));
      pricingOfertas(c, releyendo, ctl.signal)
        .then((d) => !ctl.signal.aborted && setDatos((prev) => ({ ...prev, [c]: d })))
        .catch((e) => !ctl.signal.aborted && setDatos((prev) => ({ ...prev, [c]: { error: mensajeDeError(e, "No se pudieron leer las ofertas.") } })));
    });
    return () => ctl.abort();
  }, [abierto, lecturas]);

  const releer = () => {
    fresco.current = true;
    setLecturas((n) => n + 1);
  };

  const cuentas = cuenta === "todas" ? [...CUENTAS] : [cuenta];
  const listas = cuentas.map((c) => datos[c]).filter((d): d is PricingOfertas => !!d && d !== "cargando" && !("error" in d));
  const cargando = cuentas.some((c) => datos[c] === "cargando" || datos[c] === undefined);
  const errores = cuentas
    .map((c) => {
      const d = datos[c];
      return d && d !== "cargando" && "error" in d ? `${NOMBRE_CUENTA[c]}: ${d.error}` : null;
    })
    .filter(Boolean) as string[];

  const todas = useMemo(() => listas.flatMap((d) => d.filas).filter((f) => dePestana(f, pestana)), [listas, pestana]);
  const cuentaVista = (f: PricingOfertaFila): Vista =>
    f.estado !== "candidate" ? "inscritas" : f.puede === false ? "no" : "pueden";
  const n: Record<Vista, number> = { pueden: 0, inscritas: 0, no: 0 };
  todas.forEach((f) => (n[cuentaVista(f)] += 1));
  const q = buscar.trim().toLowerCase();
  const filas = todas
    .filter((f) => cuentaVista(f) === vista)
    .filter((f) => !q || (f.sku || "").toLowerCase().includes(q) || (f.titulo || "").toLowerCase().includes(q) || f.item.toLowerCase().includes(q))
    .sort((a, b) => b.u30 - a.u30 || (b.stock ?? 0) - (a.stock ?? 0) || (a.inicio || "").localeCompare(b.inicio || ""));
  const promos = listas.flatMap((d) => d.promos.filter((p) => dePestana({ tipo: p.tipo, promo: p.nombre }, pestana)).map((p) => ({ ...p, cuenta: d.cuenta })));

  const actualizar = (vieja: PricingOfertaFila, cambio: Partial<PricingOfertaFila>) =>
    setDatos((prev) => {
      const d = prev[vieja.cuenta];
      if (!d || d === "cargando" || "error" in d) return prev;
      return { ...prev, [vieja.cuenta]: { ...d, filas: d.filas.map((f) => (llave(f) === llave(vieja) ? { ...f, ...cambio } : f)) } };
    });

  return (
    <dialog
      ref={ref}
      className="of-dlg"
      aria-labelledby="of-titulo"
      onClose={onCerrar}
      onClick={(e) => {
        if (e.target === ref.current) ref.current?.close();
      }}
    >
      <div className="dlg-cab">
        <div>
          <h3 id="of-titulo">Ofertas de Mercado Libre</h3>
          <div className="sm">
            Relámpago, Buen Fin, campaña propia y otras campañas de ML de las dos cuentas
            {listas.length ? ` · leído ${listas.map((d) => d.leido.slice(11, 16)).join(" y ")}` : ""}
            {esAdmin ? " · tomar y quitar escribe en ML al momento y queda en la bitácora" : " · solo ver (tomar y quitar es de admin)"}
          </div>
        </div>
        <div className="of-cab-botones">
          <button type="button" className="ed-cancelar" onClick={releer} disabled={cargando}>
            {cargando ? "Leyendo ML…" : "Volver a leer"}
          </button>
          <button className="cerrar" type="button" onClick={() => ref.current?.close()}>
            Cerrar
          </button>
        </div>
      </div>

      <div className="of-cuerpo">
        <div className="of-filtros">
          <div className="seg" role="tablist" aria-label="Tipo de oferta">
            {PESTANAS.map(([k, t]) => (
              <button key={k} type="button" role="tab" aria-selected={pestana === k} aria-pressed={pestana === k} onClick={() => setPestana(k)}>
                {t}
              </button>
            ))}
          </div>
          <div className="seg" aria-label="Cuenta">
            {(["todas", ...CUENTAS] as const).map((c) => (
              <button key={c} type="button" aria-pressed={cuenta === c} onClick={() => setCuenta(c)}>
                {c === "todas" ? "Las dos" : NOMBRE_CUENTA[c]}
              </button>
            ))}
          </div>
          <div className="seg" aria-label="Estado">
            {(
              [
                ["pueden", "Pueden entrar"],
                ["inscritas", "Inscritas"],
                ["no", "No pueden"],
              ] as [Vista, string][]
            ).map(([k, t]) => (
              <button key={k} type="button" aria-pressed={vista === k} onClick={() => setVista(k)}>
                {t}
                <span className="cnt">{n[k]}</span>
              </button>
            ))}
          </div>
          <input
            className="of-buscar"
            type="search"
            value={buscar}
            onChange={(e) => setBuscar(e.target.value)}
            placeholder="SKU o título"
            aria-label="Buscar por SKU o título"
          />
        </div>

        <p className="of-ayuda">{AYUDA[pestana]}</p>

        {promos.length > 0 && (
          <ul className="of-promos">
            {promos.map((p) => (
              <li key={p.cuenta + p.id}>
                <b>{p.tipo === "LIGHTNING" ? "Relámpago" : p.nombre}</b> · {NOMBRE_CUENTA[p.cuenta]}
                {p.inicio ? ` · ${cuando(p.inicio)} → ${cuando(p.fin)}` : ""}
                {p.limite && p.estado === "pending" ? ` · inscribir hasta ${cuando(p.limite)}` : ""}
                {" · "}
                <span className="mono">
                  {p.conteo.started || 0} activas · {p.conteo.pending || 0} programadas · {p.conteo.candidate || 0} candidatas
                </span>
              </li>
            ))}
          </ul>
        )}

        {errores.map((e) => (
          <p key={e} className="ed-error">
            {e}
          </p>
        ))}
        {cargando && <p className="sm">Leyendo las ofertas en Mercado Libre (10 a 25 s la primera vez)…</p>}
        {!cargando && !filas.length && !errores.length && <p className="sm">No hay publicaciones en esta vista.</p>}

        {filas.length > 0 && (
          <div className="of-tabla-env">
            <table className="of-tabla">
              <thead>
                <tr>
                  <th>Publicación</th>
                  <th className="der">Stock · 30 d</th>
                  <th className="der">Hoy cobra</th>
                  <th className="der">Oferta</th>
                  <th>Estado</th>
                  {esAdmin && <th className="der">Acción</th>}
                </tr>
              </thead>
              <tbody>
                {filas.slice(0, 400).map((f) => (
                  <FilaOferta key={llave(f)} f={f} esAdmin={esAdmin} onCambio={(c) => actualizar(f, c)} />
                ))}
              </tbody>
            </table>
            {filas.length > 400 && <p className="sm">Se muestran 400 de {filas.length}: usa el buscador para encontrar el resto.</p>}
          </div>
        )}
      </div>
    </dialog>
  );
}

function FilaOferta({
  f,
  esAdmin,
  onCambio,
}: {
  f: PricingOfertaFila;
  esAdmin: boolean;
  onCambio: (c: Partial<PricingOfertaFila>) => void;
}) {
  const [modo, setModo] = useState<"nada" | "tomar" | "quitar">("nada");
  const [precio, setPrecio] = useState("");
  const [piezas, setPiezas] = useState("");
  const [trabajando, setTrabajando] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; texto: string } | null>(null);
  // Por qué (v0.673.0): va a la bitácora de pricing. Se propone el más común; se puede cambiar.
  const [motivo, setMotivo] = useState(esBuenFin(f.promo) ? "temporada" : "campana_canal");
  const [motivoTexto, setMotivoTexto] = useState("");

  const ofertaPrecio = f.estado === "candidate" ? f.sugerido : f.precio;
  const vsHoy = ofertaPrecio && f.cobra ? ofertaPrecio / f.cobra - 1 : null;
  const nPrecio = parseFloat(precio);
  const nPiezas = parseInt(piezas, 10);
  const fuera = (f.min != null && nPrecio < f.min - 0.005) || (f.max != null && nPrecio > f.max + 0.005);
  const valido =
    nPrecio > 0 && !fuera && (f.tipo !== "LIGHTNING" || (nPiezas > 0 && (!f.stock_min || nPiezas >= f.stock_min) && (!f.stock_max || nPiezas <= f.stock_max)));

  const abrirTomar = () => {
    const base = f.sugerido ?? f.max ?? f.min ?? null;
    setPrecio(base ? String(base) : "");
    setPiezas(f.stock_min ? String(f.stock_min) : "");
    setMsg(null);
    setModo("tomar");
  };

  const pedido = {
    item: f.item,
    cuenta: f.cuenta,
    tipo: f.tipo,
    promo_id: f.promo_id,
    sku: f.sku,
    motivo,
    motivo_texto: motivoTexto.trim() || null,
    // Lo que se veía en la fila al decidir (plano): va a la bitácora de pricing.
    contexto: {
      cobra_hoy: f.cobra,
      stock: f.stock,
      ventas_30d: f.u30,
      sugerido: f.sugerido,
      min: f.min,
      max: f.max,
      tipo_oferta: f.tipo,
      oferta: f.promo,
      estado_oferta: f.estado,
      motivo_bloqueo: f.motivo,
    },
  };
  const conMotivo = motivoListo(motivo, motivoTexto);

  const tomar = async () => {
    setTrabajando(true);
    setMsg(null);
    try {
      const r = await pricingOfertaTomar({ ...pedido, precio: nPrecio, stock: f.tipo === "LIGHTNING" ? nPiezas : null });
      const est = r.estado === "started" ? "activa" : r.estado === "pending" ? "programada" : "enviada a ML";
      setMsg({
        ok: true,
        texto:
          `Listo: ${est} a ${fmt2(r.precio ?? nPrecio)}` +
          (r.inicio ? ` · ${cuando(r.inicio)} → ${cuando(r.fin)}` : "") +
          (r.avisos.length ? ` · ${r.avisos.join(" ")}` : ""),
      });
      setModo("nada");
      if (r.estado === "pending" || r.estado === "started")
        onCambio({ estado: r.estado, precio: r.precio ?? nPrecio, inicio: r.inicio ?? null, fin: r.fin ?? null, cobra: r.cobra_despues ?? f.cobra, restante: f.tipo === "LIGHTNING" ? nPiezas : f.restante });
    } catch (e) {
      setMsg({ ok: false, texto: mensajeDeError(e, "No se pudo tomar la oferta.") });
    } finally {
      setTrabajando(false);
    }
  };

  const quitar = async () => {
    setTrabajando(true);
    setMsg(null);
    try {
      const r = await pricingOfertaQuitar(pedido);
      setMsg({ ok: true, texto: r.estado === "candidate" ? "Quitada: vuelve a ser candidata." : r.avisos.join(" ") || "Enviado a ML." });
      setModo("nada");
      if (r.estado === "candidate") onCambio({ estado: "candidate", precio: null, inicio: null, fin: null, cobra: r.cobra_despues ?? f.cobra });
    } catch (e) {
      setMsg({ ok: false, texto: mensajeDeError(e, "No se pudo quitar la oferta.") });
    } finally {
      setTrabajando(false);
    }
  };

  const relampagoActiva = f.tipo === "LIGHTNING" && f.estado === "started";

  return (
    <tr>
      <td>
        <div className="of-pub">
          {/* eslint-disable-next-line @next/next/no-img-element -- miniatura de ML, ya del tamaño justo */}
          {f.foto ? <img src={f.foto} alt="" width={40} height={40} loading="lazy" /> : <span className="of-sin-foto" />}
          <div>
            <div className="mono sm">
              {f.sku || "sin SKU"} · {NOMBRE_CUENTA[f.cuenta] || f.cuenta}
              {f.tipo !== "LIGHTNING" ? ` · ${f.promo}` : ""}
            </div>
            {f.url ? (
              <a href={f.url} target="_blank" rel="noopener noreferrer">
                {f.titulo || f.item}
              </a>
            ) : (
              <span>{f.titulo || f.item}</span>
            )}
          </div>
        </div>
      </td>
      <td className="der mono">
        {f.stock ?? "—"} pzs
        <div className="sm">{f.u30} vendidas</div>
      </td>
      <td className="der mono">{fmt2(f.cobra)}</td>
      <td className="der mono">
        {f.estado === "candidate" ? (
          <>
            {f.sugerido != null ? (
              <>
                <b>{fmt2(f.sugerido)}</b>
                <div className="sm">sugerido</div>
              </>
            ) : (
              "—"
            )}
            {(f.min != null || f.max != null) && (
              <div className="sm">
                {f.min != null && f.max != null ? `${fmt2(f.min)} – ${fmt2(f.max)}` : f.min != null ? `desde ${fmt2(f.min)}` : `hasta ${fmt2(f.max)}`}
              </div>
            )}
            {f.tipo === "LIGHTNING" && f.stock_min != null && (
              <div className="sm">
                {f.stock_min}–{f.stock_max} pzs
              </div>
            )}
          </>
        ) : (
          <b>{fmt2(f.precio)}</b>
        )}
        {vsHoy != null && vsHoy <= -0.005 && <div className="sm of-baja">{(vsHoy * 100).toFixed(1)} % vs hoy</div>}
        {vsHoy != null && vsHoy >= 0.005 && (
          <div className="sm of-caro" title="El comprador paga siempre la promoción más baja: esta no le cambiaría el precio">
            +{(vsHoy * 100).toFixed(1)} %: más cara que hoy
          </div>
        )}
      </td>
      <td className="of-estado">
        {f.estado === "started" ? (
          <span className="of-chip activa">Activa</span>
        ) : f.estado === "pending" ? (
          <span className="of-chip prog">Programada</span>
        ) : f.puede === false ? (
          <span className="of-chip no">No puede</span>
        ) : f.motivo ? (
          <span className="of-chip duda">Dudosa</span>
        ) : (
          <span className="of-chip si">Puede entrar</span>
        )}
        {f.inicio && f.estado !== "candidate" && (
          <div className="sm">
            {cuando(f.inicio)} → {cuando(f.fin)}
          </div>
        )}
        {f.tipo === "LIGHTNING" && f.restante != null && f.estado !== "candidate" && <div className="sm">quedan {f.restante} pzs</div>}
        {f.motivo && <div className={`sm ${f.puede === false ? "of-motivo" : "of-aviso"}`}>{f.motivo}</div>}
        {msg && <div className={`sm ${msg.ok ? "of-ok" : "ed-error"}`}>{msg.texto}</div>}
      </td>
      {esAdmin && (
        <td className="der of-accion">
          {f.estado === "candidate" && f.puede !== false && modo === "nada" && (
            <button type="button" className="ed-revisar" onClick={abrirTomar}>
              Tomar
            </button>
          )}
          {f.estado !== "candidate" && modo === "nada" && (
            <button
              type="button"
              className="ed-cancelar"
              disabled={relampagoActiva}
              title={relampagoActiva ? "Ya empezó: ML no deja quitar una relámpago activa" : "Sacarla de esta oferta en ML"}
              onClick={() => {
                setMsg(null);
                setModo("quitar");
              }}
            >
              Quitar
            </button>
          )}
          {modo === "tomar" && (
            <div className="ed-form">
              <label>
                Precio $
                <input type="number" step="0.01" min={f.min ?? 0} max={f.max ?? undefined} value={precio} onChange={(e) => setPrecio(e.target.value)} aria-label="Precio de la oferta" />
              </label>
              {f.tipo === "LIGHTNING" && (
                <label>
                  Piezas
                  <input type="number" step="1" min={f.stock_min ?? 1} max={f.stock_max ?? undefined} value={piezas} onChange={(e) => setPiezas(e.target.value)} aria-label="Piezas para la relámpago" />
                </label>
              )}
              {fuera && <div className="sm ed-error">Fuera del rango de ML</div>}
              {nPrecio > 0 && f.cobra != null && nPrecio >= f.cobra + 0.005 && (
                <div className="sm of-caro">Más cara que lo que cobra hoy ({fmt2(f.cobra)}): el comprador no la vería.</div>
              )}
              {f.tipo === "LIGHTNING" && <div className="sm">ML escoge el horario; ya activa no se quita.</div>}
              <MotivoSelect motivo={motivo} texto={motivoTexto} onMotivo={setMotivo} onTexto={setMotivoTexto} compacto />
              <div className="ed-botones">
                <button type="button" className="ed-revisar" disabled={!valido || !conMotivo || trabajando} onClick={() => void tomar()}>
                  {trabajando ? "Tomando…" : "Tomar en ML"}
                </button>
                <button type="button" className="ed-cancelar" disabled={trabajando} onClick={() => setModo("nada")}>
                  Cancelar
                </button>
              </div>
            </div>
          )}
          {modo === "quitar" && (
            <div className="ed-form">
              <div className="sm">¿Sacarla de {f.tipo === "LIGHTNING" ? "la relámpago" : `«${f.promo}»`}?</div>
              <MotivoSelect motivo={motivo} texto={motivoTexto} onMotivo={setMotivo} onTexto={setMotivoTexto} compacto />
              <div className="ed-botones">
                <button type="button" className="ed-revisar" disabled={!conMotivo || trabajando} onClick={() => void quitar()}>
                  {trabajando ? "Quitando…" : "Sí, quitar"}
                </button>
                <button type="button" className="ed-cancelar" disabled={trabajando} onClick={() => setModo("nada")}>
                  No
                </button>
              </div>
            </div>
          )}
        </td>
      )}
    </tr>
  );
}
