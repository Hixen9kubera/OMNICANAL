"use client";

/**
 * /analisis/conversion — Ventas contra visitas por publicación.
 *
 * Pedido de Eduardo (15-oct-2026): monitorear la conversión de las
 * publicaciones con las burbujas del Monitor de precios FULL. De las tres
 * propuestas de diseño eligió el MAPA DE BURBUJAS como pantalla principal y la
 * BANDEJA DE ALERTAS como segunda vista, aquí en Análisis.
 *
 * Solo Mercado Libre da visitas. Amazon, TikTok, Temu y Walmart muestran sus
 * piezas con las visitas como «sin medición» — con la textura rayada de
 * /monitoreo: «no lo sabemos» no se puede ver igual que «fue cero».
 *
 * Los umbrales viven en el navegador de cada quien (localStorage) mientras
 * Eduardo confirma los definitivos; ver REGLAS_ORIGINALES en lib/conversion.ts.
 */

import { useEffect, useMemo, useState } from "react";
import { Loader2, SlidersHorizontal } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import {
  ALERTAS, CUADRANTES, REGLAS_ORIGINALES, type Cuadrante, type Reglas, type RespConversion,
  fechaCorta, guardarReglas, leerReglas, metricas, num, pct, pesos, piezasPerdidas,
} from "@/lib/conversion";
import MapaBurbujas, { type FilaMapa } from "@/components/conversion/MapaBurbujas";
import BandejaAlertas from "@/components/conversion/BandejaAlertas";
import DetallePublicacion from "@/components/conversion/DetallePublicacion";

const CANALES = [
  { v: "mercado_libre", label: "Mercado Libre" },
  { v: "amazon", label: "Amazon" },
  { v: "tiktok", label: "TikTok" },
  { v: "temu", label: "Temu" },
  { v: "walmart", label: "Walmart" },
];
const ESTADOS = [{ v: "active", label: "Activas" }, { v: "paused", label: "Pausadas" }, { v: "todas", label: "Todas" }];
const RAYADO = { backgroundImage: "repeating-linear-gradient(135deg, #e2e8f0 0 4px, #ffffff 4px 8px)" };

type Vista = "mapa" | "bandeja";

function Seg<T extends string>({ opciones, valor, onCambio }: {
  opciones: { v: T; label: string; nota?: string }[]; valor: T; onCambio: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {opciones.map((o) => (
        <button key={o.v} type="button" aria-pressed={valor === o.v} onClick={() => onCambio(o.v)}
          className={`inline-flex min-h-[40px] items-center rounded-lg border px-3 text-[13px] ${
            valor === o.v ? "border-indigo-200 bg-indigo-50 font-semibold text-indigo-800" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`}>
          {o.label}{o.nota && <span className="ml-1.5 text-[11px] font-normal text-slate-500">{o.nota}</span>}
        </button>
      ))}
    </div>
  );
}

export default function ConversionPage() {
  const [canal, setCanal] = useState("mercado_libre");
  const [cuenta, setCuenta] = useState("todas");
  const [estado, setEstado] = useState("active");
  const [categoria, setCategoria] = useState("todas");
  const [busqueda, setBusqueda] = useState("");
  const [vista, setVista] = useState<Vista>("mapa");
  const [reglas, setReglas] = useState<Reglas>(REGLAS_ORIGINALES);
  const [verReglas, setVerReglas] = useState(false);
  const [tamano, setTamano] = useState<"stock" | "ingreso" | "precio">("stock");
  const [colorPor, setColorPor] = useState<"cuadrante" | "cuenta">("cuadrante");
  const [seleccion, setSeleccion] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<string | null>(null);
  const [datos, setDatos] = useState<RespConversion | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReglas(leerReglas());
    const q = new URLSearchParams(window.location.search);
    if (q.get("vista") === "bandeja") setVista("bandeja");
  }, []);

  const cambiarVista = (v: Vista) => {
    setVista(v);
    const url = new URL(window.location.href);
    if (v === "bandeja") url.searchParams.set("vista", "bandeja"); else url.searchParams.delete("vista");
    window.history.replaceState(null, "", url.toString());
  };
  const cambiarRegla = (k: keyof Reglas, v: number) => {
    const r = { ...reglas, [k]: v };
    setReglas(r); guardarReglas(r);
  };

  useEffect(() => {
    let vivo = true;
    setCargando(true); setError(null); setSeleccion(null);
    const q = new URLSearchParams({ canal, estado });
    if (cuenta !== "todas" && canal === "mercado_libre") q.set("cuenta", cuenta);
    fetchSesion(`${API_BASE}/api/analisis/conversion?${q.toString()}`, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || `HTTP ${r.status}`);
        return r.json();
      })
      .then((d: RespConversion) => { if (vivo) setDatos(d); })
      .catch((e: Error) => { if (vivo) { setDatos(null); setError(e.message); } })
      .finally(() => { if (vivo) setCargando(false); });
    return () => { vivo = false; };
  }, [canal, cuenta, estado]);

  const categorias = useMemo(() => {
    const s = new Set<string>();
    datos?.publicaciones.forEach((p) => { if (p.categoria_raiz) s.add(p.categoria_raiz); });
    return Array.from(s).sort((a, b) => a.localeCompare(b, "es"));
  }, [datos]);

  const filas: FilaMapa[] = useMemo(() => {
    if (!datos) return [];
    const t = busqueda.trim().toLowerCase();
    return datos.publicaciones
      .filter((p) => categoria === "todas" || p.categoria_raiz === categoria)
      .filter((p) => !t || [p.sku, p.titulo, p.listing_id].some((x) => (x ?? "").toLowerCase().includes(t)))
      .map((p) => ({ p, m: metricas(p, reglas) }));
  }, [datos, categoria, busqueda, reglas]);

  const medidas = filas.filter((f) => f.p.visitas != null);
  const sumV = medidas.reduce((a, f) => a + (f.p.visitas ?? 0), 0);
  const sumUMedidas = medidas.reduce((a, f) => a + f.p.piezas, 0);
  const sumU = filas.reduce((a, f) => a + f.p.piezas, 0);
  const enAlerta = filas.filter((f) => f.m.alertas.length);
  const medidoAl = medidas.reduce<string | null>((mx, f) => (f.p.visitas_at && (!mx || f.p.visitas_at > mx) ? f.p.visitas_at : mx), null);
  const conteo = (q: Cuadrante) => filas.filter((f) => f.m.cuadrante === q).length;
  const sel = filas.find((f) => f.p.listing_id === seleccion) ?? null;
  const conVisitas = datos?.tiene_visitas ?? canal === "mercado_libre";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Conversión de publicaciones</h1>
          <p className="mt-1 text-sm text-slate-600">
            Cuánta gente ve cada publicación y cuántas piezas vende, en los últimos 30 días.
            {conVisitas && medidoAl && <> Visitas medidas el {fechaCorta(medidoAl)}.</>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {conVisitas && (
            <nav aria-label="Vista" className="flex gap-1 rounded-xl border border-slate-200 bg-white p-1">
              {(["mapa", "bandeja"] as Vista[]).map((v) => (
                <button key={v} type="button" aria-pressed={vista === v} onClick={() => cambiarVista(v)}
                  className={`min-h-[36px] rounded-lg px-3 text-[13px] ${vista === v ? "bg-indigo-50 font-semibold text-indigo-800" : "text-slate-600 hover:bg-slate-50"}`}>
                  {v === "mapa" ? "Mapa de burbujas" : `Bandeja de alertas${enAlerta.length ? ` (${enAlerta.length})` : ""}`}
                </button>
              ))}
            </nav>
          )}
          <button type="button" aria-expanded={verReglas} onClick={() => setVerReglas((x) => !x)}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 text-[13px] font-semibold text-slate-700 hover:bg-slate-50">
            <SlidersHorizontal size={15} /> Reglas y umbrales
          </button>
        </div>
      </div>

      <section aria-label="Filtros" className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-slate-600">Canal</span>
          <Seg opciones={CANALES.map((c) => ({ ...c, nota: c.v === "mercado_libre" ? undefined : "sin visitas" }))}
            valor={canal} onCambio={(v) => { setCanal(v); setCategoria("todas"); }} />
        </div>
        {canal === "mercado_libre" && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-slate-600">Cuenta</span>
              <Seg opciones={[{ v: "todas", label: "Ambas" }, { v: "BEKURA", label: "BEKURA" }, { v: "SANCORFASHION", label: "SANCORFASHION" }]}
                valor={cuenta} onCambio={setCuenta} />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-slate-600">Estado</span>
              <Seg opciones={ESTADOS} valor={estado} onCambio={setEstado} />
            </div>
            <label className="flex items-center gap-2 text-xs font-semibold text-slate-600">
              Categoría
              <select value={categoria} onChange={(e) => setCategoria(e.target.value)}
                className="min-h-[40px] rounded-lg border border-slate-200 bg-white px-2 text-[13px] font-normal text-slate-800">
                <option value="todas">Todas</option>
                {categorias.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          </>
        )}
        <label className="flex min-w-[220px] flex-1 items-center gap-2 text-xs font-semibold text-slate-600">
          Buscar
          <input type="search" value={busqueda} onChange={(e) => setBusqueda(e.target.value)} placeholder="SKU, título o MLM"
            className="min-h-[40px] flex-1 rounded-lg border border-slate-200 px-3 text-[13px] font-normal text-slate-800" />
        </label>
      </section>

      {verReglas && (
        <section aria-label="Reglas y umbrales" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="grid gap-x-6 gap-y-3 text-[13px] text-slate-700" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
            {([
              ["visitasAltas", "Mucho tráfico desde (visitas en 30 d)", 1],
              ["convMeta", "Meta de conversión (%)", 100],
              ["minVisitas", "Visitas mínimas para juzgar", 1],
              ["convBaja", "«Casi sin ventas»: conversión bajo (%)", 100],
              ["caidaConv", "«La conversión cayó»: caída de (%)", 100],
              ["caidaVisitas", "«Las visitas bajaron»: caída de (%)", 100],
              ["minVisitasCaida", "Visitas mínimas para juzgar una caída", 1],
              ["convAgota", "«Se agota»: conversión desde (%)", 100],
              ["diasAgota", "«Se agota»: stock para días o menos", 1],
            ] as [keyof Reglas, string, number][]).map(([k, etiqueta, f]) => (
              <label key={k} className="flex items-center justify-between gap-3">
                <span>{etiqueta}</span>
                <input type="number" min={0} step={f === 100 ? 0.5 : 1} value={+(reglas[k] * f).toFixed(2)}
                  onChange={(e) => cambiarRegla(k, Number(e.target.value) / f)}
                  className="h-10 w-24 rounded-lg border border-slate-300 px-2 text-right font-semibold text-slate-900" />
              </label>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3 text-xs text-slate-500">
            <span>Se guardan solo en este navegador. Son valores propuestos: falta que Eduardo confirme los definitivos (salvo «se agota», que es de sus reglas de FULL).</span>
            <button type="button" onClick={() => { setReglas(REGLAS_ORIGINALES); guardarReglas(REGLAS_ORIGINALES); }}
              className="rounded-lg border border-slate-200 px-3 py-2 font-semibold text-slate-700 hover:bg-slate-50">Volver a las originales</button>
          </div>
        </section>
      )}

      {error && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">No se pudo leer la conversión: {error}</div>}
      {cargando && !datos && (
        <div className="flex items-center gap-2 py-16 text-sm text-slate-500"><Loader2 size={16} className="animate-spin" /> Leyendo publicaciones…</div>
      )}

      {datos && !conVisitas && (
        <section aria-label="Ventas del canal" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-[15px] font-semibold text-slate-900">{CANALES.find((c) => c.v === canal)?.label}: solo ventas</h2>
            <span className="flex items-center gap-1.5 text-xs text-slate-600">
              <span className="inline-block h-3 w-6 rounded-sm border border-slate-300" style={RAYADO} />
              sin medición: este canal no nos da visitas (no es cero)
            </span>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-[13px]">
              <thead>
                <tr className="text-left text-xs text-slate-600">
                  <th scope="col" className="px-3 py-2 font-semibold">Producto</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Piezas 30 d</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">30 d anteriores</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Canceladas</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Visitas</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Conversión</th>
                </tr>
              </thead>
              <tbody>
                {filas.slice(0, 300).map(({ p }) => (
                  <tr key={`${p.sku}|${p.cuenta}`} className="border-t border-slate-100">
                    <td className="px-3 py-2.5"><div className="font-semibold text-slate-900">{p.titulo ?? p.sku}</div><div className="text-xs text-slate-500">{p.sku}</div></td>
                    <td className="px-3 py-2.5 text-right font-semibold text-slate-900">{num(p.piezas)}</td>
                    <td className="px-3 py-2.5 text-right text-slate-700">{num(p.piezas_ant)}</td>
                    <td className="px-3 py-2.5 text-right text-slate-700">{num(p.canceladas)}</td>
                    <td className="px-3 py-2.5"><span className="inline-block rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600" style={RAYADO}>sin medición</span></td>
                    <td className="px-3 py-2.5"><span className="inline-block rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600" style={RAYADO}>sin medición</span></td>
                  </tr>
                ))}
                {!filas.length && <tr><td colSpan={6} className="px-3 py-10 text-center text-sm text-slate-500">Sin ventas en los últimos 60 días.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {datos && conVisitas && (
        <>
          <section aria-label="Resumen" className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3.5 shadow-sm">
              <div className="text-xs font-semibold text-slate-600">Visitas</div>
              <div className="mt-1 text-[26px] font-bold text-slate-900">{num(sumV)}</div>
              <div className="text-xs text-slate-500">{num(filas.length)} publicaciones{filas.length - medidas.length > 0 ? ` · ${num(filas.length - medidas.length)} sin medición` : ""}</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3.5 shadow-sm">
              <div className="text-xs font-semibold text-slate-600">Piezas vendidas</div>
              <div className="mt-1 text-[26px] font-bold text-slate-900">{num(sumU)}</div>
              <div className="text-xs text-slate-500">pagadas; las canceladas no cuentan</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3.5 shadow-sm">
              <div className="text-xs font-semibold text-slate-600">Conversión</div>
              <div className="mt-1 text-[26px] font-bold text-slate-900">{pct(sumV ? sumUMedidas / sumV : null)}</div>
              <div className="text-xs text-slate-500">piezas ÷ visitas · meta {pct(reglas.convMeta)}</div>
            </div>
            <button type="button" onClick={() => cambiarVista("bandeja")}
              className="rounded-2xl border border-orange-200 bg-orange-50 px-4 py-3.5 text-left shadow-sm hover:border-orange-300">
              <div className="text-xs font-semibold text-orange-800">Para revisar</div>
              <div className="mt-1 text-[26px] font-bold text-orange-950">{num(enAlerta.length)}</div>
              <div className="text-xs text-orange-800">publicaciones con al menos una alerta</div>
            </button>
          </section>

          {vista === "bandeja" ? (
            <BandejaAlertas filas={filas} reglas={reglas} onAbrir={setDetalle} />
          ) : (
            <div className="flex flex-wrap items-start gap-4">
              <section aria-label="Mapa de burbujas" className="min-w-0 rounded-2xl border border-slate-200 bg-white px-5 pb-5 pt-4 shadow-sm" style={{ flex: "999 1 640px" }}>
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <h2 className="text-[15px] font-semibold text-slate-900">Visitas contra ventas, una burbuja por publicación</h2>
                  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600">
                    <label className="flex items-center gap-1.5">Tamaño
                      <select value={tamano} onChange={(e) => setTamano(e.target.value as typeof tamano)} className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-800">
                        <option value="stock">Stock</option><option value="ingreso">Ingreso 30 d</option><option value="precio">Precio</option>
                      </select>
                    </label>
                    <label className="flex items-center gap-1.5">Color
                      <select value={colorPor} onChange={(e) => setColorPor(e.target.value as typeof colorPor)} className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-800">
                        <option value="cuadrante">Grupo</option><option value="cuenta">Cuenta</option>
                      </select>
                    </label>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-700">
                  {colorPor === "cuenta"
                    ? [["BEKURA", "#4f46e5"], ["SANCORFASHION", "#f59e0b"]].map(([c, col]) => (
                        <span key={c} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: col }} />{c}</span>))
                    : (["gan", "traf", "joya", "dorm", "chica"] as Cuadrante[]).map((q) => (
                        <span key={q} className="flex items-center gap-1.5">
                          <span className="h-2.5 w-2.5 rounded-full" style={q === "chica" ? { border: "1.5px dashed #64748b" } : { background: CUADRANTES[q].color }} />
                          {CUADRANTES[q].nombre} <b className="font-semibold">{num(conteo(q))}</b>
                        </span>))}
                </div>
                <MapaBurbujas filas={filas} reglas={reglas} seleccion={seleccion} onSeleccion={setSeleccion} tamano={tamano} colorPor={colorPor} />
                <div className="flex flex-wrap gap-x-5 gap-y-1.5 border-t border-slate-100 pt-3 text-xs text-slate-600">
                  <span><b className="font-semibold text-slate-700">Diagonal:</b> conversión. Arriba de la línea índigo, la publicación convierte mejor que la meta.</span>
                  <span><b className="font-semibold text-slate-700">Tamaño:</b> {tamano === "ingreso" ? "pesos vendidos en 30 días" : tamano === "precio" ? "precio de hoy" : "piezas en stock (FULL en las de FULL)"}.</span>
                  <span><b className="font-semibold text-slate-700">Punteadas:</b> menos de {reglas.minVisitas} visitas, muy pocas para juzgar.</span>
                </div>
              </section>

              <aside aria-label="Panel lateral" className="flex min-w-0 flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" style={{ flex: "1 1 320px" }}>
                {sel ? (
                  <>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-sm font-semibold leading-snug text-slate-900">{sel.p.titulo ?? sel.p.sku}</div>
                        <div className="mt-0.5 text-xs text-slate-500">{sel.p.sku} · {sel.p.cuenta}{sel.p.full ? " · FULL" : ""}</div>
                      </div>
                      <button type="button" onClick={() => setSeleccion(null)} aria-label="Quitar selección"
                        className="h-11 w-11 shrink-0 rounded-lg border border-slate-200 text-lg text-slate-600 hover:bg-slate-50">×</button>
                    </div>
                    <span className="self-start rounded-full px-2.5 py-1 text-xs font-semibold text-white"
                      style={{ background: sel.m.cuadrante === "gan" ? "#4338ca" : sel.m.cuadrante === "traf" ? "#c2410c" : sel.m.cuadrante === "joya" ? "#0f766e" : "#64748b" }}>
                      {CUADRANTES[sel.m.cuadrante].nombre}
                    </span>
                    <div className="grid grid-cols-2 gap-2.5">
                      {[
                        ["Visitas 30 d", num(sel.p.visitas), sel.p.visitas_ant != null ? `antes ${num(sel.p.visitas_ant)}` : "sin medición anterior"],
                        ["Piezas 30 d", num(sel.p.piezas), sel.p.piezas_ant != null ? `antes ${num(sel.p.piezas_ant)}` : ""],
                        ["Conversión", pct(sel.m.conv), `antes ${pct(sel.m.convAnt)}`],
                        ["Precio · stock", pesos(sel.p.precio), `${num(sel.p.stock)} pzs · ${Number.isFinite(sel.m.dias) ? `${Math.round(sel.m.dias)} días` : "sin ventas"}`],
                      ].map(([t, v, s]) => (
                        <div key={t} className="rounded-xl bg-slate-50 p-2.5">
                          <div className="text-[11px] text-slate-500">{t}</div>
                          <div className="text-lg font-bold text-slate-900">{v}</div>
                          <div className="text-[11px] text-slate-500">{s}</div>
                        </div>
                      ))}
                    </div>
                    {sel.m.alertas.map((a) => (
                      <div key={a.k} className="rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-[13px] text-orange-950">
                        <span className="font-semibold">{a.texto}.</span> {ALERTAS[a.k].revisar}
                      </div>
                    ))}
                    <button type="button" onClick={() => setDetalle(sel.p.listing_id as string)}
                      className="min-h-[44px] rounded-xl bg-indigo-600 text-sm font-semibold text-white hover:bg-indigo-700">
                      Ver detalle: ventas día por día
                    </button>
                  </>
                ) : (
                  <>
                    <div>
                      <h2 className="text-[15px] font-semibold text-slate-900">Para revisar hoy</h2>
                      <p className="mt-1 text-xs text-slate-500">Toca una burbuja o una fila para ver la publicación.</p>
                    </div>
                    {enAlerta.slice().sort((a, b) => piezasPerdidas(b.p, reglas) - piezasPerdidas(a.p, reglas)).slice(0, 8).map((f) => (
                      <button key={f.p.listing_id} type="button" onClick={() => setSeleccion(f.p.listing_id as string)}
                        className="flex min-h-[44px] w-full items-start gap-2.5 rounded-xl border border-slate-100 p-2.5 text-left hover:bg-slate-50">
                        <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: CUADRANTES[f.m.cuadrante].color }} />
                        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                          <span className="truncate text-[13px] font-semibold text-slate-900">{f.p.titulo ?? f.p.sku}</span>
                          <span className="text-xs font-medium text-orange-800">{f.m.alertas[0].texto}{f.m.alertas.length > 1 ? ` · y ${f.m.alertas.length - 1} más` : ""}</span>
                          <span className="text-[11px] text-slate-500">{num(f.p.visitas)} visitas · {num(f.p.piezas)} pzs · {pct(f.m.conv)} · {f.p.cuenta}</span>
                        </span>
                      </button>
                    ))}
                    {!enAlerta.length && <p className="text-[13px] text-slate-500">Ninguna publicación con alertas con estos filtros.</p>}
                    {enAlerta.length > 8 && (
                      <button type="button" onClick={() => cambiarVista("bandeja")} className="self-start text-[13px] font-semibold text-indigo-700 hover:underline">
                        Ver las {num(enAlerta.length)} en la bandeja →
                      </button>
                    )}
                  </>
                )}
              </aside>
            </div>
          )}
        </>
      )}

      {detalle && <DetallePublicacion listingId={detalle} reglas={reglas} onCerrar={() => setDetalle(null)} />}
    </div>
  );
}
