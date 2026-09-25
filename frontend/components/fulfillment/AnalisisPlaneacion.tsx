"use client";

/**
 * FULLFILMENT · ANÁLISIS · «Planeación de la semana» (Brandon, 24-sep-2026: "todos
 * los puntos se pasan a Análisis y se deja Crear FULL únicamente para crear FULLs").
 *
 *   · B — los totales por tienda de la planeación que se está armando en Crear FULL
 *     (con lo que la persona editó).
 *   · Ganadores SIN EXISTENCIA y su REEMPLAZO: el ganador no se puede surtir (0 libre
 *     en Odoo y 0 en el almacén); el reemplazo es el siguiente que SÍ vende y SÍ
 *     tiene stock. «Agregar» lo marca en Crear FULL como reemplazo.
 *   · Títulos que no coinciden: el título del marketplace contra el nombre de ODOO,
 *     con las dos fotos para comparar.
 *   · Órdenes sin completar: cuánto lleva cada borrador sin confirmarse y cada salida
 *     sin validarse.
 *
 * Los datos los arma Crear FULL (`onPlan`), que se queda montado aunque se cambie de
 * pantalla: así lo editado no se pierde.
 */

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, ExternalLink, ImageOff, PackageX, Replace } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import type { Totales } from "./proponer";
import { Ayuda, Ceja, FONDO_RAYADO, Tarjeta, dia, num, pesos } from "./ui";
import type { BorradorFull, FiltroCanal, FiltroCuenta, SalidaAbierta, Tienda } from "./tipos";

export interface GanadorAnalisis {
  tienda: Tienda; sku: string; nombre: string | null; vv: number; v7: number; precio: number | null;
  stock: number | null; url: string | null;
  reemplazos: { sku: string; nombre: string | null; tipo: string; libre: number; vendio?: number;
                precio?: number | null }[];
}

export interface TituloAnalisis {
  tienda: Tienda; sku: string; nombre: string | null; nombre_odoo: string | null; titulo_mkt: string | null;
  imagen_mkt: string | null; url: string | null; listing_id: string | null; parecido: number | null;
  /** El título tampoco se parece al nombre del catálogo: revisar primero. */
  urgente: boolean | null;
}

/** Lo que Crear FULL le pasa a Análisis. */
export interface PlanAnalisis {
  semana: string;
  generado: string;
  nombres: Record<Tienda, string>;
  almacen: Record<Tienda, string>;
  totales: { tienda: Tienda; t: Totales }[];
  total: Totales;
  ganadores: GanadorAnalisis[];
  titulos: TituloAnalisis[];
  otras: { tienda: Tienda; sku: string; tipo: string; detalle: string }[];
  borradores: BorradorFull[];
  abiertas: SalidaAbierta[];
}

const tiendaEnFiltro = (t: Tienda, canal: FiltroCanal, cuenta: FiltroCuenta) =>
  canal === "todos" ? (cuenta === "todas" || !t.startsWith("meli") || t === `meli:${cuenta}`)
    : canal === "meli" ? t.startsWith("meli") && (cuenta === "todas" || t === `meli:${cuenta}`)
      : t === canal;

/** Cuánto lleva una orden: el color dice si ya preocupa. */
function Lleva({ dias, limite = 21 }: { dias: number | null; limite?: number }) {
  if (dias === null) return <span className="text-[11.5px] text-slate-400">sin fecha</span>;
  const c = dias > limite ? "border-rose-200 bg-rose-50 text-rose-700"
    : dias > 7 ? "border-amber-200 bg-amber-50 text-amber-800" : "border-slate-200 bg-white text-slate-600";
  return (
    <span className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-[11.5px] font-bold ${c}`}>
      lleva {dias} {dias === 1 ? "día" : "días"}
    </span>
  );
}

export default function PlaneacionSemana({ plan, canal, cuenta, onAgregarReemplazo }: {
  plan: PlanAnalisis | null;
  canal: FiltroCanal;
  cuenta: FiltroCuenta;
  onAgregarReemplazo: (tienda: Tienda, sku: string, de: string) => void;
}) {
  const [agregados, setAgregados] = useState<Set<string>>(new Set());
  // Primero lo accionable: los ganadores que SÍ tienen reemplazo.
  const [verGan, setVerGan] = useState<"con" | "sin">("con");
  const [fotos, setFotos] = useState<Record<string, string | null>>({});
  const en = (t: Tienda) => tiendaEnFiltro(t, canal, cuenta);

  const titulos = useMemo(() => (plan?.titulos ?? []).filter((x) => en(x.tienda)),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [plan, canal, cuenta]);
  // Las fotos de Odoo se piden sólo para los títulos que se van a enseñar.
  const faltan = useMemo(() => Array.from(new Set(titulos.map((x) => x.sku))).filter((s) => !(s in fotos)).slice(0, 60),
  [titulos, fotos]);
  // (Cada tanda que llega vuelve a calcular `faltan`: así se piden las siguientes 60.)
  useEffect(() => {
    if (!faltan.length) return;
    let vivo = true;
    fetchSesion(`${API_BASE}/api/fulfillment/crear-full/imagenes?skus=${encodeURIComponent(faltan.join(","))}`,
                { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { imagenes: {} }))
      .then((d: { imagenes: Record<string, string | null> }) => {
        if (vivo) setFotos((f) => ({ ...f, ...Object.fromEntries(faltan.map((s) => [s, d.imagenes?.[s] ?? null])) }));
      })
      .catch(() => { if (vivo) setFotos((f) => ({ ...f, ...Object.fromEntries(faltan.map((s) => [s, null])) })); });
    return () => { vivo = false; };
  }, [faltan]);

  if (!plan) {
    return (
      <div className="mt-4 rounded-2xl px-6 py-12 text-center text-sm text-slate-500" style={{ background: FONDO_RAYADO }}>
        Leyendo la planeación de la semana (ventas, publicaciones verificadas en vivo con Mercado Libre y lo libre en
        Odoo)… tarda unos 30 segundos.
      </div>
    );
  }

  const totales = plan.totales.filter((x) => en(x.tienda));
  const todosGan = plan.ganadores.filter((g) => en(g.tienda));
  const conReemplazo = todosGan.filter((g) => g.reemplazos.length > 0);
  const ganadores = verGan === "con" ? conReemplazo : todosGan.filter((g) => g.reemplazos.length === 0);
  const otras = plan.otras.filter((x) => en(x.tienda));
  const borradores = [...plan.borradores].filter((b) => !b.tienda || en(b.tienda))
    .sort((a, b) => (b.dias ?? 0) - (a.dias ?? 0));
  const abiertas = plan.abiertas.filter((x) => en(x.tienda));
  const pct = (n: number | null) => (n === null ? "—" : `${n}%`);
  const todas = totales.length === plan.totales.length;

  return (
    <div className="mt-4 flex flex-col gap-3">
      {/* ── B · Totales por tienda ───────────────────────────────────────── */}
      <Tarjeta className="min-w-0">
        <Ceja>B · Totales por tienda · planeación {plan.semana}</Ceja>
        <p className="mt-1 text-[12px] text-slate-500">
          La planeación que se está armando en Crear FULL, con lo que ya editaste. Sólo las tiendas prendidas allá.
        </p>
        <div className="mt-2 overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[640px] text-[12.5px]">
            <thead>
              <tr className="bg-slate-50 text-left text-[10px] font-bold uppercase tracking-[.05em] text-slate-500">
                <th className="px-3 py-2">Tienda</th>
                <th className="px-2 py-2 text-right"><Ayuda texto="Suma de lo que pidió cada renglón (el faltante para la cobertura).">Pedidas</Ayuda></th>
                <th className="px-2 py-2 text-right"><Ayuda texto="Suma de la propuesta: lo que bodega puede surtir.">Propuestas</Ayuda></th>
                <th className="px-2 py-2 text-right"><Ayuda texto="Lo que se va a crear, con tus cambios.">A mandar</Ayuda></th>
                <th className="px-2 py-2 text-right"><Ayuda texto="Tasa de validado: lo que bodega puede entre lo pedido, de los renglones con dato de Odoo.">Validado</Ayuda></th>
                <th className="px-2 py-2 text-right"><Ayuda lado="der" texto="Lo que se va a mandar contra lo pedido.">Final vs pedido</Ayuda></th>
                <th className="px-3 py-2 text-right"><Ayuda lado="der" texto="Renglones sin dato de Odoo: quedan pendientes, no en cero.">Pendientes</Ayuda></th>
              </tr>
            </thead>
            <tbody>
              {totales.map(({ tienda, t }) => (
                <tr key={tienda} className="border-t border-slate-100">
                  <td className="px-3 py-1.5 font-semibold text-slate-700">{plan.nombres[tienda]}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(t.pedidas)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(t.propuestas)}</td>
                  <td className="px-2 py-1.5 text-right font-mono font-bold tabular-nums">{num(t.a_mandar)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{pct(t.tasa_validado)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{pct(t.final_vs_pedido)}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums text-slate-500">{num(t.pendientes)}</td>
                </tr>
              ))}
              {totales.length > 1 && todas && (
                <tr className="border-t-2 border-slate-200 bg-slate-50 font-bold">
                  <td className="px-3 py-1.5">Total</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(plan.total.pedidas)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(plan.total.propuestas)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(plan.total.a_mandar)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{pct(plan.total.tasa_validado)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{pct(plan.total.final_vs_pedido)}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums">{num(plan.total.pendientes)}</td>
                </tr>
              )}
              {totales.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-4 text-center text-[12.5px] text-slate-400">
                  Esta tienda está apagada en Crear FULL.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Tarjeta>

      {/* ── Ganadores sin existencia y su REEMPLAZO ──────────────────────── */}
      <Tarjeta className="min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Ceja>Ganadores sin existencia y su reemplazo · {todosGan.length}</Ceja>
          <div className="flex overflow-hidden rounded-lg border border-slate-200 bg-white text-xs font-bold">
            {([["con", `Con reemplazo · ${conReemplazo.length}`],
               ["sin", `Sin reemplazo: compras · ${todosGan.length - conReemplazo.length}`]] as const).map(([k, t]) => (
              <button key={k} type="button" onClick={() => setVerGan(k)}
                      className={`px-3 py-1.5 ${verGan === k ? "bg-indigo-50 text-indigo-800" : "text-slate-500 hover:bg-slate-50"}`}>
                {t}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-1 max-w-4xl text-[12px] leading-relaxed text-slate-500">
          Vendieron bien en la ventana pero <b>no se pueden surtir</b>: no hay existencia libre en Odoo ni piezas en el
          almacén del marketplace. El <b>reemplazo</b> es el siguiente SKU ya publicado en esa tienda que <b>sí vende y sí
          tiene stock</b>: primero el mismo modelo (otro color o talla), luego la misma categoría; el que más vende primero.
        </p>
        <div className="mt-2 max-h-[560px] divide-y divide-slate-100 overflow-y-auto rounded-xl border border-slate-200">
          {ganadores.map((g) => {
            const [principal, ...otras] = g.reemplazos;
            return (
              <div key={`${g.tienda}|${g.sku}`} className="px-3 py-2.5 text-[12.5px]">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="min-w-0">
                    <span className="font-mono font-bold text-slate-900">{g.sku}</span>
                    <span className="text-slate-500"> · {(g.nombre ?? "").slice(0, 70)}</span>
                  </span>
                  <span className="text-[11.5px] text-slate-500">
                    {plan.nombres[g.tienda]} · vendió <b className="text-slate-700">{num(g.vv)}</b> ({num(g.v7)} en 7 días)
                    {g.precio ? ` · ${pesos(g.precio)}` : ""}
                  </span>
                </div>
                <div className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-rose-50 px-2 py-0.5 text-[11.5px] font-semibold text-rose-700">
                  <PackageX className="h-3.5 w-3.5" /> No se puede surtir: 0 libres en Odoo y 0 en {plan.almacen[g.tienda]}
                </div>
                {principal ? (
                  <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-violet-200 bg-violet-50/60 px-2.5 py-1.5">
                    <span className="min-w-0">
                      <span className="mr-1.5 inline-flex items-center gap-1 rounded bg-violet-600 px-1.5 py-0.5 text-[10px] font-extrabold uppercase tracking-[.06em] text-white">
                        <Replace className="h-3 w-3" /> Reemplazo
                      </span>
                      <span className="font-mono font-bold text-slate-900">{principal.sku}</span>
                      <span className="text-slate-500"> · {(principal.nombre ?? "").slice(0, 60)}</span>
                      <span className="block text-[11.5px] text-slate-600">
                        {principal.tipo} · vendió {num(principal.vendio ?? null)} · libre {num(principal.libre)} en Odoo
                        {principal.precio ? ` · ${pesos(principal.precio)}` : ""}
                      </span>
                    </span>
                    {agregados.has(`${g.tienda}|${principal.sku}`) ? (
                      <span className="inline-flex items-center gap-1 text-[11.5px] font-semibold text-emerald-700">
                        <CheckCircle2 className="h-3.5 w-3.5" /> en Crear FULL como reemplazo
                      </span>
                    ) : (
                      <button type="button"
                              onClick={() => { onAgregarReemplazo(g.tienda, principal.sku, g.sku); setAgregados((a) => new Set(a).add(`${g.tienda}|${principal.sku}`)); }}
                              className="rounded-md bg-violet-600 px-2.5 py-1 text-[11.5px] font-bold text-white hover:bg-violet-700">
                        Agregar a Crear FULL
                      </button>
                    )}
                  </div>
                ) : (
                  <p className="mt-1.5 text-[11.5px] text-slate-500">
                    Sin reemplazo que venda y tenga stock en esa tienda: <b>señal de compras</b>.
                  </p>
                )}
                {otras.length > 0 && (
                  <p className="mt-1 text-[11px] text-slate-500">
                    Otras opciones: {otras.map((o) => `${o.sku} (vendió ${num(o.vendio ?? null)}, libre ${num(o.libre)})`).join(" · ")}
                  </p>
                )}
              </div>
            );
          })}
          {ganadores.length === 0 && (
            <p className="px-3 py-4 text-[12.5px] text-slate-400">
              {verGan === "con" ? "Ningún ganador sin existencia tiene un reemplazo que venda y tenga stock en este filtro."
                                : "Ninguno en este filtro."}
            </p>
          )}
        </div>
      </Tarjeta>

      {/* ── Títulos que no coinciden: ML contra Odoo, con fotos ─────────── */}
      <Tarjeta className="min-w-0">
        <Ceja>Títulos que no coinciden · marketplace contra Odoo · {titulos.length}</Ceja>
        <p className="mt-1 max-w-4xl text-[12px] leading-relaxed text-slate-500">
          La publicación dice una cosa y el producto de Odoo otra: puede ser un SKU reciclado o una publicación
          equivocada. Compara las fotos antes de mandar piezas.
        </p>
        {titulos.length === 0 && <p className="mt-2 text-[12.5px] text-slate-400">Todos los títulos coinciden con Odoo.</p>}
        {[
          { lista: titulos.filter((x) => x.urgente !== false), t: "Revisar primero", c: "text-rose-700",
            nota: "Tampoco se parecen al nombre del catálogo de Omnicanal: lo más probable es que la publicación y el producto de Odoo sean cosas distintas." },
          { lista: titulos.filter((x) => x.urgente === false), t: "Probablemente sólo cambia la redacción", c: "text-slate-600",
            nota: "El título sí se parece al nombre del catálogo; Odoo lo describe con otras palabras (a veces en inglés). Aun así, compara las fotos: si el producto de Odoo es otro, no se debe mandar." },
        ].filter((g) => g.lista.length).map((g) => (
          <div key={g.t} className="mt-3">
            <div className={`text-[11px] font-extrabold uppercase tracking-[.06em] ${g.c}`}>{g.t} · {g.lista.length}</div>
            <p className="mt-0.5 text-[11.5px] text-slate-500">{g.nota}</p>
            <div className="mt-2 grid max-h-[900px] grid-cols-1 gap-2 overflow-y-auto lg:grid-cols-2">
              {[...g.lista].sort((a, b) => (a.parecido ?? 0) - (b.parecido ?? 0)).map((x) => (
                <div key={`${x.tienda}|${x.sku}`} className="min-w-0 rounded-xl border border-slate-200 p-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-mono text-[12.5px] font-bold text-slate-900">{x.sku}</span>
                    <span className="text-[11px] text-slate-500">
                      {plan.nombres[x.tienda]}
                      {x.parecido !== null ? ` · ${Math.round(x.parecido * 100)}% de palabras en común` : ""}
                    </span>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2.5">
                    <Foto titulo="Marketplace" src={x.imagen_mkt} texto={x.titulo_mkt} enlace={x.url} extra={x.listing_id} />
                    <Foto titulo="Odoo" src={fotos[x.sku] ?? null} cargando={!(x.sku in fotos)}
                          texto={x.nombre_odoo ?? "no está en Odoo"} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
        {otras.length > 0 && (
          <details className="mt-3 text-[12px] text-slate-600">
            <summary className="cursor-pointer text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-500">
              Otras alertas · {otras.length}
            </summary>
            <div className="mt-1.5 divide-y divide-slate-100 rounded-lg border border-slate-200">
              {otras.map((a, i) => (
                <div key={`${a.sku}-${a.tipo}-${i}`} className="px-3 py-1.5">
                  <span className="font-mono font-bold text-slate-800">{a.sku}</span>
                  <span className="text-slate-400"> · {plan.nombres[a.tienda]} · </span>
                  <span className="text-amber-800">{a.detalle}</span>
                </div>
              ))}
            </div>
          </details>
        )}
      </Tarjeta>

      {/* ── Órdenes sin completar: cuánto llevan ─────────────────────────── */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Tarjeta className="min-w-0">
          <Ceja>Borradores sin confirmar · {borradores.length}</Ceja>
          <p className="mt-1 text-[12px] text-slate-500">
            Cotizaciones a FULL, FBA o WFS que nadie ha confirmado en Odoo (últimos 90 días), la más vieja primero. Las
            de 21 días o menos se restan de la planeación; las de PRUEBA y «sin tienda», no.
          </p>
          <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
            {borradores.map((b) => (
              <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[12.5px]">
                <a href={b.url} target="_blank" rel="noreferrer" className="min-w-0 hover:underline">
                  <span className="font-mono font-bold text-slate-800">{b.orden}</span>
                  <span className="text-slate-400"> · {b.kam ?? "—"} · desde el {dia(b.creada)}</span>
                  <span className="block text-[11.5px] text-slate-500">
                    {num(b.piezas)} pzs · {b.skus} SKUs · {b.tienda ? plan.nombres[b.tienda] : "sin tienda"}
                    {b.prueba ? " · PRUEBA" : ""}
                    {(b.dias ?? 0) > 21 && b.tienda && !b.prueba ? " · ya no se resta de la planeación" : ""}
                  </span>
                </a>
                <Lleva dias={b.dias} />
              </div>
            ))}
            {borradores.length === 0 && <p className="px-3 py-3 text-[12px] text-slate-400">Ninguno.</p>}
          </div>
        </Tarjeta>
        <Tarjeta className="min-w-0">
          <Ceja>Salidas sin validar · {abiertas.length}</Ceja>
          <p className="mt-1 text-[12px] text-slate-500">
            Órdenes confirmadas cuya salida bodega no ha validado, la más vieja primero. Después de 21 días ya no cuentan
            como «en camino» y siguen reservando stock en Odoo.
          </p>
          <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
            {abiertas.map((z) => (
              <div key={`${z.orden}-${z.salida}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[12.5px]">
                <span className="min-w-0">
                  <span className="font-mono font-bold text-slate-800">{z.orden}</span>
                  <span className="text-slate-400"> · {z.salida} · desde el {dia(z.creada)}</span>
                  <span className="block text-[11.5px] text-slate-500">
                    {num(z.piezas)} pzs pedidas · {plan.nombres[z.tienda]}{z.kam ? ` · ${z.kam}` : ""}
                    {z.olvidada ? " · olvidada: reserva stock y no cuenta como en camino" : ""}
                  </span>
                </span>
                <Lleva dias={z.dias} />
              </div>
            ))}
            {abiertas.length === 0 && <p className="px-3 py-3 text-[12px] text-slate-400">Ninguna.</p>}
          </div>
        </Tarjeta>
      </div>

      <p className="text-xs leading-relaxed text-slate-400">
        Planeación leída el {new Date(plan.generado).toLocaleString("es-MX", { timeZone: "America/Mexico_City" })}.
        Los totales siguen lo que edites en Crear FULL; los ganadores, los títulos y las órdenes salen de la misma lectura.
      </p>
    </div>
  );
}

function Foto({ titulo, src, texto, enlace, extra, cargando }: {
  titulo: string; src: string | null; texto: string | null; enlace?: string | null; extra?: string | null; cargando?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-bold uppercase tracking-[.06em] text-slate-400">{titulo}</div>
      <div className="mt-1 flex aspect-square max-w-[160px] items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-white">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt={texto ?? titulo} className="h-full w-full object-contain" loading="lazy" />
        ) : (
          <span className="flex flex-col items-center gap-1 text-[10.5px] text-slate-400">
            <ImageOff className="h-4 w-4" /> {cargando ? "cargando…" : "sin foto"}
          </span>
        )}
      </div>
      <p className="mt-1 line-clamp-3 text-[11.5px] leading-snug text-slate-700" title={texto ?? ""}>{texto ?? "—"}</p>
      {enlace && (
        <a href={enlace} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 font-mono text-[10.5px] text-indigo-500 hover:underline">
          {extra ?? "ver"}<ExternalLink className="h-2.5 w-2.5" />
        </a>
      )}
    </div>
  );
}
