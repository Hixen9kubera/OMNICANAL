"use client";

/**
 * Bandeja de alertas de Análisis › Conversión (la segunda vista, Eduardo
 * 15-oct-2026): tarjetas que cuentan cada alerta y filtran, y una tabla
 * ordenada por las piezas que faltan para llegar a la meta con el tráfico que
 * la publicación YA tiene — primero lo que más venta deja ir.
 *
 * Las visitas no tienen serie diaria en producción (solo el total de 30 días),
 * así que la minigráfica es de PIEZAS por día; la de visitas llegará cuando se
 * guarden las visitas diarias.
 */

import { useState } from "react";
import {
  ALERTAS, type ClaveAlerta, type Reglas, fechaCorta, num, pct, pesos, piezasPerdidas,
} from "@/lib/conversion";
import type { FilaMapa } from "./MapaBurbujas";

interface Props {
  filas: FilaMapa[];
  reglas: Reglas;
  onAbrir: (listingId: string) => void;
}

const POR_PAGINA = 60;

function Minigrafica({ xs }: { xs: number[] }) {
  const mx = Math.max(1, ...xs);
  const pts = xs.map((v, i) => `${((i * 100) / Math.max(1, xs.length - 1)).toFixed(1)},${(26 - (v / mx) * 24).toFixed(1)}`).join(" ");
  return (
    <svg aria-hidden="true" viewBox="0 0 100 28" preserveAspectRatio="none" className="h-7 w-[90px]">
      <polyline points={pts} fill="none" stroke="#0f172a" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function delta(act: number, ant: number | null | undefined) {
  if (ant == null || ant === 0) return null;
  const d = act / ant - 1;
  const color = d <= -0.25 ? "text-red-700" : d >= 0.1 ? "text-emerald-700" : "text-slate-500";
  // Arriba de +300 % el porcentaje ya no se lee («+13560 %»): se dice cuántas veces.
  if (d > 3) return { texto: `×${Math.round(act / ant)}`, color };
  return { texto: `${d >= 0 ? "+" : "−"}${Math.abs(Math.round(d * 100))} %`, color };
}

export default function BandejaAlertas({ filas, reglas, onAbrir }: Props) {
  const [filtro, setFiltro] = useState<ClaveAlerta | "todas">("todas");
  const [pagina, setPagina] = useState(1);

  const enAlerta = filas.filter((f) => f.m.alertas.length && f.p.listing_id);
  const cuenta = (k: ClaveAlerta) => enAlerta.filter((f) => f.m.alertas.some((a) => a.k === k)).length;
  const reglaTexto: Record<ClaveAlerta, string> = {
    visitas: `${num(reglas.visitasAltas)} visitas o más y conversión bajo ${pct(reglas.convBaja, 0)}.`,
    cayo: `Convierte ${Math.round(reglas.caidaConv * 100)} % menos que en la medición anterior.`,
    agota: `Conversión de ${pct(reglas.convAgota, 0)} o más y stock para ${reglas.diasAgota} días o menos.`,
    sinvis: `Activa, con stock y menos de ${reglas.minVisitas} visitas en 30 días.`,
    bajan: `${Math.round(reglas.caidaVisitas * 100)} % menos visitas que en la medición anterior.`,
  };
  const tarjetas: { k: ClaveAlerta | "todas"; titulo: string; texto: string; n: number }[] = [
    { k: "todas", titulo: "Todas las alertas", texto: "Cualquier publicación con al menos una alerta.", n: enAlerta.length },
    ...(Object.keys(ALERTAS) as ClaveAlerta[]).map((k) => ({ k, titulo: ALERTAS[k].titulo, texto: reglaTexto[k], n: cuenta(k) })),
  ];

  const lista = enAlerta
    .filter((f) => filtro === "todas" || f.m.alertas.some((a) => a.k === filtro))
    .sort((a, b) => piezasPerdidas(b.p, reglas) - piezasPerdidas(a.p, reglas));
  const visibles = lista.slice(0, pagina * POR_PAGINA);
  const escala = Math.max(0.1, reglas.convMeta * 2.5);

  return (
    <div className="flex flex-col gap-4">
      <section aria-label="Alertas" className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
        {tarjetas.map((t) => {
          const on = filtro === t.k;
          return (
            <button key={t.k} type="button" aria-pressed={on} onClick={() => { setFiltro(t.k); setPagina(1); }}
              className={`flex min-h-[96px] flex-col items-start gap-1.5 rounded-2xl p-4 text-left transition ${
                on ? "border-[1.5px] border-indigo-500 bg-indigo-50" : "border border-slate-200 bg-white hover:border-slate-300"}`}>
              <span className="flex w-full items-baseline justify-between gap-2">
                <span className="text-[13px] font-semibold text-slate-900">{t.titulo}</span>
                <span className={`text-2xl font-bold ${t.k === "visitas" || t.k === "cayo" ? "text-orange-700" : "text-slate-900"}`}>{t.n}</span>
              </span>
              <span className="text-xs leading-snug text-slate-600">{t.texto}</span>
            </button>
          );
        })}
      </section>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full min-w-[1100px] border-collapse text-[13px]">
          <thead>
            <tr className="bg-slate-50 text-left text-xs text-slate-600">
              <th scope="col" className="px-4 py-2.5 font-semibold">Publicación</th>
              <th scope="col" className="px-3 py-2.5 font-semibold">Visitas 30 d</th>
              <th scope="col" className="px-3 py-2.5 font-semibold">Piezas 30 d</th>
              <th scope="col" className="px-3 py-2.5 font-semibold">Conversión</th>
              <th scope="col" className="px-3 py-2.5 text-right font-semibold">Precio · stock</th>
              <th scope="col" className="px-4 py-2.5 font-semibold">Por qué aparece</th>
            </tr>
          </thead>
          <tbody>
            {visibles.map(({ p, m }) => {
              const dv = delta(p.visitas ?? 0, p.visitas_ant), du = delta(p.piezas, p.piezas_ant);
              const ancho = Math.min(1, (m.conv ?? 0) / escala) * 100;
              return (
                <tr key={p.listing_id} className="border-t border-slate-100 align-top">
                  <td className="px-4 py-3">
                    <button type="button" onClick={() => onAbrir(p.listing_id as string)}
                      className="text-left font-semibold text-slate-900 hover:text-indigo-700 hover:underline">
                      {p.titulo ?? p.sku}
                    </button>
                    <div className="mt-0.5 text-xs text-slate-500">{p.sku} · {p.cuenta}{p.full ? " · FULL" : ""} · {p.listing_id}</div>
                  </td>
                  <td className="px-3 py-3">
                    <div className="font-semibold text-slate-900">{num(p.visitas)}</div>
                    {dv
                      ? <div className={`mt-0.5 text-xs ${dv.color}`}>{dv.texto} contra el {fechaCorta(p.visitas_ant_at)}</div>
                      : <div className="mt-0.5 text-xs text-slate-400">sin medición anterior</div>}
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2.5">
                      <span className="min-w-[36px] font-semibold text-slate-900">{num(p.piezas)}</span>
                      {p.piezas_por_dia && <Minigrafica xs={p.piezas_por_dia} />}
                    </div>
                    {du && <div className={`mt-0.5 text-xs ${du.color}`}>{du.texto} contra la medición anterior</div>}
                  </td>
                  <td className="min-w-[150px] px-3 py-3">
                    <div className="font-semibold text-slate-900">{pct(m.conv)}</div>
                    <div className="relative mt-1.5 h-1.5 w-[120px] rounded-full bg-slate-100">
                      <div className="absolute inset-y-0 left-0 rounded-full"
                        style={{ width: `${ancho}%`, background: (m.conv ?? 0) >= reglas.convMeta ? "#4f46e5" : "#ea580c" }} />
                      <div className="absolute -bottom-[3px] -top-[3px] w-0.5 bg-slate-900"
                        style={{ left: `${Math.min(1, reglas.convMeta / escala) * 100}%` }} />
                    </div>
                    <div className="mt-1 text-xs text-slate-500">antes {pct(m.convAnt)}</div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-right">
                    <div className="font-semibold text-slate-900">{pesos(p.precio)}</div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      {num(p.stock)} pzs · {Number.isFinite(m.dias) ? `${Math.round(m.dias)} días` : "sin ventas"}
                    </div>
                  </td>
                  <td className="min-w-[240px] px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {m.alertas.map((a) => (
                        <span key={a.k} className="rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs font-semibold text-orange-900">
                          {a.texto}
                        </span>
                      ))}
                    </div>
                    <div className="mt-1.5 text-xs leading-snug text-slate-600">{ALERTAS[m.alertas[0].k].revisar}</div>
                  </td>
                </tr>
              );
            })}
            {!visibles.length && (
              <tr><td colSpan={6} className="px-4 py-10 text-center text-sm text-slate-500">Ninguna publicación con esta alerta.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {lista.length > visibles.length && (
        <button type="button" onClick={() => setPagina((n) => n + 1)}
          className="self-center rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-indigo-700 hover:bg-slate-50">
          Ver {Math.min(POR_PAGINA, lista.length - visibles.length)} más de {num(lista.length - visibles.length)}
        </button>
      )}
    </div>
  );
}
