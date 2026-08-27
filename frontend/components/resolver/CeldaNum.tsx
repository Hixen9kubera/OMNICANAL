"use client";

/**
 * CeldaNum — celda numérica editable de una tabla de resolución de costos.
 *
 * Se extrajo de `ResolverCostosModal` para que el modal SKU-primero la use tal
 * cual en vez de copiarla. Sin cambios de comportamiento.
 *
 * El texto vive en estado local y se COMMITEA en `blur` / Enter, no en cada
 * tecla: el commit dispara un viaje al servidor (el cálculo encadena piezas →
 * CBM por pieza → flete) y hacerlo por tecla sería una llamada por dígito.
 */

import { useState } from "react";

export default function CeldaNum({
  valor,
  decimales = 2,
  editado = false,
  onCambio,
}: {
  valor: number | null | undefined;
  decimales?: number;
  /** Capturado a mano: se resalta y el solucionador ya no lo sobreescribe. */
  editado?: boolean;
  onCambio: (v: number) => void;
}) {
  const [texto, setTexto] = useState<string | null>(null);
  const mostrado = texto ?? (valor == null || valor === 0 ? "" : String(valor));
  return (
    <input
      value={mostrado}
      inputMode="decimal"
      onChange={(e) => setTexto(e.target.value)}
      onBlur={() => {
        if (texto === null) return;
        const n = Number(texto);
        setTexto(null);
        if (!Number.isNaN(n) && n !== valor) onCambio(n);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
      placeholder="—"
      className={[
        "w-full rounded border px-1 py-0.5 text-right text-xs tabular-nums",
        "hover:border-slate-300 focus:border-indigo-400 focus:outline-none",
        editado
          ? "border-indigo-300 bg-indigo-50 font-semibold text-indigo-800"
          : "border-transparent bg-white/70",
      ].join(" ")}
      title={`${decimales} decimales`}
    />
  );
}
