/**
 * Moneda — el tratamiento visual de TODO campo que lleva dinero (Eduardo, 5-ago).
 *
 * POR QUÉ EXISTE. En Costos conviven dos monedas y no se distinguían: el costo
 * del producto se CAPTURA en dólares, pero se GUARDA en pesos (se multiplica
 * por el tipo de cambio al grabar y se divide al mostrar). Todo lo demás —flete
 * CBM, costo final, precios— es peso. Dos campos idénticos, uno al lado del
 * otro, con 19× de diferencia entre teclear en uno o en el otro. Un 1,625 en la
 * casilla equivocada son $30,891.
 *
 * CÓMO SE DISTINGUEN. Color + etiqueta, nunca color solo: quien no distinga
 * ámbar de índigo sigue leyendo "USD" y "MXN" en letras.
 *   · USD → ámbar, fondo tenue y anillo ámbar al enfocar
 *   · MXN → índigo, el tono neutro del panel
 * El chip va DENTRO del campo, pegado al borde derecho, para que viaje con la
 * casilla aunque la etiqueta de arriba se corte o la columna se angoste.
 *
 * Vive aparte y no dentro de Costos porque el mismo par de monedas aparece en
 * el editor por producto, en la tabla masiva y en el Estudio.
 */

export type Moneda = "USD" | "MXN";

/** Paleta por moneda. Un solo lugar que cambiar si se suma otra divisa. */
export const TONO: Record<Moneda, {
  chip: string; borde: string; anillo: string; fondo: string; texto: string;
}> = {
  USD: {
    chip: "bg-amber-100 text-amber-700 ring-1 ring-amber-200",
    borde: "border-amber-300",
    anillo: "focus:ring-amber-300",
    fondo: "bg-amber-50/40",
    texto: "text-amber-700",
  },
  MXN: {
    chip: "bg-indigo-50 text-indigo-600 ring-1 ring-indigo-100",
    borde: "border-indigo-200",
    anillo: "focus:ring-indigo-300",
    fondo: "bg-white",
    texto: "text-indigo-700",
  },
};

/** La notación: "USD" / "MXN" en un chip. `flotante` lo mete dentro del campo. */
export function ChipMoneda({ moneda, flotante, className = "" }: {
  moneda: Moneda; flotante?: boolean; className?: string;
}) {
  return (
    <span
      title={moneda === "USD" ? "Dólares estadounidenses" : "Pesos mexicanos"}
      className={[
        "rounded px-1 py-px text-[9px] font-bold tracking-wide",
        TONO[moneda].chip,
        flotante ? "pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2" : "",
        className,
      ].join(" ")}
    >
      {moneda}
    </span>
  );
}

/**
 * Campo de captura con su moneda marcada: signo a la izquierda, chip a la
 * derecha y el borde del color de la divisa.
 *
 * `padDerecha` deja sitio al chip; con `compacto` (celdas de tabla) el chip se
 * encoge para no comerse el número.
 */
export function EntradaMoneda({
  moneda, value, onChange, placeholder, compacto, alineado = "left", titulo,
}: {
  moneda: Moneda;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  compacto?: boolean;
  alineado?: "left" | "right";
  titulo?: string;
}) {
  const t = TONO[moneda];
  return (
    <div className="relative" title={titulo}>
      <span className={`pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-sm ${t.texto}`}>
        $
      </span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        inputMode="decimal"
        className={[
          "w-full rounded-lg border-2 tabular-nums text-slate-800 outline-none focus:ring-2",
          t.borde, t.anillo, t.fondo,
          compacto ? "py-1 pl-5 pr-9 text-xs" : "py-2.5 pl-6 pr-12 text-sm",
          alineado === "right" ? "text-right" : "",
        ].join(" ")}
      />
      <ChipMoneda moneda={moneda} flotante className={compacto ? "scale-90" : ""} />
    </div>
  );
}

/** Encabezado de columna con su moneda: "Costo prod. [USD]". */
export function TituloMoneda({ children, moneda }: {
  children: React.ReactNode; moneda: Moneda;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      {children}
      <ChipMoneda moneda={moneda} />
    </span>
  );
}
