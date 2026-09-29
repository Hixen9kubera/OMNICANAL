"use client";

// TablaRadar.tsx — Las tarjetas de dirección y la tabla de SKUs del radar.
// Todo lo que se pinta llega calculado del backend; aquí sólo se rotula.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronRight, Star, Truck } from "lucide-react";

import type { RadarConteos, RadarCuenta, RadarDireccion, RadarItem, RadarParametros } from "@/lib/api";
import {
  CUENTAS_ML,
  DIRECCIONES,
  META_DIRECCION,
  cambioRelativo,
  cortaCuenta,
  decimal,
  definicionDireccion,
  entero,
  etiquetaClase,
  etiquetaContenedor,
  tituloContenedor,
  motivoSinReferencia,
  num,
  pesos,
  porcentaje,
  textoCobertura,
} from "./formato";
import {
  ChipDireccion,
  EtiquetaProcedencia,
  ICONO_DIRECCION,
  PuntoExperiencia,
  SinDato,
  Valor,
} from "./ui";

// ── Tarjetas ────────────────────────────────────────────────────────────────

export function TarjetasDireccion({
  conteos,
  activa,
  parametros,
  onElegir,
}: {
  conteos: Partial<RadarConteos> | null | undefined;
  activa: RadarDireccion | null;
  parametros: RadarParametros | null | undefined;
  onElegir: (d: RadarDireccion | null) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      {DIRECCIONES.map((d) => {
        const meta = META_DIRECCION[d];
        const Icono = ICONO_DIRECCION[d];
        const on = activa === d;
        const n = conteos?.[d];
        return (
          <button
            key={d}
            type="button"
            aria-pressed={on}
            onClick={() => onElegir(on ? null : d)}
            className={[
              "flex min-h-[112px] flex-col gap-2 rounded-xl border-2 px-4 py-3.5 text-left transition-colors",
              on ? meta.tarjetaActiva : "border-[#E4E7EE] bg-white hover:border-slate-300",
            ].join(" ")}
          >
            <span className="flex items-center gap-2 text-[13px] font-semibold" style={{ color: meta.color }}>
              <span className={`flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-lg ${meta.icono}`}>
                <Icono size={15} strokeWidth={2.2} aria-hidden />
              </span>
              {meta.etiqueta}
            </span>
            <span className="text-[26px] font-bold tabular-nums text-slate-900">
              {typeof n === "number" ? entero(n) : <SinDato />}
            </span>
            <span className="text-xs leading-snug text-[#4A5163]">{definicionDireccion(d, parametros)}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── Tabla ───────────────────────────────────────────────────────────────────

/** Las cuentas conocidas primero (BEKURA, SANCOR) y luego cualquier otra. */
function cuentasOrdenadas(item: RadarItem): { cuenta: string; fila: RadarCuenta | null }[] {
  const filas = item.cuentas ?? [];
  const conocidas = CUENTAS_ML.map((c) => ({
    cuenta: c.valor,
    fila: filas.find((f) => f.cuenta?.toUpperCase() === c.valor) ?? null,
  }));
  const otras = filas
    .filter((f) => !CUENTAS_ML.some((c) => c.valor === f.cuenta?.toUpperCase()))
    .map((f) => ({ cuenta: f.cuenta, fila: f }));
  return [...conocidas, ...otras];
}

export function cuentaPrincipal(item: RadarItem): RadarCuenta | null {
  const filas = item.cuentas ?? [];
  return filas.find((f) => f.cuenta === item.cuenta_principal) ?? filas[0] ?? null;
}

function precioDe(f: RadarCuenta | null | undefined): number | null {
  if (!f) return null;
  return f.precio_cobrado ?? f.precio ?? null;
}

/** "A $1,169 (−10 %)" para subir/bajar; si no, la primera razón. */
export function textoSugerido(item: RadarItem): string | null {
  const p = precioDe(cuentaPrincipal(item));
  if ((item.direccion === "subir" || item.direccion === "bajar") && item.precio_sugerido !== null) {
    const cambio = cambioRelativo(p, item.precio_sugerido);
    return `A ${pesos(item.precio_sugerido)}${cambio ? ` (${cambio})` : ""}`;
  }
  return item.razones?.[0] ?? null;
}

function Posicion({ pct, banda }: { pct: number | null; banda: number }) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return <SinDato />;
  const color = pct > banda ? "#B4530A" : pct < -banda ? "#1849A9" : "#667085";
  const ancho = Math.min(Math.abs(pct), 30) * (40 / 30); // la mitad de la barra = 30 %
  const texto = pct > 0 ? `${porcentaje(pct, true)} arriba` : pct < 0 ? `${porcentaje(Math.abs(pct))} abajo` : "En la mediana";
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[13px] font-semibold tabular-nums" style={{ color }}>{texto}</span>
      <span className="relative block h-1.5 w-[84px] rounded-full bg-[#EEF0F4]" aria-hidden>
        <span className="absolute -top-[3px] left-[41px] h-3 w-0.5 bg-[#98A2B3]" />
        <span
          className="absolute top-0 h-1.5 rounded-full"
          style={{ left: pct >= 0 ? 42 : 42 - ancho, width: ancho, background: color }}
        />
      </span>
    </div>
  );
}

function Cobertura({ dias, ventasDia }: { dias: number | null; ventasDia: number | null }) {
  const txtDias = textoCobertura(dias, ventasDia);
  let txtVentas: string | null = null;
  if (ventasDia !== null && ventasDia !== undefined && Number.isFinite(ventasDia)) {
    txtVentas = ventasDia === 0 ? "Sin venta en 90 días" : `${decimal(ventasDia)} por día`;
  }
  return (
    <div className="flex flex-col gap-0.5">
      <Valor texto={txtDias} className="text-[13px] font-semibold tabular-nums" />
      {txtVentas !== null ? <span className="text-xs text-[#4A5163]">{txtVentas}</span> : <SinDato />}
    </div>
  );
}

function fuenteReferencia(item: RadarItem, nMin: number | null): string {
  const r = item.referencia;
  if (!r || r.precio === null) return motivoSinReferencia(r?.motivo, nMin);
  const n = typeof r.n === "number" ? ` · ${entero(r.n)} rivales` : "";
  if (r.fuente === "busqueda") return `Búsqueda${n}`;
  if (r.fuente === "categoria") return `Categoría${n}`;
  return `Mercado${n}`;
}

export function TablaRadar({
  items,
  parametros,
}: {
  items: RadarItem[];
  parametros: RadarParametros | null | undefined;
}) {
  const router = useRouter();
  const banda = typeof parametros?.banda_mantener === "number" ? parametros.banda_mantener * 100 : 5;
  const nMin = num(parametros?.n_min_referencia);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1320px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-[#E4E7EE] bg-[#F9FAFC] text-left text-xs font-semibold text-[#4A5163]">
            <th scope="col" className="px-3 py-2.5 pl-5">SKU</th>
            <th scope="col" className="px-3 py-2.5">Clase</th>
            <th scope="col" className="px-3 py-2.5">Precio actual</th>
            <th scope="col" className="px-3 py-2.5">Mercado</th>
            <th scope="col" className="px-3 py-2.5">Posición</th>
            <th scope="col" className="px-3 py-2.5">Contribución</th>
            <th scope="col" className="px-3 py-2.5">Piso</th>
            <th scope="col" className="px-3 py-2.5">Cobertura</th>
            <th scope="col" className="px-3 py-2.5">Calidad</th>
            <th scope="col" className="px-3 py-2.5">Dirección</th>
            <th scope="col" className="px-2 py-2.5"><span className="sr-only">Detalle</span></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const href = `/radar/${encodeURIComponent(item.sku)}`;
            const principal = cuentaPrincipal(item);
            const hayFull = (item.cuentas ?? []).some((c) => c.full === true);
            const cont = etiquetaContenedor(item.contenedor);
            const sugerido = textoSugerido(item);
            return (
              <tr
                key={item.sku}
                onClick={(e) => {
                  // Los enlaces de la fila navegan solos (y respetan ctrl+clic).
                  if ((e.target as HTMLElement).closest("a")) return;
                  router.push(href);
                }}
                className="cursor-pointer border-b border-[#EEF0F4] align-middle hover:bg-slate-50"
              >
                <td className="max-w-[280px] px-3 py-2.5 pl-5">
                  <div className="flex items-center gap-2">
                    <Link href={href} className="font-semibold tabular-nums text-slate-900 hover:text-indigo-700">
                      {item.sku}
                    </Link>
                    {cont && (
                      <span
                        title={tituloContenedor(item)}
                        className="shrink-0 rounded-md border border-[#D0D5DD] px-1.5 py-px text-[11px] font-semibold text-[#4A5163]"
                      >
                        {cont}{item.contenedor_multi ? " +" : ""}
                      </span>
                    )}
                  </div>
                  <div className="truncate text-[13px] text-[#4A5163]" title={item.titulo ?? undefined}>
                    {item.titulo ?? <SinDato texto="Sin título" />}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-[13px]">
                  <Valor texto={etiquetaClase(item.clase)} />
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex flex-col gap-0.5 tabular-nums">
                    {cuentasOrdenadas(item).map(({ cuenta, fila }) => {
                      const p = precioDe(fila);
                      const esPrincipal = fila !== null && fila.cuenta === item.cuenta_principal;
                      const distinto = fila && fila.precio !== null && fila.precio_cobrado !== null
                        && fila.precio !== fila.precio_cobrado;
                      return (
                        <span
                          key={cuenta}
                          className={`text-[13px] ${esPrincipal ? "font-semibold text-slate-900" : ""}`}
                          title={distinto ? `Publicado ${pesos(fila.precio)} · cobrado ${pesos(fila.precio_cobrado)}` : undefined}
                        >
                          <span className="text-[11px] font-normal text-[#4A5163]">{cortaCuenta(cuenta)} </span>
                          {fila === null ? <span className="text-slate-400">—</span> : <Valor texto={pesos(p)} />}
                        </span>
                      );
                    })}
                  </div>
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex flex-col gap-0.5">
                    <Valor texto={pesos(item.referencia?.precio)} className="font-semibold tabular-nums" />
                    <span className="text-xs text-[#4A5163]">{fuenteReferencia(item, nMin)}</span>
                  </div>
                </td>
                <td className="px-3 py-2.5">
                  <Posicion pct={item.posicion_pct} banda={banda} />
                </td>
                <td className="px-3 py-2.5">
                  {item.contribucion === null ? (
                    <SinDato />
                  ) : (
                    <div className="flex flex-col items-start gap-1">
                      <span className={`font-semibold tabular-nums ${item.contribucion < 0 ? "text-rose-700" : ""}`}>
                        {pesos(item.contribucion)}
                      </span>
                      <EtiquetaProcedencia estado={item.contribucion_estado ?? "estimado"} />
                    </div>
                  )}
                </td>
                <td className="px-3 py-2.5 text-[13px] tabular-nums">
                  <Valor texto={pesos(item.piso)} />
                </td>
                <td className="px-3 py-2.5">
                  <Cobertura dias={item.cobertura_dias} ventasDia={item.ventas_dia} />
                </td>
                <td className="px-3 py-2.5 text-xs">
                  <div className="flex flex-col gap-1 text-[#4A5163]">
                    {hayFull && (
                      <span className="inline-flex items-center gap-1 font-semibold text-[#1849A9]">
                        <Truck size={13} aria-hidden />
                        Full
                      </span>
                    )}
                    <PuntoExperiencia experiencia={principal?.experiencia ?? null} />
                    {typeof principal?.calidad === "number" ? (
                      <span className="inline-flex items-center gap-1">
                        <Star size={11} className="text-[#8A5A00]" aria-hidden />
                        {entero(principal.calidad)}/100
                      </span>
                    ) : (
                      <SinDato texto="Calidad sin dato" />
                    )}
                  </div>
                </td>
                <td className="max-w-[200px] px-3 py-2.5">
                  <div className="flex flex-col items-start gap-1">
                    <ChipDireccion direccion={item.direccion} />
                    {sugerido && (
                      <span className="line-clamp-2 text-xs leading-snug text-[#4A5163]" title={sugerido}>
                        {sugerido}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-2 py-2.5">
                  <Link
                    href={href}
                    aria-label={`Ver detalle de ${item.sku}`}
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-[#4A5163] hover:bg-slate-100"
                  >
                    <ChevronRight size={16} aria-hidden />
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
