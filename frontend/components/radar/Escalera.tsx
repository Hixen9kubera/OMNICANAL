"use client";

// Escalera.tsx — Dónde queda cada precio frente al mismo producto en ML.
//
// Una sola escala horizontal con: piso, referencia de mercado, techo con
// premio, precio sugerido, el precio cobrado de cada cuenta (rombos) y los
// rivales (puntos). Los valores llegan del backend; aquí sólo se ubican.
//
// Las etiquetas de cada renglón (arriba: referencia y cuentas; abajo: piso,
// techo y sugerido) se reparten para no encimarse: si dos chocan se agrupan y
// el bloque se centra sobre sus marcas, con una línea guía hasta cada una.

import type { RadarDetalle } from "@/lib/api";
import { META_DIRECCION, cambioRelativo, etiquetaCuenta, pesos, porcentaje } from "./formato";

const ANCHO = 1000;
const MARGEN = 44;
const PISTA_Y = 84;
const ALTO = 172;

interface Etiqueta {
  x: number; // posición del valor en la escala
  nombre: string;
  valor: string;
  color: string;
}

/** Ancho aproximado del texto a 12 px (Inter ronda 0.55 em por carácter). */
function anchoTexto(e: Etiqueta): number {
  return Math.max(e.nombre.length, e.valor.length) * 6.7 + 10;
}

/**
 * Reparte etiquetas en una fila sin que se encimen. Agrupa las que chocan y
 * centra cada grupo sobre la media de sus marcas (mínimos cuadrados), dentro
 * de los márgenes. Devuelve el centro de cada etiqueta, en el orden recibido.
 */
function repartir(etiquetas: Etiqueta[], separacion = 10): number[] {
  const orden = etiquetas.map((e, i) => ({ i, x: e.x, w: anchoTexto(e) })).sort((a, b) => a.x - b.x);
  type Grupo = { miembros: typeof orden; inicio: number; ancho: number };
  const colocar = (miembros: typeof orden): Grupo => {
    const ancho = miembros.reduce((s, m) => s + m.w, 0) + separacion * (miembros.length - 1);
    // desplazamiento de cada centro dentro del bloque
    let acc = 0;
    const offs = miembros.map((m) => {
      const o = acc + m.w / 2;
      acc += m.w + separacion;
      return o;
    });
    let inicio = miembros.reduce((s, m, k) => s + (m.x - offs[k]), 0) / miembros.length;
    inicio = Math.max(4, Math.min(ANCHO - 4 - ancho, inicio));
    return { miembros, inicio, ancho };
  };
  let grupos: Grupo[] = orden.map((m) => colocar([m]));
  let cambio = true;
  while (cambio) {
    cambio = false;
    for (let k = 0; k < grupos.length - 1; k++) {
      const a = grupos[k];
      const b = grupos[k + 1];
      if (a.inicio + a.ancho + separacion > b.inicio) {
        grupos = [...grupos.slice(0, k), colocar([...a.miembros, ...b.miembros]), ...grupos.slice(k + 2)];
        cambio = true;
        break;
      }
    }
  }
  const centros = new Array<number>(etiquetas.length);
  for (const g of grupos) {
    let acc = g.inicio;
    for (const m of g.miembros) {
      centros[m.i] = acc + m.w / 2;
      acc += m.w + separacion;
    }
  }
  return centros;
}

/** Paso "bonito" para ~6 marcas del eje. */
function pasoEje(rango: number): number {
  const bruto = rango / 6;
  const pot = Math.pow(10, Math.floor(Math.log10(bruto)));
  for (const m of [1, 2, 2.5, 5, 10]) if (bruto <= m * pot) return m * pot;
  return 10 * pot;
}

export default function Escalera({ item }: { item: RadarDetalle }) {
  const meta = META_DIRECCION[item.direccion] ?? META_DIRECCION.sin_referencia;
  const ref = item.referencia?.precio ?? null;
  const cuentas = (item.cuentas ?? [])
    .map((c) => ({ cuenta: c.cuenta, precio: c.precio_cobrado ?? c.precio ?? null }))
    .filter((c): c is { cuenta: string; precio: number } => typeof c.precio === "number");
  const rivales = (item.comparables ?? [])
    .filter((r) => !r.es_nuestro && typeof r.precio === "number")
    .map((r) => r.precio as number);

  const valores = [item.piso, ref, item.techo, item.precio_sugerido, ...cuentas.map((c) => c.precio), ...rivales]
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (valores.length === 0) {
    return <p className="py-6 text-sm text-slate-500">Sin precios que ubicar en la escala.</p>;
  }

  let lo = Math.min(...valores);
  let hi = Math.max(...valores);
  const holgura = Math.max((hi - lo) * 0.08, hi * 0.03, 1);
  lo = Math.max(0, lo - holgura);
  hi = hi + holgura;
  const paso = pasoEje(hi - lo);
  lo = Math.floor(lo / paso) * paso;
  hi = Math.ceil(hi / paso) * paso;
  const x = (v: number) => MARGEN + ((v - lo) / (hi - lo)) * (ANCHO - 2 * MARGEN);
  const marcas: number[] = [];
  for (let v = lo; v <= hi + paso / 2; v += paso) marcas.push(v);

  const pPrincipal = cuentas.find((c) => c.cuenta === item.cuenta_principal)?.precio ?? cuentas[0]?.precio ?? null;

  // Renglón de arriba: referencia y cuentas. Abajo: piso, techo y sugerido.
  const arriba: Etiqueta[] = [];
  if (ref !== null) {
    arriba.push({ x: x(ref), nombre: "Referencia de mercado", valor: `${pesos(ref)} · mediana`, color: "#3730A3" });
  }
  for (const c of cuentas) {
    arriba.push({ x: x(c.precio), nombre: etiquetaCuenta(c.cuenta), valor: pesos(c.precio) ?? "", color: "#1F2430" });
  }
  const abajo: Etiqueta[] = [];
  if (item.piso !== null) {
    abajo.push({ x: x(item.piso), nombre: "Piso", valor: pesos(item.piso) ?? "", color: "#1F2430" });
  }
  if (item.techo !== null) {
    const premio = porcentaje(item.premio_calidad_pct, true);
    abajo.push({
      x: x(item.techo),
      nombre: "Techo con premio",
      valor: `${pesos(item.techo)}${premio ? ` · ${premio}` : ""}`,
      color: "#4A5163",
    });
  }
  if (item.precio_sugerido !== null) {
    const cambio = cambioRelativo(pPrincipal, item.precio_sugerido);
    abajo.push({
      x: x(item.precio_sugerido),
      nombre: "Sugerido",
      valor: `${pesos(item.precio_sugerido)}${cambio ? ` · ${cambio}` : ""}`,
      color: meta.color,
    });
  }
  const cArriba = repartir(arriba);
  const cAbajo = repartir(abajo);

  const descripcion = [
    `Escala de ${pesos(lo)} a ${pesos(hi)}.`,
    item.piso !== null ? `Piso ${pesos(item.piso)}.` : "Piso sin dato.",
    ref !== null ? `Referencia de mercado ${pesos(ref)}.` : "Sin referencia de mercado.",
    item.techo !== null ? `Techo con premio ${pesos(item.techo)}.` : "",
    item.precio_sugerido !== null ? `Sugerido ${pesos(item.precio_sugerido)}.` : "",
    ...cuentas.map((c) => `${etiquetaCuenta(c.cuenta)} ${pesos(c.precio)}.`),
    rivales.length ? `${rivales.length} rivales entre ${pesos(Math.min(...rivales))} y ${pesos(Math.max(...rivales))}.` : "Sin rivales.",
  ].filter(Boolean).join(" ");

  const banda = item.piso !== null && item.techo !== null && item.techo > item.piso;

  return (
    <div>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${ANCHO} ${ALTO}`}
          className="h-auto w-full min-w-[680px]"
          role="img"
          aria-label={descripcion}
        >
          {/* pista y banda piso→techo */}
          <rect x={MARGEN} y={PISTA_Y - 4} width={ANCHO - 2 * MARGEN} height={8} rx={4} fill="#EEF0F4" />
          {banda && (
            <rect
              x={x(item.piso as number)}
              y={PISTA_Y - 7}
              width={Math.max(2, x(item.techo as number) - x(item.piso as number))}
              height={14}
              rx={7}
              fill="#E0E3FB"
            />
          )}
          {/* eje */}
          <g fontSize={11} fill="#667085" textAnchor="middle">
            {marcas.map((v) => (
              <g key={v}>
                <path d={`M${x(v)} ${PISTA_Y + 8}v5`} stroke="#98A2B3" strokeWidth={1} />
                <text x={x(v)} y={PISTA_Y + 26}>{pesos(v)}</text>
              </g>
            ))}
          </g>

          {/* marcas verticales en la pista */}
          {item.piso !== null && (
            <path d={`M${x(item.piso)} ${PISTA_Y - 14}V${PISTA_Y + 14}`} stroke="#1F2430" strokeWidth={2} />
          )}
          {ref !== null && <path d={`M${x(ref)} ${PISTA_Y - 14}V${PISTA_Y + 14}`} stroke="#4F46E5" strokeWidth={2} />}
          {item.techo !== null && (
            <path
              d={`M${x(item.techo)} ${PISTA_Y - 14}V${PISTA_Y + 14}`}
              stroke="#4A5163"
              strokeWidth={1.5}
              strokeDasharray="4 3"
            />
          )}
          {item.precio_sugerido !== null && (
            <path d={`M${x(item.precio_sugerido)} ${PISTA_Y - 14}V${PISTA_Y + 14}`} stroke={meta.color} strokeWidth={2.5} />
          )}

          {/* rivales */}
          <g strokeWidth={1.5}>
            {rivales.map((v, k) => (
              <circle key={k} cx={x(v)} cy={PISTA_Y} r={5.5} fill="#475467" stroke="#FFFFFF" opacity={0.85} />
            ))}
          </g>

          {/* nuestras cuentas (rombos) */}
          {cuentas.map((c) => (
            <path
              key={c.cuenta}
              d={`M${x(c.precio)} ${PISTA_Y - 9}l9 9-9 9-9-9z`}
              fill="#1F2430"
              stroke="#FFFFFF"
              strokeWidth={1.5}
            />
          ))}

          {/* etiquetas de arriba + guías */}
          {arriba.map((e, k) => (
            <g key={`a${k}`}>
              <path d={`M${cArriba[k]} 36L${e.x} ${PISTA_Y - 15}`} stroke={e.color} strokeWidth={1} opacity={0.5} fill="none" />
              <text x={cArriba[k]} y={15} textAnchor="middle" fontSize={12} fontWeight={600} fill={e.color}>{e.nombre}</text>
              <text x={cArriba[k]} y={30} textAnchor="middle" fontSize={12} fill="#4A5163">{e.valor}</text>
            </g>
          ))}
          {/* etiquetas de abajo + guías */}
          {abajo.map((e, k) => (
            <g key={`b${k}`}>
              <path d={`M${e.x} ${PISTA_Y + 15}L${cAbajo[k]} ${PISTA_Y + 44}`} stroke={e.color} strokeWidth={1} opacity={0.5} fill="none" />
              <text x={cAbajo[k]} y={PISTA_Y + 60} textAnchor="middle" fontSize={12} fontWeight={600} fill={e.color}>{e.nombre}</text>
              <text x={cAbajo[k]} y={PISTA_Y + 75} textAnchor="middle" fontSize={12} fill="#4A5163">{e.valor}</text>
            </g>
          ))}
        </svg>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-[#4A5163]">
        <Leyenda icono={<circle cx={7} cy={7} r={5} fill="#475467" />}>Rival</Leyenda>
        <Leyenda icono={<path d="M7 1l6 6-6 6-6-6z" fill="#1F2430" />}>Nuestras cuentas (precio cobrado)</Leyenda>
        <Leyenda icono={<rect x={0} y={3} width={14} height={8} rx={4} fill="#E0E3FB" />}>Entre piso y techo</Leyenda>
        <Leyenda icono={<path d="M7 0V14" stroke="#1F2430" strokeWidth={2} />}>Piso</Leyenda>
        <Leyenda icono={<path d="M7 0V14" stroke="#4F46E5" strokeWidth={2} />}>Referencia de mercado</Leyenda>
        <Leyenda icono={<path d="M7 0V14" stroke="#4A5163" strokeWidth={1.5} strokeDasharray="4 3" />}>
          Techo con premio (decisión)
        </Leyenda>
        <Leyenda icono={<path d="M7 0V14" stroke={meta.color} strokeWidth={2.5} />}>Sugerido</Leyenda>
      </div>
    </div>
  );
}

function Leyenda({ icono, children }: { icono: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width={14} height={14} viewBox="0 0 14 14" aria-hidden>{icono}</svg>
      {children}
    </span>
  );
}
