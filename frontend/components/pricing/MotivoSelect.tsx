"use client";

// MotivoSelect.tsx — «¿Por qué?» al mover un precio o una oferta desde PRICING (v0.673.0, pedido de Brandon
// 20-oct-2026). El motivo va a la bitácora del esquema `pricing` (pricing.accion.motivo): es lo único que contesta,
// meses después, por qué se movió cada precio. El catálogo vive en la base (pricing.motivo, crece sin migración);
// si no se puede leer, se usa el de la migración 0074. «Otro» pide texto.

import { useEffect, useState } from "react";

import { pricingMotivosCambio, type PricingMotivoCambio } from "@/lib/api";

const RESPALDO: PricingMotivoCambio[] = [
  { codigo: "mas_cara_que_competencia", nombre: "Más cara que la competencia", descripcion: null },
  { codigo: "mas_barata_que_competencia", nombre: "Muy abajo de la competencia", descripcion: null },
  { codigo: "sin_ventas", nombre: "Sin ventas", descripcion: null },
  { codigo: "poca_conversion", nombre: "Convierte poco", descripcion: null },
  { codigo: "subir_margen", nombre: "Subir margen", descripcion: null },
  { codigo: "bajo_costo", nombre: "Precio abajo del costo", descripcion: null },
  { codigo: "sobrestock", nombre: "Mucho stock", descripcion: null },
  { codigo: "se_agota", nombre: "Se va a agotar", descripcion: null },
  { codigo: "campana_canal", nombre: "Campaña del canal", descripcion: null },
  { codigo: "temporada", nombre: "Temporada o evento", descripcion: null },
  { codigo: "correccion", nombre: "Corregir un error", descripcion: null },
  { codigo: "prueba", nombre: "Prueba o experimento", descripcion: null },
  { codigo: "instruccion", nombre: "Instrucción de alguien", descripcion: null },
  { codigo: "otro", nombre: "Otro", descripcion: null },
];

// Una sola lectura por página: todas las filas comparten el catálogo.
let cargando: Promise<PricingMotivoCambio[]> | null = null;
function catalogo(): Promise<PricingMotivoCambio[]> {
  if (!cargando)
    cargando = pricingMotivosCambio()
      .then((r) => (r.motivos.length ? r.motivos : RESPALDO))
      .catch(() => RESPALDO);
  return cargando;
}

/** Listo para mandar: hay motivo, y si es «otro», con texto. */
export const motivoListo = (motivo: string, texto: string) => !!motivo && (motivo !== "otro" || texto.trim().length > 0);

export default function MotivoSelect({
  motivo,
  texto,
  onMotivo,
  onTexto,
  compacto = false,
}: {
  motivo: string;
  texto: string;
  onMotivo: (m: string) => void;
  onTexto: (t: string) => void;
  /** En la celda de Ofertas: sin la nota de abajo. */
  compacto?: boolean;
}) {
  const [lista, setLista] = useState<PricingMotivoCambio[]>(RESPALDO);
  useEffect(() => {
    let vivo = true;
    void catalogo().then((xs) => vivo && setLista(xs));
    return () => {
      vivo = false;
    };
  }, []);
  const elegido = lista.find((m) => m.codigo === motivo);

  return (
    <div className="ed-motivo">
      <label>
        ¿Por qué?
        <select value={motivo} onChange={(e) => onMotivo(e.target.value)} aria-label="Motivo del cambio" required>
          <option value="">Escoge el motivo…</option>
          {lista.map((m) => (
            <option key={m.codigo} value={m.codigo} title={m.descripcion || undefined}>
              {m.nombre}
            </option>
          ))}
        </select>
      </label>
      {(motivo === "otro" || !compacto) && (
        <input
          type="text"
          value={texto}
          maxLength={2000}
          onChange={(e) => onTexto(e.target.value)}
          placeholder={motivo === "otro" ? "Escribe el motivo (obligatorio)" : "Detalle (opcional)"}
          aria-label="Detalle del motivo"
        />
      )}
      {!compacto && elegido?.descripcion && <div className="sm">{elegido.descripcion}</div>}
    </div>
  );
}
