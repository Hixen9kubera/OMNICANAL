/**
 * ChipRevision — la etiqueta de "este costeo ya se validó".
 *
 * Vive aparte porque la pintan DOS vistas (la tabla de Análisis y el popup
 * "Productos más vendidos") y ya hay precedente de lo que cuesta duplicar un
 * chip: PanelHover nació justo por eso.
 *
 * Tres estados, no dos — el tercero es el que importa y el que un simple
 * "validado sí/no" no sabe decir:
 *
 *   · sin marca      → nadie ha verificado ese costo contra el packing list
 *   · VALIDADO       → alguien lo verificó, y los números siguen siendo esos
 *   · VALIDADO (⚠)   → se validó, pero la fila se tocó DESPUÉS: la marca sigue
 *                      puesta y ya no describe los números que estás viendo
 *
 * Por qué esto va en una tabla de MÁRGENES: toda la columna de margen descansa
 * sobre `costo`, y un margen calculado sobre un costo verificado no vale lo
 * mismo que uno calculado sobre un costo que nadie miró. Sin el chip, los dos
 * se leen idénticos.
 *
 * El "sin marca" NO se pinta a propósito: hoy son las 15,838 filas y llenar la
 * tabla de chips grises solo haría ruido. Lo que se señala es lo verificado.
 */

interface Props {
  revisadoAt?: string | null;
  revisadoPor?: string | null;
  movida?: boolean;
  /** `full` agrega la fecha al lado; en tablas apretadas se deja en el tooltip. */
  variante?: "chip" | "full";
}

const fecha = (iso: string) => {
  try {
    return new Date(iso).toLocaleDateString("es-MX", {
      day: "2-digit", month: "short", year: "numeric",
    });
  } catch {
    return iso.slice(0, 10);
  }
};

export default function ChipRevision({
  revisadoAt, revisadoPor, movida = false, variante = "chip",
}: Props) {
  if (!revisadoAt) return null;

  const quien = revisadoPor || "sin firma";
  const cuando = fecha(revisadoAt);
  const ayuda = movida
    ? `Costeo validado el ${cuando} por ${quien}, PERO la fila se modificó `
      + `después: la validación ya no describe estos números. Hay que volver a `
      + `revisarlo.`
    : `Costeo validado el ${cuando} por ${quien} contra el packing list del `
      + `proveedor. Los números no se han movido desde entonces.`;

  const clase = movida
    ? "bg-amber-50 text-amber-700 ring-1 ring-amber-200"
    : "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200";

  return (
    <span
      title={ayuda}
      className={`inline-flex shrink-0 items-center gap-0.5 rounded px-1 text-[9px] font-bold ${clase}`}
    >
      {movida ? "VALIDADO ⚠" : "VALIDADO"}
      {variante === "full" && (
        <span className="font-normal opacity-70">· {cuando}</span>
      )}
    </span>
  );
}
