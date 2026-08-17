"use client";

/**
 * MargenesRealesModal — "Productos más vendidos": popup sobre la pestaña
 * Análisis (Eduardo, 6-ago: "que sea un filtro en la propia pestaña al lado
 * de período, en vez de ser una pestaña aparte").
 *
 * Es la fase 0 de Márgenes con los TRES cobros de Meli reales:
 *   · Precio prom  = ingreso ÷ unidades de los pedidos (realizado)
 *   · Comisión /u  = sale_fee que ML cobró en esos pedidos
 *   · Envío /u     = cobro real por embarque (API de shipments de ML),
 *                    prorrateado por unidad en carritos mixtos
 * El estimado viejo de envío aparece TACHADO cuando difiere del real — la
 * evidencia de por qué esto existe (Malla Sombra: $349 estimado vs $88 real).
 *
 * La columna Visitas·CR% va de adorno a propósito (pedido explícito): la
 * fuente de visitas aún no está conectada, pero el lugar ya está apartado
 * para que al conectarla no haya que mover la tabla.
 *
 * El backend consulta los embarques por tandas (presupuesto) y cachea por
 * orden; mientras falten, `pendientes > 0` y el modal refresca solo.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, BadgePercent, RefreshCw, Tag, X } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import { avisoCostoImplausible, costoImplausible } from "@/lib/margen";
import PanelHover from "@/components/PanelHover";
import { CUENTA_DOT, CUENTA_INI, CUENTA_NOMBRE } from "@/lib/canales";

interface Fila {
  sku: string; titulo: string | null; uds: number; ingreso: number;
  precio_prom: number | null; costo_base: number | null;
  comision_unit: number | null; envio_unit: number | null;
  envio_estimado: number | null; cobertura_envio_pct: number;
  uds_sin_envio: number; precio_pub: number | null; precio_lista: number | null;
  costo_final: number | null; ganancia_unit: number | null;
  margen_pct: number | null; ganancia_total: number | null;
  estado: string | null;   // 'activa' | 'pausada' | 'otra'
  visitas: number | null; visitas_dias: number | null; cr_pct: number | null;
  /* En qué cuentas vendió. En la lista General un renglón puede venir de las
     dos, y entonces el estado ya es el resuelto entre ambas. */
  cuentas?: string[];
  /* …y cómo está en CADA una: {BEKURA: 'pausada', SANCORFASHION: 'activa'}.
     El estado resuelto dice que se puede comprar, no dónde. */
  estado_cuenta?: Record<string, string>;
}
interface Respuesta {
  dias: number; pendientes: number; consultadas: number; nota: string;
  estado: string | null;
  /* Lista General: UN renglón por SKU, con las cuentas ya fundidas y ordenada
     por el total del SKU. La calcula el backend a propósito — fundirla aquí
     desde los dos top-10 por cuenta repetía SKUs y podía perder productos que
     solo son grandes sumados (Eduardo, 14-ago). */
  general: Fila[];
  cuentas: { cuenta: string; filas: Fila[] }[];
}

const fMoney = (v: number | string | null | undefined, dec = 0) =>
  v == null ? "—" : `$${Number(v).toLocaleString("es-MX", { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
const fNum = (v: number | string | null | undefined, dec = 0) =>
  v == null ? "—" : Number(v).toLocaleString("es-MX", { minimumFractionDigits: dec, maximumFractionDigits: dec });

/* Los colores y las iniciales salen de lib/canales.ts, los MISMOS que la tabla
   de Análisis (Eduardo, 14-ago). Antes este popup tenía su propia paleta —BK en
   índigo, SC en celeste— y la tabla otra —BK celeste, SC violeta—, así que el
   mismo SKU se veía de dos maneras según dónde se mirara. */
const NOMBRE_CUENTA = CUENTA_NOMBRE;
// Etiqueta corta por fila para la vista GENERAL (Eduardo, 6-ago: "Ambas" es UNA
// lista con las primeras 10, no las dos tablas al mismo tiempo).
//
// Desde el 14-ago hay UN renglón POR SKU: los chips dicen en qué cuentas vendió
// —pueden ser los dos— y las cifras vienen sumadas. Antes el mismo SKU salía dos
// veces, una por cuenta, y competía consigo mismo por un lugar del top.
/* PUNTOS DE CUENTA CON SU TARJETA — el mismo patrón que la tabla de Análisis.
   Los puntitos dicen EN CUÁNTAS cuentas está el SKU, pero no cuáles ni cómo, y
   "está en dos cuentas" con una pausada se leía igual que con las dos vendiendo
   (Eduardo, 14-ago). La tarjeta abre el censo, cuenta por cuenta. */
function PuntosCuenta({ f }: { f: Fila }) {
  const ctas = f.cuentas ?? [];
  const porCta = f.estado_cuenta ?? {};
  const puntos = (
    <span className="flex items-center gap-0.5">
      {ctas.map((c) => (
        <span key={c} className={`h-2 w-2 rounded-full ${CUENTA_DOT[c] ?? "bg-slate-400"}`} />
      ))}
    </span>
  );
  if (!ctas.length) return puntos;
  const venden = ctas.filter((c) => porCta[c] === "activa").length;
  return (
    <PanelHover ancho={300} panel={
      <>
        <span className="block font-semibold text-white">
          Vendió en {ctas.length} {ctas.length === 1 ? "cuenta" : "cuentas"}
        </span>
        {ctas.map((c) => {
          const vende = porCta[c] === "activa";
          return (
            <span key={c} className="mt-1 flex items-baseline justify-between gap-3">
              <span className="truncate">
                <span className={vende ? "text-emerald-300" : "text-slate-400"}>●</span>
                {" "}Meli · {CUENTA_INI[c] ?? c}
              </span>
              <span className={`shrink-0 ${vende ? "text-emerald-300" : "text-slate-400"}`}>
                {porCta[c] === "activa" ? "activa"
                 : porCta[c] === "pausada" ? "pausada" : "sin publicación"}
              </span>
            </span>
          );
        })}
        <span className="mt-1.5 block text-slate-400">
          {venden === 0
            ? "Ninguna puede comprarse ahora mismo: vendió en el período pero hoy no está a la venta en ningún lado."
            : venden === ctas.length
              ? "Se puede comprar en todas."
              : `${venden} de ${ctas.length} se puede comprar; en la otra existe pero no vende.`}
        </span>
      </>
    }>{puntos}</PanelHover>
  );
}

/* La etiqueta de estado se quedó CORTA a propósito (Eduardo, 14-ago). Se probó
   con el alcance escrito —"ACTIVA SOLO EN BK", "EN NINGUNA"— y sobra: los
   puntos de arriba ya dicen en qué cuentas está y su tarjeta cuál está activa,
   así que el texto largo repetía lo mismo y ensanchaba el renglón. */

/* Situación de la publicación en esa cuenta. Importa para leer el margen: una
   pausada ya no está sangrando aunque su fila siga en rojo, y una activa con
   margen negativo sí requiere acción hoy. 'otra' = vendió en el período pero
   hoy no le queda publicación viva (ni activa ni pausada). */
const CHIP_ESTADO: Record<string, { txt: string; clase: string; ayuda: string }> = {
  activa: { txt: "ACTIVA", clase: "bg-emerald-50 text-emerald-700",
            ayuda: "Publicación activa: se está vendiendo ahora" },
  pausada: { txt: "PAUSADA", clase: "bg-amber-50 text-amber-700",
             ayuda: "Publicación pausada: vendió en el período pero hoy no está a la venta" },
  otra: { txt: "SIN PUB.", clase: "bg-slate-100 text-slate-500",
          ayuda: "Sin publicación viva en esta cuenta (cerrada o dada de baja)" },
};
const FILTRO_ESTADO = [
  { id: "TODAS", label: "Todas" },
  { id: "activa", label: "Activas" },
  { id: "pausada", label: "Pausadas" },
] as const;
type FiltroEstado = (typeof FILTRO_ESTADO)[number]["id"];
const FILTRO_CUENTAS = [
  { id: "TODAS", label: "Ambas" },
  { id: "BEKURA", label: "Kubera" },
  { id: "SANCORFASHION", label: "San Corpe" },
] as const;
type FiltroCuenta = (typeof FILTRO_CUENTAS)[number]["id"];

/* Tope de refrescos automáticos mientras el caché de envíos se llena: con
   presupuesto 250/carga, 8 rondas cubren ~2,000 órdenes — más que un mes. */
const MAX_RONDAS = 8;

/* Cuando el costo no es creíble el número SÍ se muestra, pero en ámbar y con ⚠
   (Eduardo, 6-ago): esconderlo sacaba al SKU del análisis y con él la señal de
   que algo pasa ahí. El ámbar es deliberado — no es el rojo/verde que se lee
   como veredicto, es "esto está en duda". */
function Margen({ f }: { f: Fila }) {
  const dudoso = f.precio_prom != null && costoImplausible(f.precio_prom, f.costo_base);
  if (f.margen_pct == null) {
    return (
      <span className="text-slate-300"
            title={f.envio_unit == null
              ? "Aún sin envío real consultado para este SKU — se completa solo"
              : "Falta costo o comisión para calcular el margen"}>—</span>
    );
  }
  return (
    <div title={dudoso ? avisoCostoImplausible(f.precio_prom!, f.costo_base!) : undefined}>
      <div className={`flex items-center justify-end gap-1 font-bold tabular-nums ${
          dudoso ? "text-amber-600"
          : f.margen_pct < 20 ? "text-red-500" : "text-emerald-600"}`}>
        {dudoso && <AlertTriangle size={11} className="shrink-0" />}
        {fNum(f.margen_pct, 1)}%
      </div>
      <div className={`text-[10px] tabular-nums ${dudoso ? "text-amber-500" : "text-slate-400"}`}>
        {fMoney(f.ganancia_unit, 2)}/u
      </div>
      {dudoso && (
        <div className="text-[9px] font-semibold uppercase tracking-wide text-amber-500">
          costo dudoso
        </div>
      )}
    </div>
  );
}

function Precio({ f }: { f: Fila }) {
  const promo = f.precio_lista != null && f.precio_pub != null
    && f.precio_lista > f.precio_pub * 1.05;
  return (
    <div title={`Promedio realizado del período (${fNum(f.uds)} uds ÷ ${fMoney(f.ingreso)})`
                + (f.precio_pub != null ? `\nPublicación activa hoy: ${fMoney(f.precio_pub, 2)}` : "")
                + (promo ? `\nPrecio de LISTA: ${fMoney(f.precio_lista, 2)} — hay promoción montada` : "")}>
      <div className="font-semibold tabular-nums text-slate-800">{fMoney(f.precio_prom, 2)}</div>
      {promo && (
        <div className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-violet-600">
          <BadgePercent size={10} />
          promo · lista {fMoney(f.precio_lista)}
        </div>
      )}
    </div>
  );
}

function Envio({ f }: { f: Fila }) {
  if (f.envio_unit == null) {
    return <span className="text-[11px] text-slate-400" title="Consultando embarques a Mercado Libre…">…</span>;
  }
  const dif = f.envio_estimado != null && Math.abs(f.envio_estimado - f.envio_unit) > 5;
  return (
    <div title={`Cobro real de ML por embarque, promedio por unidad`
                + (f.cobertura_envio_pct < 100
                   ? `\nCobertura: ${f.cobertura_envio_pct}% de las piezas (el resto sigue consultándose)` : "")
                + (dif ? `\nEl estimado viejo decía ${fMoney(f.envio_estimado, 2)}` : "")}>
      <span className="tabular-nums text-slate-700">{fMoney(f.envio_unit, 2)}</span>
      {f.cobertura_envio_pct < 100 && <span className="text-amber-500">*</span>}
      {dif && (
        <div className="text-[10px] tabular-nums text-slate-400 line-through">{fMoney(f.envio_estimado, 2)}</div>
      )}
    </div>
  );
}



/* VISITAS y CONVERSIÓN. Es el par que explica por qué un producto vende poco:
   sin visitas el problema es de visibilidad (precio, posición, publicidad); con
   muchas visitas y poca conversión el problema está en la ficha o el precio.
   Caso real: MUE-0226-DOR convierte 47.8% con 274 visitas — no es mal producto,
   es invisible; CUNA-0011-GRI recibe 4,188 visitas y convierte 2.2%.

   El color va sobre la CONVERSIÓN, no sobre las visitas: mucho tráfico no es
   un logro si no se traduce en venta. 5% es la referencia sana en ML. */
function Visitas({ f }: { f: Fila }) {
  if (f.visitas == null) {
    return (
      <span className="text-[11px] text-slate-300"
            title="Sin medición de visitas todavía — se consulta a Mercado Libre y se guarda por unas horas">
        — · —
      </span>
    );
  }
  const parcial = f.visitas_dias != null && f.visitas_dias < 28;
  return (
    <div title={`${fNum(f.visitas)} visitas a la publicación en Mercado Libre`
                + (f.visitas_dias ? `\nVentana devuelta por ML: ${f.visitas_dias} días` : "")
                + (parcial ? " (menos que el período pedido — ML no siempre entrega la ventana completa)" : "")
                + (f.cr_pct != null ? `\nConversión: ${fNum(f.cr_pct, 1)}% (${fNum(f.uds)} uds ÷ ${fNum(f.visitas)} visitas)` : "")
                + "\nNo incluye Amazon."}>
      <div className="tabular-nums text-slate-700">
        {fNum(f.visitas)}{parcial && <span className="text-amber-500">*</span>}
      </div>
      <div className={`text-[10px] font-semibold tabular-nums ${
          f.cr_pct == null ? "text-slate-300"
          : f.cr_pct >= 5 ? "text-emerald-600" : "text-amber-600"}`}>
        {f.cr_pct == null ? "—" : `${fNum(f.cr_pct, 1)}%`}
      </div>
    </div>
  );
}

function TablaCuenta({ titulo, sub, filas, conCuentas }: {
  titulo: string; sub: string; filas: Fila[];
  /* Solo la lista General muestra de qué cuentas viene cada renglón. */
  conCuentas?: boolean;
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white">
      <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3">
        <Tag size={15} className="text-indigo-500" />
        <h2 className="text-sm font-bold text-slate-800">{titulo}</h2>
        <span className="text-xs text-slate-400">{sub}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-slate-100 text-[10px] uppercase tracking-wide text-slate-400">
              <th className="px-3 py-2 text-left">#</th>
              <th className="px-3 py-2 text-left">Producto</th>
              <th className="px-3 py-2 text-right"
                  title="Visitas a la publicación en Mercado Libre y conversión (unidades ÷ visitas) del mismo período. No incluye Amazon.">
                Visitas · CR%
              </th>
              <th className="px-3 py-2 text-right">Uds</th>
              <th className="px-3 py-2 text-right">Venta</th>
              <th className="px-3 py-2 text-right">Precio prom</th>
              <th className="px-3 py-2 text-right">Costo base</th>
              <th className="px-3 py-2 text-right">Comisión /u</th>
              <th className="px-3 py-2 text-right">Envío real /u</th>
              <th className="px-3 py-2 text-right">Costo final</th>
              <th className="px-3 py-2 text-right">Margen</th>
              <th className="px-3 py-2 text-right">Ganancia período</th>
            </tr>
          </thead>
          <tbody>
            {filas.map((f, i) => (
              <tr key={f.sku} className="border-b border-slate-50 hover:bg-slate-50/60">
                <td className="px-3 py-2 text-slate-400">{i + 1}</td>
                <td className="max-w-[240px] px-3 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-slate-800">{f.sku}</span>
                    {/* Un chip POR CUENTA donde vendió. En la pestaña de una
                        cuenta sobra —el título ya lo dice— y solo sale en la
                        General, que ahora trae un renglón por SKU. */}
                    {/* Puntos + tarjeta, igual que en la tabla. En la pestaña
                        de una cuenta sobra: el título ya dice cuál es. */}
                    {conCuentas && (
                      <span className="shrink-0"><PuntosCuenta f={f} /></span>
                    )}
                    {(() => {
                      const e = CHIP_ESTADO[f.estado ?? "otra"] ?? CHIP_ESTADO.otra;
                      return (
                        <span title={e.ayuda}
                              className={`rounded px-1 text-[9px] font-bold ${e.clase}`}>
                          {e.txt}
                        </span>
                      );
                    })()}
                  </div>
                  <div className="truncate text-[11px] text-slate-400">{f.titulo ?? ""}</div>
                </td>
                <td className="px-3 py-2 text-right"><Visitas f={f} /></td>
                <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-700">{fNum(f.uds)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{fMoney(f.ingreso)}</td>
                <td className="px-3 py-2 text-right"><Precio f={f} /></td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-700"
                    title="costos_validados.costo_total (producto + flete marítimo)">
                  {fMoney(f.costo_base, 2)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-700"
                    title="sale_fee promedio que ML cobró de verdad en los pedidos del período">
                  {fMoney(f.comision_unit, 2)}
                </td>
                <td className="px-3 py-2 text-right"><Envio f={f} /></td>
                <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-800">
                  {fMoney(f.costo_final, 2)}
                </td>
                <td className="px-3 py-2 text-right"><Margen f={f} /></td>
                {/* La ganancia sigue la misma regla que el margen: se muestra,
                    en ámbar, cuando el costo del que sale está en duda. */}
                <td className={`px-3 py-2 text-right font-bold tabular-nums ${
                    f.ganancia_total == null ? "text-slate-300"
                    : (f.precio_prom != null && costoImplausible(f.precio_prom, f.costo_base))
                      ? "text-amber-600"
                      : f.ganancia_total < 0 ? "text-red-500" : "text-emerald-600"}`}
                    title={f.precio_prom != null && costoImplausible(f.precio_prom, f.costo_base)
                           ? avisoCostoImplausible(f.precio_prom, f.costo_base!) : undefined}>
                  {fMoney(f.ganancia_total)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function MargenesRealesModal({ cerrar }: { cerrar: () => void }) {
  const [visible, setVisible] = useState(false);
  const [dias, setDias] = useState(30);
  const [cuenta, setCuenta] = useState<FiltroCuenta>("TODAS");
  const [estado, setEstado] = useState<FiltroEstado>("TODAS");
  const [data, setData] = useState<Respuesta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rondas = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setVisible(true), 20);
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") cerrar(); };
    window.addEventListener("keydown", esc);
    return () => { clearTimeout(t); window.removeEventListener("keydown", esc); };
  }, [cerrar]);

  // El filtro de ESTADO viaja al backend, no se aplica aquí: el top se corta
  // en SQL, así que filtrar en el cliente dejaría "las que sobrevivan de 10"
  // en vez del top 10 de las activas.
  const cargar = useCallback((d: number, est: FiltroEstado) => {
    const q = `dias=${d}` + (est === "TODAS" ? "" : `&estado=${est}`);
    fetchSesion(`${API_BASE}/api/fulfillment/margenes-reales?${q}`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((res: Respuesta) => {
        setData(res); setError(null);
        // El backend consulta hasta `presupuesto` embarques por carga: si aún
        // quedan pendientes, se vuelve a pedir y cada ronda avanza otro tanto.
        if (res.pendientes > 0 && rondas.current < MAX_RONDAS) {
          rondas.current += 1;
          setTimeout(() => cargar(d, est), 2500);
        }
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    rondas.current = 0;
    setData(null);
    cargar(dias, estado);
  }, [dias, estado, cargar]);

  // "Ambas" = UNA lista general, YA FUNDIDA POR EL BACKEND: un renglón por SKU
  // con las cuentas sumadas y el estado resuelto entre ellas. Antes se armaba
  // aquí juntando los dos top-10 y reordenando, lo que repetía el SKU una vez
  // por cuenta y podía perder productos grandes solo en la suma.
  const general: Fila[] = data?.general ?? [];
  const cuentasVisibles = (data?.cuentas ?? []).filter((c) => c.cuenta === cuenta);
  const sufijoEstado = estado === "TODAS" ? ""
    : estado === "activa" ? " activas" : " pausadas";

  return (
    <div
      /* ANCLADO ARRIBA, no centrado: con `items-center`, cada vez que el
         contenido se encogía (al recargar por un filtro) el modal se
         recentraba y el encabezado —con los filtros— se movía 38 px hacia
         abajo. Fijando el borde superior, los controles no se mueven nunca. */
      className={`fixed inset-0 z-50 flex items-start justify-center px-4 pb-4 pt-[5vh] transition-opacity duration-200 ${visible ? "opacity-100" : "opacity-0"}`}
      onClick={cerrar}
    >
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" />
      <div
        onClick={(e) => e.stopPropagation()}
        /* scrollbarGutter reserva el carril de la barra SIEMPRE: sin esto, al
           pasar de "cargando" (contenido corto, sin barra) a la tabla completa
           (con barra) todo se recorría ~15 px de golpe. */
        style={{ scrollbarGutter: "stable" }}
        className={`relative max-h-[90vh] w-full max-w-6xl overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl transition-all duration-200 ${visible ? "translate-y-0 scale-100 opacity-100" : "translate-y-3 scale-95 opacity-0"}`}
      >
        {/* Encabezado en DOS renglones fijos. Antes era uno solo con
            `justify-between`: al aparecer el aviso de "consultando envíos" el
            bloque izquierdo crecía, la fila se partía y los filtros —que viven
            a la derecha— caían a un segundo renglón pegados a la IZQUIERDA.
            Cambiaban de lugar justo cuando el usuario los estaba usando. Con
            renglón propio, los filtros no se mueven pase lo que pase arriba. */}
        <div className="mb-3 space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <BadgePercent size={18} className="shrink-0 text-indigo-600" />
              <span className="text-sm font-bold text-slate-800">Productos más vendidos</span>
              <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-bold text-indigo-600">
                cobros reales de Meli
              </span>
              {data && data.pendientes > 0 && (
                <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-600">
                  <RefreshCw size={11} className="animate-spin" />
                  consultando envíos — faltan {fNum(data.pendientes)} piezas
                </span>
              )}
            </div>
            <button onClick={cerrar} aria-label="Cerrar"
                    className="shrink-0 rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700">
              <X size={18} />
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex overflow-hidden rounded-lg border border-slate-200 text-xs font-semibold">
              {FILTRO_CUENTAS.map((c) => (
                <button key={c.id} onClick={() => setCuenta(c.id)}
                        className={`px-2.5 py-1.5 transition-colors ${cuenta === c.id ? "bg-indigo-600 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>
                  {c.label}
                </button>
              ))}
            </div>
            <div className="flex overflow-hidden rounded-lg border border-slate-200 text-xs font-semibold">
              {FILTRO_ESTADO.map((e) => (
                <button key={e.id} onClick={() => setEstado(e.id)}
                        title={e.id === "TODAS" ? "Activas y pausadas"
                               : `Solo publicaciones ${e.label.toLowerCase()}`}
                        className={`px-2.5 py-1.5 transition-colors ${estado === e.id ? "bg-indigo-600 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>
                  {e.label}
                </button>
              ))}
            </div>
            <div className="flex overflow-hidden rounded-lg border border-slate-200 text-xs font-semibold">
              {[7, 30, 60, 90].map((d) => (
                <button key={d} onClick={() => setDias(d)}
                        className={`px-2.5 py-1.5 transition-colors ${dias === d ? "bg-slate-700 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>
                  {d}d
                </button>
              ))}
            </div>
          </div>
        </div>

        <p className="mb-3 text-[12px] text-slate-500">
          Margen sobre <b>Costo Final</b> = costo base + comisión real +{" "}
          <b>envío real por embarque</b> (API de ML). No incluye cargos de
          almacenamiento FULL.
        </p>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            No se pudo cargar: {error}
          </div>
        )}
        {/* min-h: al cambiar de filtro la tabla desaparece un instante; sin
            una altura mínima el modal se encogía y, al estar centrado, saltaba
            hacia arriba para volver a bajar. Con esto se queda quieto. */}
        <div className="min-h-[420px] space-y-4">
          {!data && !error && (
            <div className="flex h-40 items-center justify-center text-sm text-slate-400">
              Cargando márgenes…
            </div>
          )}
          {cuenta === "TODAS" ? (
            data && (
              <TablaCuenta titulo="General"
                           sub={`top ${general.length} por SKU${sufijoEstado}, sumando ambas cuentas`}
                           filas={general} conCuentas />
            )
          ) : (
            cuentasVisibles.map((c) => (
              <TablaCuenta key={c.cuenta}
                           titulo={NOMBRE_CUENTA[c.cuenta] ?? c.cuenta}
                           sub={`top ${c.filas.length}${sufijoEstado} por unidades vendidas`}
                           filas={c.filas} />
            ))
          )}
          {data && (cuenta === "TODAS" ? general.length === 0 : cuentasVisibles.every((c) => !c.filas.length)) && (
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-400">
              Sin productos {estado === "TODAS" ? "" : `${estado === "activa" ? "activos" : "pausados"} `}
              con venta en este período.
            </div>
          )}
        </div>
        {data && (
          <p className="mt-3 text-[11px] text-slate-400">
            * en <b>Envío</b>: cobertura parcial — el promedio sale de las piezas
            ya consultadas; en carritos con varios productos el cobro del
            embarque se prorratea por unidad. * en <b>Visitas</b>: ML devolvió
            menos días que el período pedido. Las visitas y la conversión son
            solo de Mercado Libre.
          </p>
        )}
      </div>
    </div>
  );
}
