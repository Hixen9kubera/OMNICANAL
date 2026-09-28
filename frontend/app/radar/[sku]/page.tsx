"use client";

/**
 * /radar/[sku] — Detalle del radar de precios para un SKU (F1, SOLO LECTURA).
 *
 * Por qué el radar propone lo que propone: la dirección con sus razones, la
 * escalera de precio (piso, referencia, techo, sugerido, nuestras cuentas y
 * los rivales), la contribución por cuenta con la procedencia de cada renglón,
 * los comparables, la rotación de 90 días y las señales de calidad.
 *
 * Nada aquí escribe. "Crear propuesta" está deshabilitado hasta F6. Mismo
 * guard que la lista: solo admin, y un 401/403 se pinta como "No disponible".
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ChevronRight, CircleDot, Info, Lock, Package, Star, Truck } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import AccesoRadar from "@/components/radar/Acceso";
import Desglose from "@/components/radar/Desglose";
import Escalera from "@/components/radar/Escalera";
import Rotacion, { diasDeSerie } from "@/components/radar/Rotacion";
import {
  LLAVE_FILTROS_RADAR,
  META_DIRECCION,
  cambioRelativo,
  decimal,
  entero,
  etiquetaClase,
  etiquetaContenedor,
  etiquetaCuenta,
  fechaCorta,
  fechaHora,
  fraccionComoPct,
  num,
  pesos,
  porcentaje,
  motivoSinReferencia,
  textoCobertura,
} from "@/components/radar/formato";
import {
  CajaError,
  Cargando,
  ChipDireccion,
  EtiquetaProcedencia,
  NoDisponible,
  PuntoExperiencia,
  SinDato,
  SubnavRadar,
  Valor,
} from "@/components/radar/ui";
import { ApiError, mensajeDeError, radarPreciosSku, type RadarCuenta, type RadarDetalle } from "@/lib/api";

export default function RadarSkuPage() {
  return (
    <>
      <AppNavbar />
      <AccesoRadar>
        <DetalleRadar />
      </AccesoRadar>
    </>
  );
}

function skuDeRuta(crudo: string | string[] | undefined): string {
  const s = Array.isArray(crudo) ? crudo[0] : crudo ?? "";
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

function DetalleRadar() {
  const params = useParams<{ sku: string }>();
  const sku = skuDeRuta(params?.sku);
  const [detalle, setDetalle] = useState<RadarDetalle | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noDisponible, setNoDisponible] = useState(false);
  const [noEncontrado, setNoEncontrado] = useState(false);
  const [intento, setIntento] = useState(0);
  const [volver, setVolver] = useState("/radar");

  useEffect(() => {
    try {
      const qs = window.sessionStorage.getItem(LLAVE_FILTROS_RADAR);
      if (qs) setVolver(`/radar?${qs}`);
    } catch {
      // sin almacenamiento: se vuelve a la lista sin filtros
    }
  }, []);

  useEffect(() => {
    if (!sku) return;
    const ctl = new AbortController();
    setCargando(true);
    setError(null);
    setNoEncontrado(false);
    radarPreciosSku(sku, ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) setDetalle(d);
      })
      .catch((e) => {
        if (ctl.signal.aborted) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setNoDisponible(true);
          return;
        }
        if (e instanceof ApiError && e.status === 404) {
          setNoEncontrado(true);
          return;
        }
        setError(mensajeDeError(e, "No se pudo leer el detalle del radar."));
      })
      .finally(() => {
        if (!ctl.signal.aborted) setCargando(false);
      });
    return () => ctl.abort();
  }, [sku, intento]);

  if (noDisponible) return <NoDisponible />;

  return (
    <>
      <SubnavRadar ambiente={detalle?.ambiente} />
      <main className="mx-auto flex max-w-[1800px] flex-col gap-5 px-4 py-6 sm:px-6">
        <nav aria-label="Migas" className="flex items-center gap-1.5 text-[13px] text-[#4A5163]">
          <Link href={volver} className="font-medium text-indigo-700 hover:text-indigo-800">Radar</Link>
          <ChevronRight size={12} aria-hidden />
          <span>Precios</span>
          <ChevronRight size={12} aria-hidden />
          <span aria-current="page" className="font-semibold text-slate-900">{sku}</span>
        </nav>

        {cargando && !detalle ? (
          <Cargando texto="Leyendo el detalle…" />
        ) : noEncontrado ? (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-[#E4E7EE] bg-white px-4 py-14 text-center">
            <Package size={26} className="text-slate-300" aria-hidden />
            <span className="text-sm font-semibold text-slate-700">
              {sku} no tiene publicación activa en Mercado Libre.
            </span>
            <span className="text-xs text-slate-500">El radar sólo mira publicaciones activas de BEKURA y SANCOR.</span>
            <Link href={volver} className="text-sm font-semibold text-indigo-700 hover:underline">Volver al radar</Link>
          </div>
        ) : error ? (
          <CajaError mensaje={error} onReintentar={() => setIntento((n) => n + 1)} />
        ) : detalle ? (
          <Contenido d={detalle} volver={volver} />
        ) : null}
      </main>
    </>
  );
}

function precioDe(c: RadarCuenta): number | null {
  return c.precio_cobrado ?? c.precio ?? null;
}

function Seccion({
  titulo,
  subtitulo,
  derecha,
  children,
  id,
}: {
  titulo: string;
  subtitulo?: string | null;
  derecha?: React.ReactNode;
  children: React.ReactNode;
  id: string;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-4 rounded-xl border border-[#E4E7EE] bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 id={id} className="text-base font-semibold text-slate-900">{titulo}</h2>
          {subtitulo && <span className="text-[13px] text-[#4A5163]">{subtitulo}</span>}
        </div>
        {derecha}
      </div>
      {children}
    </section>
  );
}

function Dato({ titulo, children, nota }: { titulo: string; children: React.ReactNode; nota?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-[#4A5163]">{titulo}</span>
      <span className="text-[15px] font-semibold tabular-nums text-slate-900">{children}</span>
      {nota && <span className="text-xs text-[#4A5163]">{nota}</span>}
    </div>
  );
}

function titularDireccion(d: RadarDetalle, pPrincipal: number | null): string {
  if ((d.direccion === "subir" || d.direccion === "bajar") && d.precio_sugerido !== null) {
    const cambio = cambioRelativo(pPrincipal, d.precio_sugerido);
    return `Propuesta: ${pesos(d.precio_sugerido)}${cambio ? ` (${cambio})` : ""}`;
  }
  switch (d.direccion) {
    case "mantener":
      return "Mantener el precio";
    case "caro_justificado":
      return "Mantener: el premio de calidad cubre la diferencia";
    case "no_competir":
      return "No competir en precio";
    case "sin_referencia":
      return "Sin referencia de mercado confiable";
    default:
      return META_DIRECCION[d.direccion]?.etiqueta ?? "";
  }
}

function Contenido({ d, volver }: { d: RadarDetalle; volver: string }) {
  const meta = META_DIRECCION[d.direccion] ?? META_DIRECCION.sin_referencia;
  const cuentas = d.cuentas ?? [];
  const principal = cuentas.find((c) => c.cuenta === d.cuenta_principal) ?? cuentas[0] ?? null;
  const pPrincipal = principal ? precioDe(principal) : null;
  const cont = etiquetaContenedor(d.contenedor);
  const pasoMax = fraccionComoPct(num(d.parametros?.paso_max));
  const premioTope = fraccionComoPct(num(d.parametros?.premio_tope));
  const hoy = cuentas
    .filter((c) => precioDe(c) !== null)
    .map((c) => `${pesos(precioDe(c))} en ${etiquetaCuenta(c.cuenta)}`)
    .join(" y ");

  const dias = diasDeSerie(d.serie_90d ?? [], d.generado_en);
  const vendidas90 = dias.reduce((s, x) => s + x.unidades, 0);
  const vendidas30 = dias.slice(-30).reduce((s, x) => s + x.unidades, 0);

  const ref = d.referencia;
  const fuenteRef =
    ref?.precio !== null && ref?.precio !== undefined
      ? `Referencia: mediana de ${entero(ref.n) ?? "?"} rivales · ${ref.fuente === "categoria" ? "categoría" : "búsqueda"}${
          ref.termino ? ` «${ref.termino}»` : ""
        }`
      : `Sin referencia de mercado · ${motivoSinReferencia(ref?.motivo, num(d.parametros?.n_min_referencia)).toLowerCase()}`;

  return (
    <>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-[26px] font-bold tabular-nums tracking-tight text-slate-900">{d.sku}</h1>
            {cont && (
              <span
                className="rounded-md border border-[#D0D5DD] px-2 py-0.5 text-xs font-semibold text-[#4A5163]"
                title={d.contenedor_multi ? "Llegó en varios contenedores; se muestra el más antiguo" : "Contenedor"}
              >
                {cont}
                {d.contenedor_multi ? " · y otros" : ""}
              </span>
            )}
            {etiquetaClase(d.clase) && (
              <span className="rounded-full bg-[#EEF0F4] px-2.5 py-0.5 text-xs font-semibold text-[#475467]">
                Clase {etiquetaClase(d.clase)}
              </span>
            )}
          </div>
          <p className="text-[15px] text-[#4A5163]">
            {d.titulo ?? "Sin título"} · Mercado Libre
            {cuentas.length > 0 && <>, publicada en {cuentas.map((c) => etiquetaCuenta(c.cuenta)).join(" y ")}</>}
          </p>
        </div>
        <div className="flex flex-col items-start gap-1 text-[13px] text-[#4A5163] lg:items-end">
          {principal && (
            <span className="inline-flex items-center gap-1.5">
              <Info size={15} aria-hidden />
              Se decide por SKU con {etiquetaCuenta(principal.cuenta)} (la que más vende)
            </span>
          )}
          <span>
            {d.generado_en ? `Datos al ${fechaHora(d.generado_en)}` : "Fecha de la foto sin dato"} · solo lectura
          </span>
        </div>
      </div>

      {/* Banda de dirección */}
      <section
        aria-label="Dirección del precio"
        className="flex flex-col gap-5 rounded-xl border border-[#E4E7EE] bg-white p-5 lg:flex-row"
        style={{ borderLeft: `4px solid ${meta.color}` }}
      >
        <div className="flex flex-col gap-2 lg:w-[420px] lg:shrink-0">
          <ChipDireccion direccion={d.direccion} grande />
          <div className="text-xl font-bold text-slate-900">{titularDireccion(d, pPrincipal)}</div>
          <div className="text-[13px] leading-relaxed text-[#4A5163]">
            {hoy ? `Hoy ${hoy}` : "Sin precio cobrado"}
            {d.piso !== null && <> · piso {pesos(d.piso)}</>}
            {d.techo !== null && <> · techo {pesos(d.techo)}</>}
            {pasoMax && (
              <>
                <br />
                Paso máximo ±{pasoMax} por cambio · decisión, no medición
              </>
            )}
          </div>
        </div>
        <div className="hidden w-px bg-[#EEF0F4] lg:block" />
        <div className="flex flex-1 flex-col gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-[#4A5163]">Por qué</span>
          {(d.razones ?? []).length === 0 ? (
            <SinDato texto="El backend no mandó razones" />
          ) : (
            <ul className="flex flex-col gap-1.5">
              {d.razones.map((r, k) => (
                <li key={k} className="flex items-start gap-2 text-sm text-slate-800">
                  <CircleDot size={14} className="mt-0.5 shrink-0" style={{ color: meta.color }} aria-hidden />
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <Seccion
        id="t-escalera"
        titulo="Escalera de precio"
        subtitulo="Dónde queda cada precio frente al mismo producto en Mercado Libre"
        derecha={
          <span className="text-xs text-[#4A5163]">
            {fuenteRef}
            {ref?.capturado_en && <> · captura {fechaCorta(ref.capturado_en)}</>}
          </span>
        }
      >
        <Escalera item={d} />
      </Seccion>

      {cuentas.length > 0 && (
        <div className="grid gap-5 md:grid-cols-2">
          {cuentas.map((c) => (
            <TarjetaCuenta key={c.cuenta} c={c} principal={c.cuenta === d.cuenta_principal} />
          ))}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <Seccion
          id="t-desglose"
          titulo="Desglose de contribución"
          subtitulo="Por pieza, con la procedencia de cada renglón · el costo del producto no entra"
        >
          <Desglose cuentas={cuentas} inicial={d.cuenta_principal} parametros={d.parametros} />
        </Seccion>

        <Seccion
          id="t-mercado"
          titulo="Mismo producto en el mercado"
          subtitulo={
            ref?.termino
              ? `Resultados de la búsqueda «${ref.termino}» · en el orden de Mercado Libre`
              : "Resultados capturados · en el orden de Mercado Libre"
          }
          derecha={
            ref?.precio !== null && ref?.precio !== undefined ? (
              <span className="rounded-full bg-[#EEF0FF] px-2.5 py-1 text-xs font-semibold text-[#3730A3]">
                Mediana {pesos(ref.precio)}
              </span>
            ) : undefined
          }
        >
          <Comparables d={d} />
        </Seccion>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Seccion
          id="t-rotacion"
          titulo="Rotación"
          subtitulo="Unidades vendidas por día · últimos 90 días · todas las cuentas"
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Dato
              titulo="Stock"
              nota={
                d.stock_detalle && (d.stock_detalle.propio !== null || d.stock_detalle.full !== null)
                  ? `Propio ${entero(d.stock_detalle.propio) ?? "sin dato"} · en Full ${entero(d.stock_detalle.full) ?? "sin dato"}`
                  : "Espejo de Woo · compartido entre cuentas"
              }
            >
              {d.stock !== null ? `${entero(d.stock)} piezas` : <SinDato />}
            </Dato>
            <Dato
              titulo="Cobertura"
              nota={d.ventas_dia !== null ? `Al ritmo de ${decimal(d.ventas_dia)} por día` : undefined}
            >
              <Valor texto={textoCobertura(d.cobertura_dias, d.ventas_dia)} />
            </Dato>
            <Dato titulo="Vendidas en 90 días" nota={`${entero(vendidas30)} en los últimos 30`}>
              {`${entero(vendidas90)} piezas`}
            </Dato>
          </div>
          <Rotacion serie={d.serie_90d ?? []} hasta={d.generado_en} ventasDia={d.ventas_dia} />
        </Seccion>

        <Seccion
          id="t-calidad"
          titulo="Calidad del canal"
          subtitulo={`Señales que sostienen el premio de calidad${premioTope ? ` (tope ${premioTope})` : ""}`}
          derecha={
            <span className="rounded-md bg-[#FEF3D6] px-2 py-1 text-xs font-semibold text-[#8A5A00]">
              Premio hoy {porcentaje(d.premio_calidad_pct, true) ?? "sin dato"} · decisión, no medición
            </span>
          }
        >
          <Calidad d={d} />
        </Seccion>
      </div>

      <section aria-label="Acciones" className="flex flex-wrap items-center gap-3 rounded-xl border border-[#E4E7EE] bg-white px-5 py-4">
        <Info size={16} className="text-[#4A5163]" aria-hidden />
        <span id="nota-f6" className="text-sm text-[#4A5163]">
          Las propuestas llegan en F6. Hoy esta pantalla solo lee.
        </span>
        <div className="flex-1" />
        <Link
          href={volver}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[#D0D5DD] bg-white px-3.5 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
        >
          <ArrowLeft size={16} aria-hidden />
          Volver al radar
        </Link>
        <button
          type="button"
          disabled
          aria-describedby="nota-f6"
          title="Llega en F6"
          className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-lg bg-slate-200 px-3.5 py-2 text-sm font-semibold text-slate-500"
        >
          <Lock size={15} aria-hidden />
          Crear propuesta
        </button>
      </section>
    </>
  );
}

function TarjetaCuenta({ c, principal }: { c: RadarCuenta; principal: boolean }) {
  const cobrado = c.precio_cobrado;
  const igual = c.precio !== null && cobrado !== null && c.precio === cobrado;
  const conversion =
    typeof c.unidades_30d === "number" && typeof c.visitas_30d === "number" && c.visitas_30d > 0
      ? porcentaje((c.unidades_30d / c.visitas_30d) * 100)
      : null;
  const porDia = typeof c.unidades_30d === "number" ? decimal(c.unidades_30d / 30) : null;
  const valor = c.contribucion?.valor ?? null;
  return (
    <section aria-label={`Cuenta ${etiquetaCuenta(c.cuenta)}`} className="flex flex-col gap-4 rounded-xl border border-[#E4E7EE] bg-white p-5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold text-slate-900">{etiquetaCuenta(c.cuenta)}</span>
        {principal && (
          <span className="rounded-full bg-[#EEF0FF] px-2 py-0.5 text-[11px] font-semibold text-[#3730A3]">Decide</span>
        )}
        <span className="text-xs text-[#4A5163]">Mercado Libre{c.item_id ? ` · ${c.item_id}` : ""}</span>
        {(c.gemelas?.length ?? 0) > 0 && (
          <span
            className="rounded-md bg-[#FEF3D6] px-1.5 py-px text-[11px] font-semibold text-[#8A5A00]"
            title={`Otras publicaciones del SKU en esta cuenta: ${c.gemelas?.join(", ")}`}
          >
            +{c.gemelas?.length} gemela{(c.gemelas?.length ?? 0) === 1 ? "" : "s"} · se toma la que más vende
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-4 border-b border-[#EEF0F4] pb-4">
        <Dato titulo="Precio publicado" nota="En la ficha de ML">
          <Valor texto={pesos(c.precio)} />
        </Dato>
        <Dato
          titulo="Precio cobrado"
          nota={cobrado === null ? undefined : igual ? "Igual al publicado" : "Distinto del publicado"}
        >
          <Valor texto={pesos(cobrado)} />
        </Dato>
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Dato titulo="Ventas 30 d" nota={porDia !== null ? `${porDia} por día` : undefined}>
          <Valor texto={entero(c.unidades_30d)} />
        </Dato>
        <Dato titulo="Visitas 30 d">
          <Valor texto={entero(c.visitas_30d)} />
        </Dato>
        <Dato titulo="Conversión" nota="Ventas ÷ visitas">
          <Valor texto={conversion} />
        </Dato>
        <Dato titulo="Full">
          {c.full === null ? <SinDato /> : c.full ? (
            <span className="inline-flex items-center gap-1 text-[#1849A9]"><Truck size={14} aria-hidden />Sí</span>
          ) : "No"}
        </Dato>
        <Dato titulo="Calidad de publicación">
          {typeof c.calidad === "number" ? `${entero(c.calidad)}/100` : <SinDato />}
        </Dato>
        <Dato titulo="Experiencia de compra">
          <span className="text-sm font-semibold"><PuntoExperiencia experiencia={c.experiencia} /></span>
        </Dato>
      </div>
      <div className="flex flex-wrap items-baseline gap-2 rounded-lg bg-[#F6F7FB] px-3 py-2.5">
        <span className="text-xs text-[#4A5163]">Contribución por pieza</span>
        {valor === null ? (
          <SinDato />
        ) : (
          <span className={`text-[15px] font-bold tabular-nums ${valor < 0 ? "text-rose-700" : "text-slate-900"}`}>
            {pesos(valor)}
          </span>
        )}
        <EtiquetaProcedencia estado={valor === null ? "sin_dato" : c.contribucion?.estado ?? "estimado"} />
        <span className="text-xs text-[#4A5163]">Cota superior: faltan Full y publicidad</span>
      </div>
    </section>
  );
}

function Comparables({ d }: { d: RadarDetalle }) {
  const filas = [...(d.comparables ?? [])].sort(
    (a, b) => (a.posicion ?? Number.MAX_SAFE_INTEGER) - (b.posicion ?? Number.MAX_SAFE_INTEGER),
  );
  const ctx = d.contexto_categoria;
  // La búsqueda de ML no guarda reseñas (y a veces tampoco visitas): una columna
  // entera de "sin dato" no dice nada, así que sólo se pinta si trae algo.
  const conResenas = filas.some((r) => typeof r.reviews === "number");
  const conVisitas = filas.some((r) => typeof r.visitas_30d === "number");
  return (
    <div className="flex flex-col gap-3">
      {filas.length === 0 ? (
        <p className="py-4 text-sm text-slate-500">Sin comparables capturados para este SKU.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-[#E4E7EE] text-left text-xs font-semibold text-[#4A5163]">
                <th scope="col" className="py-2 pr-3">#</th>
                <th scope="col" className="py-2 pr-3">Vendedor</th>
                <th scope="col" className="py-2 pr-3 text-right">Precio</th>
                <th scope="col" className="py-2 pr-3 text-right">Calificación</th>
                {conResenas && <th scope="col" className="py-2 pr-3 text-right">Reseñas</th>}
                {conVisitas && <th scope="col" className="py-2 pr-3 text-right">Visitas 30 d</th>}
              </tr>
            </thead>
            <tbody>
              {filas.map((r, k) => (
                <tr key={`${r.posicion ?? "x"}-${k}`} className={`border-b border-[#EEF0F4] ${r.es_nuestro ? "bg-[#EEF0FF]/60" : ""}`}>
                  <td className="py-2 pr-3 tabular-nums text-[#4A5163]">{r.posicion ?? "—"}</td>
                  <td className="py-2 pr-3">
                    <span className="inline-flex items-center gap-1.5">
                      {r.vendedor ?? <SinDato texto="Sin nombre" />}
                      {r.es_nuestro && (
                        <span className="rounded bg-[#3730A3] px-1.5 py-px text-[10px] font-semibold text-white">Nuestra</span>
                      )}
                    </span>
                    {r.titulo && (
                      <span className="block max-w-[320px] truncate text-xs text-[#4A5163]" title={r.titulo}>
                        {r.titulo}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-right font-semibold tabular-nums"><Valor texto={pesos(r.precio)} /></td>
                  <td className="py-2 pr-3 text-right tabular-nums">
                    {typeof r.rating === "number" ? (
                      <span className="inline-flex items-center gap-1">
                        <Star size={12} className="text-[#8A5A00]" aria-hidden />
                        {decimal(r.rating)}
                      </span>
                    ) : (
                      <SinDato />
                    )}
                  </td>
                  {conResenas && <td className="py-2 pr-3 text-right tabular-nums"><Valor texto={entero(r.reviews)} /></td>}
                  {conVisitas && <td className="py-2 pr-3 text-right tabular-nums"><Valor texto={entero(r.visitas_30d)} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs leading-relaxed text-[#4A5163]">
        La mediana de referencia excluye nuestras publicaciones.
        {ctx && (ctx.mediana !== null || ctx.n !== null) && (
          <>
            {" "}Top de la categoría: mediana {pesos(ctx.mediana) ?? "sin dato"}
            {ctx.n !== null ? ` de ${entero(ctx.n)} publicaciones` : ""} — solo contexto, no decide.
          </>
        )}
      </p>
    </div>
  );
}

function Calidad({ d }: { d: RadarDetalle }) {
  const cuentas = d.cuentas ?? [];
  return (
    <div className="flex flex-col gap-3">
      {cuentas.length === 0 ? (
        <SinDato texto="Sin publicaciones" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[420px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-[#E4E7EE] text-left text-xs font-semibold text-[#4A5163]">
                <th scope="col" className="py-2 pr-3">Cuenta</th>
                <th scope="col" className="py-2 pr-3">Full</th>
                <th scope="col" className="py-2 pr-3">Experiencia de compra</th>
                <th scope="col" className="py-2">Calidad de publicación</th>
              </tr>
            </thead>
            <tbody>
              {cuentas.map((c) => (
                <tr key={c.cuenta} className="border-b border-[#EEF0F4]">
                  <td className="py-2 pr-3 font-semibold">{etiquetaCuenta(c.cuenta)}</td>
                  <td className="py-2 pr-3">
                    {c.full === null ? <SinDato /> : c.full ? (
                      <span className="inline-flex items-center gap-1 font-semibold text-[#1849A9]"><Truck size={13} aria-hidden />Sí</span>
                    ) : "No"}
                  </td>
                  <td className="py-2 pr-3"><PuntoExperiencia experiencia={c.experiencia} /></td>
                  <td className="py-2 tabular-nums">
                    {typeof c.calidad === "number" ? `${entero(c.calidad)}/100` : <SinDato />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs leading-relaxed text-[#4A5163]">
        El premio suma Full y experiencia verde, con tope.
        {d.clase === "exceso" && " En clase Exceso el premio es 0: primero se mueve el stock."}
        {" "}Reseñas, reputación y price_to_win llegan en F5.
      </p>
    </div>
  );
}
