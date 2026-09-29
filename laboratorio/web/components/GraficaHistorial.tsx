"use client";

/**
 * Historia de precio de una publicación (DISENO §6 `historial.json`): 150 días
 * reconstruidos — precio realizado (ingreso ÷ unidades de las ventas), precio
 * ofrecido (historial limpio) — más un punto por cada snapshot del laboratorio
 * con el precio recomendado de ese día.
 *
 * Precio, unidades y visitas son magnitudes distintas: van en paneles apilados
 * con el mismo eje de fechas, nunca en un doble eje.
 */
import { PanelesX, Leyenda, TablaGemela } from "./graficas";
import type { PanelX } from "./graficas";
import { SERIE } from "@/lib/tema";
import { cifra, dia, entero, pesos } from "@/lib/formato";
import type { PuntoHistorial } from "@/lib/tipos";

const ejePesos = (v: number) => { const a = Math.abs(v); const t = a >= 1000 ? `$${(a / 1000).toFixed(a % 1000 ? 1 : 0)}k` : `$${Math.round(a)}`; return v < 0 ? `−${t}` : t; };
const ejeNum = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : `${Math.round(v * 10) / 10}`);

export default function GraficaHistorial({ serie, compacta = false }: { serie: PuntoHistorial[]; compacta?: boolean }) {
  if (!serie.length) {
    return <p className="rounded-lg border border-dashed border-slate-300 px-3 py-6 text-center text-xs text-slate-500">Sin historia para esta publicación.</p>;
  }
  const xs = serie.map((_, i) => i);
  const hayRec = serie.some((p) => p.precio_recomendado !== null);
  const paneles: PanelX[] = [
    {
      titulo: "Precio (MXN con IVA)", formatoEje: ejePesos, alto: compacta ? 110 : 150, incluirCero: false,
      series: [
        { nombre: "Realizado", color: SERIE.principal, valores: serie.map((p) => p.precio_realizado), formato: (v) => pesos(v) },
        { nombre: "Ofrecido", color: SERIE.secundaria, valores: serie.map((p) => p.precio_ofrecido), formato: (v) => pesos(v), guion: true },
        ...(hayRec ? [{ nombre: "Recomendado", color: SERIE.recomendado, valores: serie.map((p) => p.precio_recomendado), formato: (v: number) => pesos(v), puntos: true }] : []),
      ],
    },
    {
      titulo: "Unidades por día", formatoEje: ejeNum, alto: compacta ? 64 : 80,
      series: [{ nombre: "Unidades", color: SERIE.principal, valores: serie.map((p) => p.unidades), formato: (v) => entero(v), area: true }],
    },
  ];
  if (!compacta) {
    paneles.push({
      titulo: "Visitas por día", formatoEje: ejeNum, alto: 80,
      series: [{ nombre: "Visitas", color: SERIE.principal, valores: serie.map((p) => p.visitas), formato: (v) => entero(v), area: true }],
    });
  }
  return (
    <div>
      <Leyenda items={[
        { nombre: "Precio realizado", color: SERIE.principal },
        { nombre: "Precio ofrecido", color: SERIE.secundaria, tipo: "guion" },
        ...(hayRec ? [{ nombre: "Recomendado (snapshots)", color: SERIE.recomendado }] : []),
      ]} />
      <div className="mt-2">
        <PanelesX xs={xs} paneles={paneles} xEsIndice
                  formatoX={(i) => dia(serie[Math.round(i)]?.fecha)}
                  formatoXLargo={(i) => serie[Math.round(i)]?.fecha ?? ""} />
      </div>
      <TablaGemela columnas={["Fecha", "Realizado", "Ofrecido", "Recomendado", "Unidades", "Visitas"]}
                   filas={[...serie].reverse().map((p) => [p.fecha, pesos(p.precio_realizado), pesos(p.precio_ofrecido),
                     pesos(p.precio_recomendado), entero(p.unidades), cifra(p.visitas)])} />
    </div>
  );
}
