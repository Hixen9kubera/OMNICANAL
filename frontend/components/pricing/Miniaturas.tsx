"use client";

// Miniaturas.tsx — Las fotos del monitor salen de HOJAS (cuadrículas de 20×20
// celdas de 90 px), como en el artifact: `p*` son nuestras publicaciones y `s*`
// los rivales. Cada hoja se pide UNA vez, con la sesión (`pricingHoja`), y se
// usa como fondo recortado. Las de rivales solo se piden al abrir «lo
// scrapeado» de un SKU. Si una hoja no llega, queda el cuadro vacío.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { pricingHoja } from "@/lib/api";

interface Hojas {
  url: (nombre: string) => string | null;
  pedir: (nombre: string) => void;
  celda: number;
  lado: number;
}

const Contexto = createContext<Hojas | null>(null);

export function ProveedorHojas({
  version,
  celda,
  lado,
  children,
}: {
  version: string;
  celda: number;
  lado: number;
  children: React.ReactNode;
}) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const pedidas = useRef(new Set<string>());
  const vivas = useRef<string[]>([]);

  const pedir = useCallback(
    (nombre: string) => {
      if (pedidas.current.has(nombre)) return;
      pedidas.current.add(nombre);
      pricingHoja(nombre, version)
        .then((u) => {
          vivas.current.push(u);
          setUrls((prev) => ({ ...prev, [nombre]: u }));
        })
        .catch(() => {
          // Sin miniatura no se rompe nada: queda el cuadro vacío.
        });
    },
    [version],
  );

  // Las URL de objeto viven hasta que se sale de la página.
  useEffect(() => () => vivas.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const url = useCallback((nombre: string) => urls[nombre] ?? null, [urls]);
  return <Contexto.Provider value={{ url, pedir, celda, lado }}>{children}</Contexto.Provider>;
}

export function Mini({ serie, i, px, alt }: { serie: string; i: number | null | undefined; px: number; alt?: string }) {
  const ctx = useContext(Contexto);
  const lado = ctx?.lado ?? 20;
  const celda = ctx?.celda ?? 90;
  const nombre = i == null ? null : `${serie}${Math.floor(i / (lado * lado))}`;
  const pedir = ctx?.pedir;
  useEffect(() => {
    if (nombre && pedir) pedir(nombre);
  }, [nombre, pedir]);
  const url = nombre && ctx ? ctx.url(nombre) : null;
  if (i == null || !url) return <span className="mini vacia" style={{ width: px, height: px }} />;
  const pos = i % (lado * lado);
  const k = px / celda;
  return (
    <span
      className="mini"
      role="img"
      aria-label={alt || ""}
      style={{
        width: px,
        height: px,
        backgroundImage: `url(${url})`,
        backgroundSize: `${celda * lado * k}px ${celda * lado * k}px`,
        backgroundPosition: `-${(pos % lado) * celda * k}px -${Math.floor(pos / lado) * celda * k}px`,
      }}
    />
  );
}
