"use client";

/**
 * useTrabajoJob — el polling de un trabajo largo del backend.
 *
 * Los dos resolvedores de costos funcionan igual: un POST arranca el trabajo y
 * devuelve un `jid`, y la UI pregunta `GET /{jid}` cada 2.5 s hasta que el paso
 * sea `listo` o `error`. El análisis vive en MEMORIA del backend (3 h) y no se
 * persiste: si el usuario cierra la ventana, se pierde.
 *
 * Diferencia con el `useEffect` que tenía `ResolverCostosModal`: aquí se guarda
 * el id del `setTimeout` y se limpia en el cleanup. El original solo bajaba una
 * bandera `vivo`, así que abrir y cerrar el modal varias veces mientras corre
 * un trabajo largo dejaba temporizadores inertes acumulados.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { mensajeDeError } from "@/lib/api";

/** Pasos terminales: el polling se detiene al llegar a cualquiera de los dos. */
const TERMINALES = new Set(["listo", "error"]);

export interface TrabajoJob<T> {
  est: T | null;
  setEst: (v: T | null) => void;
  error: string | null;
  setError: (v: string | null) => void;
  /** Fuerza una lectura inmediata del estado (tras corregir una fila, p. ej.). */
  releer: () => Promise<void>;
}

export function useTrabajoJob<T extends { paso: string }>(
  jid: string | null,
  fetcher: (id: string, signal?: AbortSignal) => Promise<T>,
  intervalo = 2500,
): TrabajoJob<T> {
  const [est, setEst] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  // El fetcher se guarda en una ref para que un callback recreado en cada
  // render no reinicie el polling.
  const fnRef = useRef(fetcher);
  fnRef.current = fetcher;

  useEffect(() => {
    if (!jid) {
      setEst(null);
      return;
    }
    let vivo = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const e = await fnRef.current(jid);
        if (!vivo) return;
        setEst(e);
        if (!TERMINALES.has(e.paso)) timer = setTimeout(tick, intervalo);
      } catch (err) {
        if (vivo) setError(mensajeDeError(err, "Se perdió el contacto con el análisis."));
      }
    };
    tick();
    return () => {
      vivo = false;
      if (timer) clearTimeout(timer);
    };
  }, [jid, intervalo]);

  const releer = useCallback(async () => {
    if (!jid) return;
    try {
      setEst(await fnRef.current(jid));
    } catch (err) {
      setError(mensajeDeError(err, "No se pudo releer el estado del análisis."));
    }
  }, [jid]);

  return { est, setEst, error, setError, releer };
}

export default useTrabajoJob;
