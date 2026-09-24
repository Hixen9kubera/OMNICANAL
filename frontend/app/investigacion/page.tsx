"use client";

/**
 * /investigacion — Lecturas a la Open API de Temu, desde producción.
 *
 * POR QUÉ (Brandon, 24-sep-2026): Temu sólo acepta llamadas desde la IP de
 * Railway; desde una laptop contesta `5000003 NOT_IN_IP_WHITE_LIST`. Esta
 * pantalla manda la consulta al backend, que la hace con las credenciales de
 * la tienda y devuelve la respuesta REDACTADA.
 *
 * LO QUE NO HACE, y no por la pantalla sino por el backend: escribir. El
 * candado sólo deja pasar tipos que terminan en `get` o `query` y rechaza
 * cualquier verbo de escritura en el nombre. Sólo admin con sesión.
 *
 * No va en el menú a propósito: es una herramienta, no una pestaña.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Check, Copy, FlaskConical, Loader2, Play, ShieldAlert,
} from "lucide-react";
import AppNavbar from "@/components/AppNavbar";
import {
  investigarTemu,
  mensajeDeError,
  tiposInvestigacionTemu,
  type InvestigacionTemuResp,
  type InvestigacionTemuTipos,
} from "@/lib/api";

const PARAMS_INICIALES = '{\n  "pageNumber": 1,\n  "pageSize": 10\n}';

/** Los params del textarea, o el motivo por el que no son un objeto JSON. */
function leerParams(texto: string): { ok: true; valor: Record<string, unknown> } | { ok: false; error: string } {
  const t = texto.trim();
  if (!t) return { ok: true, valor: {} };
  try {
    const v: unknown = JSON.parse(t);
    if (v === null || typeof v !== "object" || Array.isArray(v)) {
      return { ok: false, error: "Los params tienen que ser un objeto JSON: { … }" };
    }
    return { ok: true, valor: v as Record<string, unknown> };
  } catch (e) {
    return { ok: false, error: `JSON inválido: ${e instanceof Error ? e.message : String(e)}` };
  }
}

export default function InvestigacionPage() {
  const [tipos, setTipos] = useState<InvestigacionTemuTipos | null>(null);
  const [errorTipos, setErrorTipos] = useState<string | null>(null);
  const [tipo, setTipo] = useState("bg.order.list.v2.get");
  const [params, setParams] = useState(PARAMS_INICIALES);
  const [cargando, setCargando] = useState(false);
  const [resp, setResp] = useState<InvestigacionTemuResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiado, setCopiado] = useState(false);

  useEffect(() => {
    const ctl = new AbortController();
    tiposInvestigacionTemu(ctl.signal)
      .then(setTipos)
      .catch((e) => {
        if (!ctl.signal.aborted) {
          setErrorTipos(mensajeDeError(e, "No se pudo leer la regla (¿sesión de admin?)."));
        }
      });
    return () => ctl.abort();
  }, []);

  const parseo = useMemo(() => leerParams(params), [params]);
  const sugerido = tipos?.sugeridos.find((s) => s.type === tipo.trim());

  const elegir = (t: string) => {
    const s = tipos?.sugeridos.find((x) => x.type === t);
    if (!s) return;
    setTipo(s.type);
    setParams(JSON.stringify(s.params, null, 2));
  };

  const consultar = useCallback(async () => {
    if (!parseo.ok || cargando) return;
    setCargando(true);
    setError(null);
    setResp(null);
    setCopiado(false);
    try {
      // El backend NO normaliza el tipo (un espacio de más es un 400). Recortar
      // aquí es sólo cortesía para lo que se pega del portapapeles.
      setResp(await investigarTemu(tipo.trim(), parseo.valor));
    } catch (e) {
      setError(mensajeDeError(e, "La consulta falló."));
    } finally {
      setCargando(false);
    }
  }, [parseo, cargando, tipo]);

  const textoResp = resp ? JSON.stringify(resp, null, 2) : "";

  const copiar = () => {
    if (!textoResp) return;
    void navigator.clipboard?.writeText(textoResp).then(() => {
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1500);
    });
  };

  return (
    <>
      <AppNavbar />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <header className="mb-6">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <FlaskConical className="h-6 w-6 text-indigo-400" />
            Investigación · Temu
          </h1>
          <p className="mt-1 text-sm text-zinc-400">
            Lecturas a la Open API de Temu hechas desde producción (la única IP que Temu acepta).
          </p>
        </header>

        <div className="mb-6 flex items-start gap-3 rounded-lg border border-sky-500/30
                        bg-sky-500/10 p-4 text-sm text-sky-100">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Sólo lecturas: el backend rechaza cualquier tipo que no termine en <code>get</code> o{" "}
            <code>query</code> o que lleve un verbo de escritura, sin llegar a Temu. Sólo sale el
            texto del negocio (ids, SKUs, guía, almacén, paquetería, estados, importes); lo demás
            —y todo dato del comprador— sale como <code>[redactado]</code>.
            {tipos && (
              <> Límite: {tipos.limite.llamadas} consultas por {tipos.limite.por_segundos} s entre
                todos (la cuota de Temu es la misma que usa la operación).</>
            )}
          </span>
        </div>

        {errorTipos && (
          <div className="mb-6 flex items-start gap-3 rounded-lg border border-amber-500/30
                          bg-amber-500/10 p-4 text-sm text-amber-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{errorTipos}</span>
          </div>
        )}
        {tipos && !tipos.temu_configurado && (
          <div className="mb-6 flex items-start gap-3 rounded-lg border border-amber-500/30
                          bg-amber-500/10 p-4 text-sm text-amber-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Temu no está configurado en este ambiente: las consultas contestarán 503.</span>
          </div>
        )}

        <section className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          {/* ── Consulta ── */}
          <div className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
            <label className="block text-sm">
              <span className="text-zinc-400">Tipos conocidos</span>
              <select
                value={sugerido ? sugerido.type : ""}
                onChange={(e) => elegir(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2
                           text-sm"
              >
                <option value="">— elegir para llenar type y params —</option>
                {tipos?.sugeridos.map((s) => (
                  <option key={s.type} value={s.type}>
                    {s.type}{s.estado !== "verificado" ? " (por verificar)" : ""}
                  </option>
                ))}
              </select>
              {sugerido && <span className="mt-1 block text-xs text-zinc-500">{sugerido.para}</span>}
            </label>

            <label className="block text-sm">
              <span className="text-zinc-400">type</span>
              <input
                value={tipo}
                onChange={(e) => setTipo(e.target.value)}
                spellCheck={false}
                placeholder="bg.order.list.v2.get"
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2
                           font-mono text-sm"
              />
            </label>

            <label className="block text-sm">
              <span className="text-zinc-400">params (objeto JSON)</span>
              <textarea
                value={params}
                onChange={(e) => setParams(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    void consultar();
                  }
                }}
                spellCheck={false}
                rows={10}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2
                           font-mono text-xs"
              />
              {!parseo.ok && <span className="mt-1 block text-xs text-amber-400">{parseo.error}</span>}
            </label>

            <button
              onClick={() => void consultar()}
              disabled={cargando || !parseo.ok || !tipo.trim()}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2
                         text-sm font-medium hover:bg-indigo-500 disabled:opacity-50"
            >
              {cargando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              Consultar
            </button>
            <span className="ml-3 text-xs text-zinc-500">Ctrl+Enter desde los params</span>

            {tipos && (
              <details className="text-xs text-zinc-400">
                <summary className="cursor-pointer text-zinc-300">La regla del candado</summary>
                <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-zinc-950 p-3">
                  {JSON.stringify(tipos.regla, null, 2)}
                </pre>
              </details>
            )}
            {tipos && (
              <details className="text-xs text-zinc-400">
                <summary className="cursor-pointer text-zinc-300">Qué significa cada código</summary>
                <dl className="mt-2 space-y-1">
                  {Object.entries(tipos.codigos).map(([cod, txt]) => (
                    <div key={cod} className="flex gap-3">
                      <dt className="w-24 shrink-0 font-mono text-zinc-300">{cod}</dt>
                      <dd>{txt}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
          </div>

          {/* ── Respuesta ── */}
          <div className="min-w-0 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-medium">Respuesta</h2>
              <button
                onClick={copiar}
                disabled={!textoResp}
                className="inline-flex items-center gap-2 rounded-lg bg-zinc-800 px-3 py-1.5
                           text-sm hover:bg-zinc-700 disabled:opacity-40"
              >
                {copiado ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
                {copiado ? "Copiado" : "Copiar JSON"}
              </button>
            </div>

            {error && (
              <div className="mb-3 flex items-start gap-3 rounded-lg border border-amber-500/30
                              bg-amber-500/10 p-3 text-sm text-amber-200">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            {resp && (
              <div className="mb-3 flex flex-wrap gap-2 text-xs">
                <span className={`rounded-full px-2.5 py-1 ring-1 ${resp.ok
                  ? "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30"
                  : "bg-amber-500/15 text-amber-400 ring-amber-500/30"}`}>
                  {resp.ok ? "OK" : `código ${resp.codigo ?? "?"}`}
                </span>
                {resp.lectura && (
                  <span className="rounded-full bg-zinc-800 px-2.5 py-1 text-zinc-300">{resp.lectura}</span>
                )}
                <span className="rounded-full bg-zinc-800 px-2.5 py-1 text-zinc-400">{resp.ms} ms</span>
                {resp.campos_redactados !== undefined && (
                  <span className="rounded-full bg-zinc-800 px-2.5 py-1 text-zinc-400">
                    {resp.campos_redactados} campos redactados
                  </span>
                )}
              </div>
            )}

            {resp ? (
              <pre className="max-h-[70vh] overflow-auto rounded-lg bg-zinc-950 p-3 font-mono
                              text-xs text-zinc-300">
                {textoResp}
              </pre>
            ) : (
              <p className="text-sm text-zinc-500">
                {cargando ? "Consultando a Temu…" : "Todavía no hay consulta."}
              </p>
            )}
          </div>
        </section>
      </main>
    </>
  );
}
