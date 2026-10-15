"use client";

// Rivales.tsx — «Lo scrapeado» de un SKU: cada rival de la búsqueda con su
// foto, su precio y por qué contó o no para la media (la regla del juez de
// OMNICANAL más la revisión con fotos del 14-oct). Arriba, nuestras
// publicaciones de ese SKU.

import { useEffect, useRef } from "react";

import type { PricingRivales } from "@/lib/api";
import { pesos } from "./formato";
import { Mini } from "./Miniaturas";
import type { Fila } from "./reglas";

const MODO: Record<string, string> = {
  m: "Cuentan para la media: el mismo producto y cantidad",
  p: "El mismo producto en otra cantidad (no cuenta)",
  d: "Dudosos, no cuentan: copia repetida o la foto no muestra el producto",
  b: "Marca de renombre o sin revisar (no cuenta)",
  n: "Nuestras publicaciones",
  x: "Descartados: otro producto, accesorio u otra gama",
};
const PLEGADOS = new Set(["x", "b", "n"]);

export default function Rivales({
  sku,
  filas,
  riv,
  series,
  onCerrar,
}: {
  sku: string | null;
  filas: Fila[];
  riv: Record<string, PricingRivales>;
  series: { pubs: string; riv: string };
  onCerrar: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (sku && !d.open) d.showModal();
    if (!sku && d.open) d.close();
  }, [sku]);

  const f = sku ? filas.find((z) => z.p.sku === sku) : undefined;
  const x = sku ? riv[sku] : undefined;
  const c = f?.p.comp || {};
  const mismos = x ? x.r.filter((r) => r[4] === "m").map((r) => r[1]).sort((u, v) => u - v) : [];
  const med = mismos.length
    ? mismos.length % 2
      ? mismos[(mismos.length - 1) / 2]
      : (mismos[mismos.length / 2 - 1] + mismos[mismos.length / 2]) / 2
    : null;
  const nuestras = sku ? filas.filter((z) => z.p.sku === sku) : [];

  return (
    <dialog
      ref={ref}
      aria-labelledby="pm-dlg-t"
      onClose={onCerrar}
      onClick={(e) => {
        if (e.target === ref.current) ref.current?.close();
      }}
    >
      {sku && (
        <>
          <div className="dlg-cab">
            <div>
              <h3 id="pm-dlg-t">{sku} · lo scrapeado en Mercado Libre</h3>
              {x && (
                <div className="sm">
                  Búsqueda <b>«{x.termino}»</b> · medida el {x.capturado} · {x.r.length} resultados ·{" "}
                  {med != null ? (
                    <>
                      de los {mismos.length} que cuentan: mín <b className="mono">{pesos(mismos[0])}</b> · media{" "}
                      <b className="mono">{pesos(med)}</b> · máx <b className="mono">{pesos(mismos[mismos.length - 1])}</b>
                    </>
                  ) : (
                    "menos de 2 que cuenten"
                  )}
                  {c.convertido ? ` · la referencia usa el precio del revisor con packs convertidos (${pesos(c.med)})` : ""}
                </div>
              )}
            </div>
            <button className="cerrar" type="button" onClick={() => ref.current?.close()}>
              Cerrar
            </button>
          </div>
          <div className="dlg-cuerpo">
            {nuestras.map((z) => (
              <div className="nuestro" key={z.p.id}>
                <Mini serie={series.pubs} i={z.p.img} px={90} alt={z.p.t} />
                <div>
                  <div className="sm">NUESTRA · {z.p.c}</div>
                  <a href={z.p.u} target="_blank" rel="noopener noreferrer">
                    <b>{z.p.t}</b>
                  </a>
                  <div className="mono">
                    {pesos(z.m.precio)}
                    {z.m.desc > 0.004 && (
                      <>
                        {" "}
                        <s>{pesos(z.m.normal)}</s>
                      </>
                    )}{" "}
                    · {z.p.stock} pzs FULL
                  </div>
                </div>
              </div>
            ))}
            {!x ? (
              <p>Este precio viene del Excel v4 ({c.fuente || ""}): no hay detalle de rivales guardado.</p>
            ) : (
              ["m", "p", "d", "b", "n", "x"].map((modo) => {
                const rs = x.r.filter((r) => r[4] === modo).sort((u, v) => u[1] - v[1]);
                if (!rs.length) return null;
                const titulo = `${MODO[modo]} · ${rs.length}`;
                const lista = rs.map((r, j) => (
                  <div className="riv" key={j}>
                    <Mini serie={series.riv} i={r[7]} px={60} alt={r[0]} />
                    <div className="num">{pesos(r[1])}</div>
                    <a href={r[3]} target="_blank" rel="noopener noreferrer">
                      {r[0]}
                    </a>
                    <div className="sm">
                      {r[2] || ""}
                      {modo === "p" && r[5] ? ` · nuestro ${r[5]} pzs, el suyo ${r[6]}` : ""}
                    </div>
                  </div>
                ));
                return PLEGADOS.has(modo) ? (
                  <details key={modo}>
                    <summary>
                      <h5 style={{ display: "inline" }}>{titulo}</h5>
                    </summary>
                    {lista}
                  </details>
                ) : (
                  <div key={modo}>
                    <h5>{titulo}</h5>
                    {lista}
                  </div>
                );
              })
            )}
          </div>
        </>
      )}
    </dialog>
  );
}
