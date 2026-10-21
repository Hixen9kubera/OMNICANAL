"use client";

// Ofertas.tsx — «Ofertas de ML» en PRICING (v0.672.0, pedido de Brandon 20-oct-2026): qué relámpagos, Buen Fin,
// campañas propias y otras campañas de ML se pueden tomar, y tomarlas o quitarlas desde aquí.
//
// v0.679.0 (Brandon, 21-oct): rediseño. Cada fila se lee de izquierda a derecha en el orden en que se decide:
// el botón PRIMERO (antes había que deslizar la tabla para verlo), luego A CUÁNTO QUEDARÍA y qué descuento
// aplica —contra lo que cobra hoy y contra el tachado que ve el comprador—, luego la publicación y sus datos.
// Al tomar, un panel debajo de la fila deja escoger el precio (sugerido, mínimo, máximo o a mano) y dice
// antes de confirmar cuánto pagaría el comprador.
//
// Ver lo puede quien ve PRICING; tomar y quitar, admin o editor de PRICING (el servidor también lo exige).
// Cada clic es UN POST o UN DELETE a ML: el servidor relee la publicación antes, comprueba después y lo deja
// en la bitácora. Las reglas de ML que la pantalla explica (medidas en vivo el 20-oct):
//   · Relámpago: ML escoge el horario (12 h) y aparta las piezas; solo se quita mientras está programada.
//   · Campaña propia: ML no deja entrar a una publicación con descuento individual activo.
//   · Buen Fin: acepta aunque tenga descuento individual; se quita mientras no empiece.

import { useEffect, useMemo, useRef, useState } from "react";
import { Zap } from "lucide-react";

import {
  mensajeDeError,
  pricingOfertaQuitar,
  pricingOfertas,
  pricingOfertaTomar,
  type PricingOfertaFila,
  type PricingOfertas,
} from "@/lib/api";

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
const VISTAS: [Vista, string][] = [
  ["pueden", "Pueden entrar"],
  ["inscritas", "Ya inscritas"],
  ["no", "No pueden"],
];

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
  buenfin: "Se inscribe con un precio dentro del rango que da ML. Acepta publicaciones con descuento individual y se puede quitar mientras no empiece.",
  propia:
    "Tus campañas (ALWAYS ON, Octubre…). ML NO deja entrar a una publicación con descuento individual activo: primero hay que quitárselo con «Cambiar precio» (Desc. 0 %).",
  ml: "Las demás campañas de ML (Activa tus descuentos, clearance de Full). Mismo trato que Buen Fin.",
};

const fmt2 = (x: number | null | undefined) =>
  x == null ? "—" : "$" + x.toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (x: number) => `${x > 0 ? "+" : "−"}${Math.abs(Math.round(x * 1000) / 10).toLocaleString("es-MX")} %`;

/** «2026-10-22 00:00» → «22 oct 00:00». */
function cuando(s: string | null | undefined) {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T") + ":00");
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString("es-MX", { day: "numeric", month: "short" }) + " " + s.slice(11, 16);
}

const llave = (f: PricingOfertaFila) => [f.cuenta, f.item, f.promo_id, f.estado, f.inicio || ""].join("|");
const vistaDe = (f: PricingOfertaFila): Vista => (f.estado !== "candidate" ? "inscritas" : f.puede === false ? "no" : "pueden");

/**
 * Qué pasaría con un precio de oferta: contra lo que se cobra hoy (lo que importa: el comprador paga SIEMPRE la
 * promoción más baja) y contra el precio de lista que ML muestra tachado.
 */
function efecto(f: PricingOfertaFila, precio: number | null) {
  if (!precio) return null;
  const lista = f.original ?? f.normal ?? null;
  return {
    vsHoy: f.cobra ? precio / f.cobra - 1 : null,
    vsLista: lista && lista > precio ? precio / lista - 1 : null,
    lista,
    masCara: f.cobra != null && precio >= f.cobra - 0.005,
  };
}

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

  const todasCuentas = useMemo(() => listas.flatMap((d) => d.filas), [listas]);
  // Cuántas PUEDEN ENTRAR en cada pestaña: lo que se ve en los botones de arriba.
  const puedenPor = useMemo(() => {
    const n: Record<Pestana, number> = { relampago: 0, buenfin: 0, propia: 0, ml: 0 };
    todasCuentas.forEach((f) => {
      if (vistaDe(f) !== "pueden") return;
      const p = PESTANAS.find(([k]) => dePestana(f, k));
      if (p) n[p[0]] += 1;
    });
    return n;
  }, [todasCuentas]);
  const todas = useMemo(() => todasCuentas.filter((f) => dePestana(f, pestana)), [todasCuentas, pestana]);
  const n: Record<Vista, number> = { pueden: 0, inscritas: 0, no: 0 };
  todas.forEach((f) => (n[vistaDe(f)] += 1));
  const q = buscar.trim().toLowerCase();
  const filas = todas
    .filter((f) => vistaDe(f) === vista)
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
          <h3 id="of-titulo">
            <Zap size={17} aria-hidden className="of-icono" /> Ofertas de Mercado Libre
          </h3>
          <div className="sm">
            Qué se puede tomar, a qué precio quedaría y qué descuento vería el comprador
            {listas.length ? ` · leído ${listas.map((d) => d.leido.slice(11, 16)).join(" y ")}` : ""}
            {esAdmin ? " · «Tomar» y «Quitar» escriben en ML al momento y quedan en la bitácora" : " · solo ver"}
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
        <div className="of-pestanas" role="tablist" aria-label="Tipo de oferta">
          {PESTANAS.map(([k, t]) => (
            <button key={k} type="button" role="tab" aria-selected={pestana === k} className={pestana === k ? "activa" : undefined} onClick={() => setPestana(k)}>
              <span>{t}</span>
              <b>{cargando && !listas.length ? "…" : puedenPor[k]}</b>
              <small>pueden entrar</small>
            </button>
          ))}
        </div>

        <div className="of-filtros">
          <div className="seg" aria-label="Estado">
            {VISTAS.map(([k, t]) => (
              <button key={k} type="button" aria-pressed={vista === k} onClick={() => setVista(k)}>
                {t}
                <span className="cnt">{n[k]}</span>
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
          <input className="of-buscar" type="search" value={buscar} onChange={(e) => setBuscar(e.target.value)} placeholder="Buscar SKU o título" aria-label="Buscar por SKU o título" />
        </div>

        <div className="of-ayuda">
          <span>{AYUDA[pestana]}</span>
          {promos.length > 0 && (
            <span className="of-promos">
              {promos.map((p) => (
                <span key={p.cuenta + p.id}>
                  <b>{p.tipo === "LIGHTNING" ? "Relámpago" : p.nombre}</b> · {NOMBRE_CUENTA[p.cuenta]}
                  {p.inicio ? ` · ${cuando(p.inicio)} → ${cuando(p.fin)}` : ""}
                  {p.limite && p.estado === "pending" ? ` · inscribir hasta ${cuando(p.limite)}` : ""} · {p.conteo.started || 0} activas · {p.conteo.pending || 0}{" "}
                  programadas · {p.conteo.candidate || 0} candidatas
                </span>
              ))}
            </span>
          )}
        </div>

        {errores.map((e) => (
          <p key={e} className="ed-error">
            {e}
          </p>
        ))}
        {cargando && <p className="sm">Leyendo las ofertas en Mercado Libre (10 a 25 s la primera vez)…</p>}
        {!cargando && !filas.length && !errores.length && <p className="of-vacio">No hay publicaciones en esta vista.</p>}

        {filas.length > 0 && (
          <div className="of-lista" role="list">
            <div className={`of-fila of-cabeza${esAdmin ? "" : " sin-accion"}`} aria-hidden>
              {esAdmin && <span />}
              <span>Si entra</span>
              <span>Publicación</span>
              <span className="der">Stock · 30 d</span>
              <span>Estado</span>
            </div>
            {filas.slice(0, 400).map((f) => (
              <FilaOferta key={llave(f)} f={f} esAdmin={esAdmin} onCambio={(c) => actualizar(f, c)} />
            ))}
            {filas.length > 400 && <p className="sm">Se muestran 400 de {filas.length}: usa el buscador para encontrar el resto.</p>}
          </div>
        )}
      </div>
    </dialog>
  );
}

function FilaOferta({ f, esAdmin, onCambio }: { f: PricingOfertaFila; esAdmin: boolean; onCambio: (c: Partial<PricingOfertaFila>) => void }) {
  const [modo, setModo] = useState<"nada" | "tomar" | "quitar">("nada");
  const [precio, setPrecio] = useState("");
  const [piezas, setPiezas] = useState("");
  const [trabajando, setTrabajando] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; texto: string } | null>(null);

  const candidata = f.estado === "candidate";
  const ofertaPrecio = candidata ? f.sugerido : f.precio;
  const ef = efecto(f, ofertaPrecio);
  const nPrecio = parseFloat(precio);
  const nPiezas = parseInt(piezas, 10);
  const efNuevo = efecto(f, nPrecio > 0 ? nPrecio : null);
  const fuera = (f.min != null && nPrecio < f.min - 0.005) || (f.max != null && nPrecio > f.max + 0.005);
  const valido =
    nPrecio > 0 && !fuera && (f.tipo !== "LIGHTNING" || (nPiezas > 0 && (!f.stock_min || nPiezas >= f.stock_min) && (!f.stock_max || nPiezas <= f.stock_max)));
  const relampagoActiva = f.tipo === "LIGHTNING" && f.estado === "started";

  // Atajos de precio: lo que ML propone y los extremos que acepta.
  const atajos: [string, number][] = [];
  if (f.sugerido != null) atajos.push(["Sugerido", f.sugerido]);
  if (f.min != null) atajos.push(["Mínimo", f.min]);
  if (f.max != null && f.max !== f.sugerido) atajos.push(["Máximo", f.max]);

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

  const tomar = async () => {
    setTrabajando(true);
    setMsg(null);
    try {
      const r = await pricingOfertaTomar({ ...pedido, precio: nPrecio, stock: f.tipo === "LIGHTNING" ? nPiezas : null });
      const est = r.estado === "started" ? "activa" : r.estado === "pending" ? "programada" : "enviada a ML";
      setMsg({
        ok: true,
        texto:
          `Listo: ${est} a ${fmt2(r.precio ?? nPrecio)}` + (r.inicio ? ` · ${cuando(r.inicio)} → ${cuando(r.fin)}` : "") + (r.avisos.length ? ` · ${r.avisos.join(" ")}` : ""),
      });
      setModo("nada");
      if (r.estado === "pending" || r.estado === "started")
        onCambio({
          estado: r.estado,
          precio: r.precio ?? nPrecio,
          inicio: r.inicio ?? null,
          fin: r.fin ?? null,
          cobra: r.cobra_despues ?? f.cobra,
          restante: f.tipo === "LIGHTNING" ? nPiezas : f.restante,
        });
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

  return (
    <div className={`of-fila${esAdmin ? "" : " sin-accion"}${modo !== "nada" ? " abierta" : ""}`} role="listitem">
      {/* 1. La acción, primero. */}
      {esAdmin && (
        <div className="of-accion">
          {candidata && f.puede !== false ? (
            <button type="button" className="of-tomar" onClick={abrirTomar} disabled={modo === "tomar"}>
              Tomar
            </button>
          ) : candidata ? (
            <span className="of-nopuede">No puede</span>
          ) : (
            <button
              type="button"
              className="of-quitar"
              disabled={relampagoActiva || modo === "quitar"}
              title={relampagoActiva ? "Ya empezó: ML no deja quitar una relámpago activa" : "Sacarla de esta oferta en ML"}
              onClick={() => {
                setMsg(null);
                setModo("quitar");
              }}
            >
              {relampagoActiva ? "Activa" : "Quitar"}
            </button>
          )}
        </div>
      )}

      {/* 2. A cuánto quedaría y qué descuento aplica. */}
      <div className="of-resultado">
        {ofertaPrecio != null ? (
          <>
            <div className="sm">{candidata ? (f.sugerido != null ? "Quedaría en (sugerido)" : "Quedaría en") : f.estado === "started" ? "Activa a" : "Programada a"}</div>
            <div className="of-precio">{fmt2(ofertaPrecio)}</div>
            {ef?.masCara ? (
              <div className="sm of-caro" title="El comprador paga siempre la promoción más baja: esta no le cambiaría el precio">
                Hoy cobra menos ({fmt2(f.cobra)}): no cambiaría el precio
              </div>
            ) : (
              ef?.vsHoy != null && (
                <div className="of-vs">
                  <b className="of-baja">{pct(ef.vsHoy)}</b> vs hoy {fmt2(f.cobra)}
                </div>
              )
            )}
            {ef?.vsLista != null && (
              <div className="sm">
                Se vería <s>{fmt2(ef.lista)}</s> → {pct(ef.vsLista)} OFF
              </div>
            )}
          </>
        ) : (
          <div className="sm">
            ML no da precio sugerido
            {f.min != null || f.max != null ? `: rango ${f.min != null ? fmt2(f.min) : "…"} – ${f.max != null ? fmt2(f.max) : "…"}` : ""}
          </div>
        )}
      </div>

      {/* 3. La publicación. */}
      <div className="of-pub">
        {/* eslint-disable-next-line @next/next/no-img-element -- miniatura de ML, ya del tamaño justo */}
        {f.foto ? <img src={f.foto} alt="" width={44} height={44} loading="lazy" /> : <span className="of-sin-foto" />}
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

      {/* 4. Datos. */}
      <div className="of-datos der mono">
        <b>{f.stock ?? "—"}</b> pzs
        <div className="sm">{f.u30} vendidas</div>
        {candidata && f.tipo === "LIGHTNING" && f.stock_min != null && (
          <div className="sm">
            pide {f.stock_min}–{f.stock_max} pzs
          </div>
        )}
      </div>

      {/* 5. Estado. */}
      <div className="of-estado">
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
        {f.inicio && !candidata && (
          <div className="sm">
            {cuando(f.inicio)} → {cuando(f.fin)}
          </div>
        )}
        {f.tipo === "LIGHTNING" && f.restante != null && !candidata && <div className="sm">quedan {f.restante} pzs</div>}
        {f.motivo && <div className={`sm ${f.puede === false ? "of-motivo" : "of-aviso"}`}>{f.motivo}</div>}
        {msg && <div className={`sm ${msg.ok ? "of-ok" : "ed-error"}`}>{msg.texto}</div>}
      </div>

      {/* Panel de tomar: debajo de la fila, a todo lo ancho. */}
      {modo === "tomar" && (
        <div className="of-panel">
          <div className="of-panel-campos">
            <label>
              Precio de la oferta
              <span className="of-input-pesos">
                $
                <input type="number" step="0.01" min={f.min ?? 0} max={f.max ?? undefined} value={precio} onChange={(e) => setPrecio(e.target.value)} aria-label="Precio de la oferta" autoFocus />
              </span>
            </label>
            {atajos.length > 0 && (
              <div className="of-atajos" aria-label="Precios que da ML">
                {atajos.map(([t, v]) => (
                  <button key={t} type="button" aria-pressed={Math.abs(nPrecio - v) < 0.005} onClick={() => setPrecio(String(v))}>
                    {t} {fmt2(v)}
                  </button>
                ))}
              </div>
            )}
            {f.tipo === "LIGHTNING" && (
              <label>
                Piezas para la relámpago
                <input type="number" step="1" min={f.stock_min ?? 1} max={f.stock_max ?? undefined} value={piezas} onChange={(e) => setPiezas(e.target.value)} aria-label="Piezas para la relámpago" />
                <span className="sm">
                  ML pide {f.stock_min}–{f.stock_max}
                </span>
              </label>
            )}
          </div>
          <div className="of-panel-efecto">
            {nPrecio > 0 ? (
              <>
                <div>
                  El comprador pagaría <b className="of-precio">{fmt2(efNuevo?.masCara ? f.cobra : nPrecio)}</b>
                </div>
                {efNuevo?.masCara ? (
                  <div className="of-caro">Hoy ya cobra {fmt2(f.cobra)}: esta oferta no le bajaría el precio.</div>
                ) : (
                  efNuevo?.vsHoy != null && (
                    <div>
                      <b className="of-baja">{pct(efNuevo.vsHoy)}</b> contra lo que cobra hoy ({fmt2(f.cobra)})
                    </div>
                  )
                )}
                {efNuevo?.vsLista != null && (
                  <div className="sm">
                    En ML se vería <s>{fmt2(efNuevo.lista)}</s> {fmt2(nPrecio)} · {pct(efNuevo.vsLista)} OFF
                  </div>
                )}
                {fuera && <div className="ed-error">Fuera del rango que acepta ML ({fmt2(f.min)} – {fmt2(f.max)}).</div>}
                {f.tipo === "LIGHTNING" && <div className="sm">ML escoge el horario (12 h); ya activa no se puede quitar.</div>}
              </>
            ) : (
              <div className="sm">Escribe el precio o escoge uno de los que da ML.</div>
            )}
          </div>
          <div className="of-panel-botones">
            <button type="button" className="of-tomar" disabled={!valido || trabajando} onClick={() => void tomar()}>
              {trabajando ? "Tomando en ML…" : "Confirmar en ML"}
            </button>
            <button type="button" className="ed-cancelar" disabled={trabajando} onClick={() => setModo("nada")}>
              Cancelar
            </button>
          </div>
        </div>
      )}
      {modo === "quitar" && (
        <div className="of-panel">
          <div className="of-panel-efecto">
            ¿Sacarla de {f.tipo === "LIGHTNING" ? "la relámpago" : `«${f.promo}»`}? El precio vuelve a lo que cobraría sin esta oferta.
          </div>
          <div className="of-panel-botones">
            <button type="button" className="of-quitar fuerte" disabled={trabajando} onClick={() => void quitar()}>
              {trabajando ? "Quitando…" : "Sí, quitar"}
            </button>
            <button type="button" className="ed-cancelar" disabled={trabajando} onClick={() => setModo("nada")}>
              No
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
