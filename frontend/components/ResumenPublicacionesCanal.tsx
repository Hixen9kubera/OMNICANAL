"use client";

/**
 * ResumenPublicacionesCanal — una línea arriba de la lista con el censo del
 * canal que se está mirando: cuántas publicaciones hay, cuántas están ACTIVAS
 * con el criterio de ESE canal, y sobre cuántas se puede calcular margen.
 *
 * POR QUÉ EXISTE. "Publicado" y "activo" no son lo mismo, y la lista de abajo
 * cuenta lo primero: su filtro "Solo publicados" da por publicada una
 * publicación PAUSADA de Mercado Libre (`listing_id` presente y no `closed`) y
 * una DISCOVERABLE de Amazon, que se ve en el catálogo y no se puede comprar.
 * El criterio de verdad —canal por canal— vive en
 * `services/publicaciones_panel.py` y es el que cuenta este renglón.
 *
 * MIENTRAS EL FILTRO NO EXISTA, EL NÚMERO SÍ. `GET /api/productos` todavía no
 * sabe filtrar por "activa del canal" (handoff abierto a omni-backend,
 * 24-ago), así que aquí no se filtra nada: se dice cuántas son, para que la
 * diferencia con el total de la lista se vea en vez de suponerse.
 *
 * NO SE CALCULA NADA: los tres números y la nota llegan de
 * `GET /api/publicaciones/cobertura`.
 */

import { useEffect, useState } from "react";
import { Info } from "lucide-react";

import { coberturaPublicaciones } from "@/lib/api";
import { fmtEntero, fmtPct, labelCanal } from "@/lib/publicaciones";
import type { CoberturaCanal, CoberturaPublicaciones } from "@/lib/types";

interface Props {
  /** Canal seleccionado en las pestañas. `general` NO se pide: Woo es la fuente del catálogo, no un canal de venta. */
  canal: string;
  /** `legacy_code` de la subcuenta activa (Mercado Libre), para que el censo mire lo mismo que la lista. */
  cuenta: string | null;
  color: string;
}

export default function ResumenPublicacionesCanal({ canal, cuenta, color }: Props) {
  const [cob, setCob] = useState<CoberturaPublicaciones | null>(null);
  const [fallo, setFallo] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setCob(null);
    setFallo(false);
    coberturaPublicaciones(canal, cuenta, ctrl.signal)
      .then(setCob)
      .catch((exc) => {
        if (exc?.name === "AbortError") return;
        setFallo(true);
      });
    return () => ctrl.abort();
  }, [canal, cuenta]);

  // Sin censo no se inventa uno: la línea desaparece. Un 0 aquí se leería como
  // "no hay publicaciones activas", que es exactamente lo que no se sabe.
  if (fallo || !cob) return null;

  const fila: CoberturaCanal | undefined = cob.canales.find((c) => c.canal === canal);
  if (!fila) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        Publicaciones en {labelCanal(canal)}
      </span>

      <Dato
        valor={fmtEntero(fila.publicaciones)}
        label="publicadas"
        ayuda={
          "Todo lo que existe en el canal, en cualquier estado: activas, pausadas, "
          + "cerradas, en revisión. Es el universo, no lo que se está vendiendo."
        }
      />

      <Dato
        valor={fmtEntero(fila.activas)}
        label="activas hoy"
        color={color}
        ayuda={
          `Se pueden comprar AHORA, con el criterio propio de ${labelCanal(canal)} `
          + "(lo decide el backend canal por canal; no es el mismo para todos).\n\n"
          + "La lista de abajo NO está filtrada por esto: su \"Solo publicados\" "
          + "cuenta también las pausadas y, en Amazon, las que se ven pero no se "
          + "venden."
          + (fila.nota ? `\n\n${fila.nota}` : "")
        }
      />

      <Dato
        valor={
          fila.pct_con_margen === null
            ? "no aplica"
            : `${fmtEntero(fila.con_margen)} de ${fmtEntero(fila.publicaciones)}`
        }
        label={
          fila.pct_con_margen === null
            ? "sin publicaciones que medir"
            : `con margen calculable (${fmtPct(fila.pct_con_margen)})`
        }
        ayuda={
          cob.aviso
          + "\n\nSobre el resto NO se puede saber: "
          + (Object.entries(fila.motivos)
              .map(([m, n]) => `${m.replace(/_/g, " ")} (${fmtEntero(n)})`)
              .join(" · ") || "sin motivos reportados")
          + ".\nUn margen promedio sin este porcentaje al lado se lee como un hecho "
          + "sobre todo el canal."
        }
      />

      {fila.nota && (
        <span
          className="flex items-center gap-1 text-[11px] text-slate-400"
          title={fila.nota}
        >
          <Info size={12} /> por qué este canal cuenta así
        </span>
      )}
    </div>
  );
}

function Dato({
  valor,
  label,
  ayuda,
  color,
}: {
  valor: string;
  label: string;
  ayuda: string;
  color?: string;
}) {
  return (
    <span className="flex items-baseline gap-1.5" title={ayuda}>
      <span
        className="text-base font-black tabular-nums"
        style={{ color: color ?? "#334155" }}
      >
        {valor}
      </span>
      <span className="text-xs font-medium text-slate-500">{label}</span>
    </span>
  );
}
