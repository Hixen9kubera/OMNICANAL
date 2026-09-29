"use client";

/**
 * La curva de una publicación (`/curva/{id}`, DISENO §5): qué pasa con visitas,
 * unidades, conversión y utilidad por día si el precio se mueve en la rejilla de
 * 0.55×P0 a 1.45×P0.
 *
 * Cuatro paneles apilados con el mismo eje de PRECIO y un solo cursor: cada
 * magnitud con su propio eje Y (nada de doble eje). Detrás, dos zonas: donde la
 * publicación pierde dinero (bajo el equilibrio) y donde gana menos que el piso
 * de margen. Encima, los marcadores: A actual · R recomendado · C competencia; el
 * máximo de utilidad es el punto destacado del primer panel.
 */
import { PanelesX, Leyenda, TablaGemela } from "./graficas";
import type { MarcadorX, ZonaX } from "./graficas";
import { SERIE } from "@/lib/tema";
import { cifra, pct, pesos } from "@/lib/formato";
import type { Curva } from "@/lib/tipos";

const ejePesos = (v: number) => { const a = Math.abs(v); const t = a >= 1000 ? `$${(a / 1000).toFixed(a % 1000 ? 1 : 0)}k` : `$${Math.round(a)}`; return v < 0 ? `−${t}` : t; };
const ejeNum = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : `${Math.round(v * 100) / 100}`);

export default function CurvaPrecio({ curva }: { curva: Curva }) {
  const puntos = [...curva.puntos].sort((a, b) => a.precio - b.precio);
  if (puntos.length < 2) return <p className="text-sm text-slate-500">Sin curva para esta publicación.</p>;
  const xs = puntos.map((p) => p.precio);
  const m = curva.marcadores ?? {};
  const xMin = xs[0];

  const marcadores: MarcadorX[] = [];
  if (m.actual != null) marcadores.push({ x: m.actual, corto: "A", etiqueta: `Actual ${pesos(m.actual)}`, color: SERIE.tinta, grosor: 1.5 });
  if (m.recomendado != null) marcadores.push({ x: m.recomendado, corto: "R", etiqueta: `Recomendado ${pesos(m.recomendado)}`, color: SERIE.recomendado, grosor: 2 });
  if (m.ref_competencia != null) marcadores.push({ x: m.ref_competencia, corto: "C", etiqueta: `Competencia ${pesos(m.ref_competencia)}`, color: SERIE.referencia, estilo: "guion" });

  const zonas: ZonaX[] = [];
  if (m.equilibrio != null) zonas.push({ desde: xMin, hasta: m.equilibrio, color: SERIE.perdida, etiqueta: "Pierde dinero" });
  if (m.equilibrio != null && m.piso != null) zonas.push({ desde: m.equilibrio, hasta: m.piso, color: SERIE.delgada, etiqueta: "Bajo el piso de margen" });
  if (m.equilibrio == null && m.piso != null) zonas.push({ desde: xMin, hasta: m.piso, color: SERIE.delgada, etiqueta: "Bajo el piso de margen" });

  let iMax = -1;
  if (m.max_utilidad != null) {
    iMax = xs.reduce((best, x, i) => (Math.abs(x - m.max_utilidad!) < Math.abs(xs[best] - m.max_utilidad!) ? i : best), 0);
  }
  const hayUtil = puntos.some((p) => p.utilidad_dia !== null);

  // Precios EXACTOS que caen fuera de la rejilla (0.55·P0 – 1.45·P0): no se dibujan,
  // se dicen. 1,216 equilibrios y 1,078 pisos quedan abajo el 28-sep.
  const xMax = xs[xs.length - 1];
  const NOMBRE: Record<string, string> = { equilibrio: "Equilibrio", piso: "Piso", recomendado: "Recomendado", actual: "Actual", ref_competencia: "Competencia", max_utilidad: "Máx. utilidad" };
  const fuera = (Object.keys(NOMBRE) as (keyof typeof m)[])
    .filter((k) => m[k] != null && ((m[k] as number) < xMin - 0.005 || (m[k] as number) > xMax + 0.005))
    .map((k) => ({ nombre: NOMBRE[k], valor: m[k] as number, abajo: (m[k] as number) < xMin }));

  return (
    <div>
      <Leyenda items={[
        { nombre: "Actual", color: SERIE.tinta, valor: pesos(m.actual ?? null) },
        { nombre: "Recomendado", color: SERIE.recomendado, valor: pesos(m.recomendado ?? null) },
        { nombre: "Competencia", color: SERIE.referencia, tipo: "guion", valor: pesos(m.ref_competencia ?? null) },
        { nombre: "Pierde dinero", color: SERIE.perdida, tipo: "zona", valor: m.equilibrio != null ? `< ${pesos(m.equilibrio)}` : "—" },
        { nombre: "Bajo el piso", color: SERIE.delgada, tipo: "zona", valor: m.piso != null ? `< ${pesos(m.piso)}` : "—" },
      ]} />
      {fuera.length > 0 && (
        <p className="mt-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11.5px] leading-snug text-slate-600">
          Fuera de la gráfica (la curva va de {pesos(xMin)} a {pesos(xMax)}):{" "}
          {fuera.map((f, i) => (
            <span key={f.nombre}>{i > 0 ? " · " : ""}<b className="font-semibold text-slate-800">{f.nombre} {pesos(f.valor)}</b> {f.abajo ? "↓ abajo" : "↑ arriba"}</span>
          ))}. Son exactos; la curva de demanda sólo se estima dentro de ese rango.
        </p>
      )}
      <div className="mt-3">
        <PanelesX
          xs={xs}
          formatoX={(x) => `$${Math.round(x)}`}
          formatoXLargo={(x) => `A ${pesos(x)}`}
          marcadores={marcadores}
          zonas={zonas}
          paneles={[
            ...(hayUtil ? [{
              titulo: "Utilidad por día", formatoEje: ejePesos, alto: 120,
              destacar: iMax >= 0 ? { indice: iMax, etiqueta: `máx ${pesos(xs[iMax])}` } : null,
              series: [{ nombre: "Utilidad/día", color: SERIE.principal, valores: puntos.map((p) => p.utilidad_dia), formato: (v: number) => pesos(v), area: true }],
            }] : []),
            { titulo: "Unidades por día", formatoEje: ejeNum, alto: 80,
              series: [{ nombre: "Unidades/día", color: SERIE.principal, valores: puntos.map((p) => p.unidades_dia), formato: (v: number) => cifra(v) }] },
            { titulo: "Visitas por día", formatoEje: ejeNum, alto: 80,
              series: [{ nombre: "Visitas/día", color: SERIE.principal, valores: puntos.map((p) => p.visitas_dia), formato: (v: number) => cifra(v) }] },
            { titulo: "Conversión", formatoEje: (v) => `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%`, alto: 70,
              series: [{ nombre: "Conversión", color: SERIE.principal, valores: puntos.map((p) => p.conversion), formato: (v: number) => pct(v, 2) }] },
          ]}
        />
      </div>
      <TablaGemela columnas={["Precio", "Visitas/día", "Conversión", "Unidades/día", "Utilidad/u", "Utilidad/día", "Margen"]}
                   filas={puntos.map((p) => [pesos(p.precio), cifra(p.visitas_dia), pct(p.conversion, 2), cifra(p.unidades_dia),
                     pesos(p.utilidad_unit), pesos(p.utilidad_dia), pct(p.margen_pct)])} />
    </div>
  );
}
