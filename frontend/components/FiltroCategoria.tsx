"use client";

import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Tag, X } from "lucide-react";
import type { CategoriaWC } from "@/lib/api";

// Marcas diacríticas combinantes (U+0300–U+036F): lo que NFD separa de la letra.
const DIACRITICOS = new RegExp(`[${String.fromCharCode(0x300)}-${String.fromCharCode(0x36f)}]`, "g");

// Sin acentos y en minúsculas: "electronica" encuentra "Electrónica".
function normalizar(s: string): string {
  return s.normalize("NFD").replace(DIACRITICOS, "").toLowerCase();
}

// Pintar las 1,224 de golpe no aporta: nadie las recorre. Se escribe y se acota.
const MAX_VISIBLES = 200;

/**
 * Filtro por categoría con BÚSQUEDA. Un <select> no sirve aquí: la vista
 * Productos tiene productos en 1,224 categorías y 638 de ellas con uno solo;
 * nadie encuentra la suya bajando una lista así. Se escribe un pedazo del
 * nombre ("funda", "electronica") y la lista se reduce; flechas + Enter para
 * elegir, Escape para cerrar, la ✕ quita el filtro.
 */
export default function FiltroCategoria({
  categorias,
  valor,
  onCambio,
}: {
  categorias: CategoriaWC[];
  valor: number | null;
  onCambio: (id: number | null) => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const [texto, setTexto] = useState("");
  const [activo, setActivo] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listaRef = useRef<HTMLUListElement>(null);

  const elegida = useMemo(
    () => categorias.find((c) => c.id === valor) ?? null,
    [categorias, valor],
  );
  const indexadas = useMemo(
    () => categorias.map((c) => ({ c, clave: normalizar(c.nombre) })),
    [categorias],
  );
  const coincidencias = useMemo(() => {
    const q = normalizar(texto.trim());
    return (q ? indexadas.filter((x) => x.clave.includes(q)) : indexadas).map((x) => x.c);
  }, [indexadas, texto]);
  const visibles = coincidencias.slice(0, MAX_VISIBLES);

  // La opción activa siempre a la vista al moverse con las flechas.
  useEffect(() => {
    if (!abierto) return;
    listaRef.current
      ?.querySelector<HTMLElement>(`[data-i="${activo}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activo, abierto]);

  function cerrar() {
    setAbierto(false);
    setTexto("");
  }

  function elegir(id: number | null) {
    onCambio(id);
    cerrar();
    inputRef.current?.blur();
  }

  function teclas(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setAbierto(true);
      setActivo((i) => Math.min(i + 1, Math.max(visibles.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActivo((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (abierto && visibles[activo]) elegir(visibles[activo].id);
    } else if (e.key === "Escape") {
      cerrar();
      inputRef.current?.blur();
    }
  }

  return (
    <div className="relative">
      <Tag size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
      <input
        ref={inputRef}
        value={abierto ? texto : (elegida?.nombre ?? "")}
        onChange={(e) => { setTexto(e.target.value); setActivo(0); setAbierto(true); }}
        onFocus={() => { setAbierto(true); setActivo(0); }}
        onBlur={cerrar}
        onKeyDown={teclas}
        placeholder={abierto && elegida ? elegida.nombre : "Categoría…"}
        title="Filtrar por categoría de WooCommerce (incluye sus subcategorías). Escribe para buscar."
        role="combobox"
        aria-expanded={abierto}
        aria-controls="filtro-categoria-lista"
        aria-autocomplete="list"
        aria-activedescendant={abierto && visibles[activo] ? `filtro-categoria-${visibles[activo].id}` : undefined}
        className={[
          "w-56 truncate rounded-lg border py-2 pl-9 pr-8 text-sm outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300",
          elegida
            ? "border-indigo-300 bg-indigo-50 font-medium text-indigo-700"
            : "border-slate-200 bg-white text-slate-700",
        ].join(" ")}
      />
      {elegida && !abierto ? (
        <button
          type="button"
          onClick={() => elegir(null)}
          title="Quitar el filtro de categoría"
          aria-label="Quitar el filtro de categoría"
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-indigo-500 hover:bg-indigo-100"
        >
          <X size={14} />
        </button>
      ) : (
        <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
      )}
      {abierto && (
        <ul
          id="filtro-categoria-lista"
          ref={listaRef}
          role="listbox"
          // Sin esto, el clic en una opción (o en la barra de desplazamiento)
          // le quita el foco al input, su onBlur cierra la lista y el clic
          // llega a una opción que ya no existe.
          onMouseDown={(e) => e.preventDefault()}
          className="absolute right-0 z-30 mt-1 max-h-80 w-72 overflow-auto rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg"
        >
          {visibles.length === 0 ? (
            <li className="px-3 py-2 text-slate-400">Ninguna categoría con «{texto.trim()}»</li>
          ) : (
            visibles.map((c, i) => (
              <li
                key={c.id}
                id={`filtro-categoria-${c.id}`}
                data-i={i}
                role="option"
                aria-selected={c.id === valor}
                onClick={() => elegir(c.id)}
                onMouseEnter={() => setActivo(i)}
                className={[
                  "cursor-pointer truncate px-3 py-1.5",
                  i === activo ? "bg-indigo-50 text-indigo-700" : "text-slate-700",
                  c.id === valor ? "font-semibold" : "",
                ].join(" ")}
              >
                {c.nombre}
              </li>
            ))
          )}
          {coincidencias.length > MAX_VISIBLES && (
            <li className="border-t border-slate-100 px-3 py-1.5 text-xs text-slate-400">
              {new Intl.NumberFormat("es-MX").format(coincidencias.length - MAX_VISIBLES)} más — escribe para acotar
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
