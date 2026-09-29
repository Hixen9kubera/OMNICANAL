"use client";

/**
 * Hook de lectura con caché en memoria por URL.
 *
 * Lo que se pide es una FOTO del pipeline (se regenera una vez al día), así que
 * repetir la misma consulta al cambiar de pestaña sólo gasta: se guarda la
 * promesa y se reusa. Al recargar se conserva lo último pintado (la regla de
 * dataviz: al refrescar, el marco se queda y no parpadea).
 *
 * `nulo404`: un 404 llega como `datos = null` y sin error (p. ej. la historia de
 * una publicación que no es de ML). `listo` distingue «todavía no llega» de
 * «llegó vacío».
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { pedir, traerTodo, NoAutorizado } from "./api";
import type { Params } from "./api";

const cache = new Map<string, Promise<unknown>>();

function clave(camino: string, params?: Params, todo?: boolean) {
  return `${todo ? "todo:" : ""}${camino}?${JSON.stringify(params ?? {})}`;
}

/** Tras un recálculo: todo lo cacheado es de la corrida anterior. */
export function olvidarCache() { cache.clear(); }

export function usePedido<T>(camino: string | null, params?: Params,
  opciones: { todo?: boolean; sinCache?: boolean; nulo404?: boolean } = {}) {
  const [datos, setDatos] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState<boolean>(camino !== null);
  const [listo, setListo] = useState(false);
  const [vuelta, setVuelta] = useState(0);
  const k = camino === null ? null : clave(camino, params, opciones.todo);
  const ultima = useRef<string | null>(null);

  useEffect(() => {
    if (k === null || camino === null) { setCargando(false); return; }
    let vivo = true;
    ultima.current = k;
    setCargando(true);
    setError(null);
    let p = opciones.sinCache ? undefined : cache.get(k);
    if (!p) {
      p = opciones.todo ? traerTodo(camino, params) : pedir(camino, params, { nulo404: opciones.nulo404 });
      if (!opciones.sinCache) cache.set(k, p);
      p.catch(() => cache.delete(k));
    }
    p.then((d) => { if (vivo && ultima.current === k) { setDatos(d as T); setCargando(false); setListo(true); } })
     .catch((e: unknown) => {
       if (!vivo) return;
       setCargando(false);
       if (e instanceof NoAutorizado) return; // ya va rumbo a /login
       setError(e instanceof Error ? e.message : "No se pudo leer el laboratorio.");
     });
    return () => { vivo = false; };
  }, [k, vuelta]); // eslint-disable-line react-hooks/exhaustive-deps

  const recargar = useCallback(() => { if (k) cache.delete(k); setVuelta((v) => v + 1); }, [k]);
  return { datos, error, cargando, listo, recargar };
}
