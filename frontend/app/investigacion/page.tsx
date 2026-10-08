"use client";

/**
 * /investigacion — Lecturas a las APIs de Temu y de TikTok, desde producción.
 *
 * POR QUÉ (Brandon, 24-sep-2026): Temu sólo acepta llamadas desde la IP de
 * Railway; desde una laptop contesta `5000003 NOT_IN_IP_WHITE_LIST`. Esta
 * pantalla manda la consulta al backend, que la hace con las credenciales de
 * la tienda y devuelve la respuesta REDACTADA.
 *
 * TIKTOK (7-oct-2026): lo mismo, para sondear qué permite su API antes de
 * automatizar el agendado de envíos. Pero el candado es otro: en TikTok el
 * nombre de la ruta no dice si escribe (`POST …/packages/search` lee y
 * `POST …/packages` compra una etiqueta), así que aquí NO se escribe ninguna
 * ruta. Se elige una consulta de una lista cerrada que vive en el backend y se
 * llenan sus campos; el método, la ruta y la tienda los pone el servidor.
 *
 * LO QUE NO HACE, y no por la pantalla sino por el backend: escribir. En Temu
 * el candado sólo deja pasar tipos que terminan en `get` o `query`; en TikTok,
 * sólo las consultas del catálogo. Sólo admin con sesión.
 *
 * No va en el menú a propósito: es una herramienta, no una pestaña. El canal
 * se puede fijar en la liga: /investigacion?canal=tiktok
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle, Check, Copy, FlaskConical, Loader2, Play, ShieldAlert,
} from "lucide-react";
import AppNavbar from "@/components/AppNavbar";
import {
  catalogoInvestigacionTikTok,
  investigarTemu,
  investigarTikTok,
  mensajeDeError,
  tiposInvestigacionTemu,
  type InvestigacionTemuResp,
  type InvestigacionTemuTipos,
  type InvestigacionTikTokCampo,
  type InvestigacionTikTokCatalogo,
  type InvestigacionTikTokConsulta,
  type InvestigacionTikTokResp,
  type InvestigacionTikTokValor,
} from "@/lib/api";

type Canal = "temu" | "tiktok";

const CANALES: { id: Canal; nombre: string; resumen: string }[] = [
  {
    id: "temu",
    nombre: "Temu",
    resumen: "Lecturas a la Open API de Temu hechas desde producción (la única IP que Temu acepta).",
  },
  {
    id: "tiktok",
    nombre: "TikTok",
    resumen:
      "Lecturas a la API de TikTok Shop hechas desde producción, para saber qué permite antes de "
      + "automatizar el agendado de envíos.",
  },
];

// ── Piezas que comparten los dos canales ─────────────────────────────────────

/** Lo que las dos respuestas tienen en común (lo que pinta el panel). */
interface RespuestaComun {
  ok: boolean;
  codigo: string | null;
  lectura?: string | null;
  ms: number;
  campos_redactados?: number;
}

function Aviso({ tono, children }: { tono: "azul" | "ambar"; children: ReactNode }) {
  const color = tono === "azul"
    ? "border-sky-500/30 bg-sky-500/10 text-sky-100"
    : "border-amber-500/30 bg-amber-500/10 text-amber-200";
  const Icono = tono === "azul" ? ShieldAlert : AlertTriangle;
  return (
    <div className={`mb-6 flex items-start gap-3 rounded-lg border p-4 text-sm ${color}`}>
      <Icono className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

/** La respuesta tal cual la mandó el backend (ya redactada), con "Copiar JSON". */
function PanelRespuesta({
  resp, error, cargando, esperando, children,
}: {
  resp: RespuestaComun | null;
  error: string | null;
  cargando: boolean;
  esperando: string;
  children?: ReactNode;
}) {
  const [copiado, setCopiado] = useState(false);
  const textoResp = useMemo(() => (resp ? JSON.stringify(resp, null, 2) : ""), [resp]);

  useEffect(() => setCopiado(false), [resp]);

  const copiar = () => {
    if (!textoResp) return;
    void navigator.clipboard?.writeText(textoResp).then(() => {
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1500);
    });
  };

  return (
    <div className="min-w-0 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-medium">Respuesta</h2>
        <button
          type="button"
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

      {children}

      {resp ? (
        <pre className="max-h-[70vh] overflow-auto rounded-lg bg-zinc-950 p-3 font-mono
                        text-xs text-zinc-300">
          {textoResp}
        </pre>
      ) : (
        <p className="text-sm text-zinc-500">
          {cargando ? esperando : "Todavía no hay consulta."}
        </p>
      )}
    </div>
  );
}

// ── Temu ─────────────────────────────────────────────────────────────────────

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

function PanelTemu() {
  const [tipos, setTipos] = useState<InvestigacionTemuTipos | null>(null);
  const [errorTipos, setErrorTipos] = useState<string | null>(null);
  const [tipo, setTipo] = useState("bg.order.list.v2.get");
  const [params, setParams] = useState(PARAMS_INICIALES);
  const [cargando, setCargando] = useState(false);
  const [resp, setResp] = useState<InvestigacionTemuResp | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <>
      <Aviso tono="azul">
        Sólo lecturas: el backend rechaza cualquier tipo que no termine en <code>get</code> o{" "}
        <code>query</code> o que lleve un verbo de escritura, sin llegar a Temu. Sólo sale el
        texto del negocio (ids, SKUs, guía, almacén, paquetería, estados, importes); lo demás
        —y todo dato del comprador— sale como <code>[redactado]</code>.
        {tipos && (
          <> Límite: {tipos.limite.llamadas} consultas por {tipos.limite.por_segundos} s entre
            todos (la cuota de Temu es la misma que usa la operación).</>
        )}
      </Aviso>

      {errorTipos && <Aviso tono="ambar">{errorTipos}</Aviso>}
      {tipos && !tipos.temu_configurado && (
        <Aviso tono="ambar">
          Temu no está configurado en este ambiente: las consultas contestarán 503.
        </Aviso>
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
            type="button"
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
        <PanelRespuesta resp={resp} error={error} cargando={cargando}
                        esperando="Consultando a Temu…" />
      </section>
    </>
  );
}

// ── TikTok ───────────────────────────────────────────────────────────────────

const ESTILO_CAMPO =
  "mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm";
const RE_ID = /^[0-9]{6,24}$/;

type Armado =
  | { ok: true; params: Record<string, InvestigacionTikTokValor> }
  | { ok: false; error: string };

/** Con qué se llena cada campo al elegir una consulta (lo que sugiere el backend). */
function valoresIniciales(c: InvestigacionTikTokConsulta): {
  valores: Record<string, string>;
  marcadas: Record<string, string[]>;
} {
  const valores: Record<string, string> = {};
  const marcadas: Record<string, string[]> = {};
  for (const f of c.campos) {
    const e = f.ejemplo;
    if (e === null || e === undefined) continue;
    if (Array.isArray(e)) {
      if (f.tipo === "opciones") marcadas[f.nombre] = e;
      else valores[f.nombre] = e.join(", ");
    } else {
      valores[f.nombre] = String(e);
    }
  }
  return { valores, marcadas };
}

/**
 * De lo que hay en el formulario a los `params` que entiende el backend.
 *
 * Esto es CORTESÍA para avisar antes de mandar: quien decide es el backend,
 * que valida cada parámetro contra el catálogo y rechaza lo que no esté
 * declarado. Sólo se mandan los campos llenos.
 */
function armarParams(
  c: InvestigacionTikTokConsulta,
  valores: Record<string, string>,
  marcadas: Record<string, string[]>,
): Armado {
  const params: Record<string, InvestigacionTikTokValor> = {};
  for (const f of c.campos) {
    if (f.tipo === "opciones") {
      const lista = marcadas[f.nombre] ?? [];
      if (lista.length > 0) params[f.nombre] = lista;
      else if (f.requerido) return { ok: false, error: `Falta: ${f.etiqueta}.` };
      continue;
    }
    const crudo = (valores[f.nombre] ?? "").trim();
    if (!crudo) {
      if (f.requerido) return { ok: false, error: `Falta: ${f.etiqueta}.` };
      continue;
    }
    switch (f.tipo) {
      case "id":
        if (!RE_ID.test(crudo)) {
          return { ok: false, error: `${f.etiqueta}: un id son de 6 a 24 dígitos.` };
        }
        params[f.nombre] = crudo;
        break;
      case "ids": {
        const ids = crudo.split(/[\s,;]+/).filter(Boolean);
        if (ids.some((x) => !RE_ID.test(x))) {
          return { ok: false, error: `${f.etiqueta}: cada id son de 6 a 24 dígitos, separados por coma.` };
        }
        if (f.maximo !== null && ids.length > f.maximo) {
          return { ok: false, error: `${f.etiqueta}: máximo ${f.maximo} por consulta.` };
        }
        params[f.nombre] = ids;
        break;
      }
      case "entero": {
        const n = Number(crudo);
        if (!/^[0-9]{1,9}$/.test(crudo)
            || (f.minimo !== null && n < f.minimo) || (f.maximo !== null && n > f.maximo)) {
          return {
            ok: false,
            error: `${f.etiqueta}: un entero entre ${f.minimo ?? 1} y ${f.maximo ?? "…"}.`,
          };
        }
        params[f.nombre] = n;
        break;
      }
      case "epoch": {
        // El campo es fecha y hora LOCALES de este equipo; TikTok quiere segundos Unix.
        const ms = new Date(crudo).getTime();
        if (Number.isNaN(ms)) return { ok: false, error: `${f.etiqueta}: fecha inválida.` };
        params[f.nombre] = Math.floor(ms / 1000);
        break;
      }
      case "booleano":
        params[f.nombre] = crudo === "true";
        break;
      case "region":
        params[f.nombre] = crudo.toUpperCase();
        break;
      default:                       // opcion, cursor, decimal: tal cual
        params[f.nombre] = crudo;
    }
  }
  // Los que van en grupo (largo, ancho, alto y su unidad): todos o ninguno.
  for (const grupo of c.juntos ?? []) {   // `??`: el backend puede ir un deploy atrás
    const faltan = grupo.filter((n) => !(n in params));
    if (faltan.length > 0 && faltan.length < grupo.length) {
      const etiquetas = faltan.map((n) => c.campos.find((f) => f.nombre === n)?.etiqueta ?? n);
      return { ok: false, error: `Van juntos o ninguno. Falta: ${etiquetas.join(", ")}.` };
    }
  }
  // Una búsqueda sin un solo filtro la rechaza el backend (400): se avisa aquí
  // y no se manda, para no dejar un «RECHAZADA» falso en la auditoría.
  if (c.metodo === "POST" && !c.campos.some((f) => f.donde === "cuerpo" && f.nombre in params)) {
    return { ok: false, error: "Elige al menos un filtro (estado, fechas o ids)." };
  }
  return { ok: true, params };
}

const REDACTADO = "[redactado]";

/** El cursor de la página siguiente, si la respuesta lo trae (y no salió tapado). */
function cursorSiguiente(result: unknown): string | null {
  if (result === null || typeof result !== "object" || Array.isArray(result)) return null;
  const t = (result as Record<string, unknown>).next_page_token;
  return typeof t === "string" && t && t !== REDACTADO ? t : null;
}

function CampoTikTok({
  campo, valor, marcadas, onValor, onMarcadas,
}: {
  campo: InvestigacionTikTokCampo;
  valor: string;
  marcadas: string[];
  onValor: (v: string) => void;
  onMarcadas: (v: string[]) => void;
}) {
  const titulo = (
    <span className="text-zinc-400">
      {campo.etiqueta}
      {campo.requerido && <span className="text-amber-400"> *</span>}
      <code className="ml-2 text-xs text-zinc-600">{campo.nombre}</code>
    </span>
  );
  const ayuda = campo.ayuda
    ? <span className="mt-1 block text-xs text-zinc-500">{campo.ayuda}</span>
    : null;

  if (campo.tipo === "opciones") {
    return (
      <fieldset className="block text-sm">
        <legend>{titulo}</legend>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
          {campo.opciones.map((o) => (
            <label key={o} className="inline-flex items-center gap-2 text-xs text-zinc-300">
              <input
                type="checkbox"
                checked={marcadas.includes(o)}
                onChange={(e) => onMarcadas(e.target.checked
                  ? [...marcadas, o]
                  : marcadas.filter((x) => x !== o))}
              />
              <span className="font-mono">{o}</span>
            </label>
          ))}
        </div>
        {ayuda}
      </fieldset>
    );
  }

  let control: ReactNode;
  if (campo.tipo === "opcion") {
    control = (
      <select value={valor} onChange={(e) => onValor(e.target.value)} className={ESTILO_CAMPO}>
        {!campo.requerido && <option value="">— sin valor —</option>}
        {campo.requerido && valor === "" && <option value="">— elegir —</option>}
        {campo.opciones.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else if (campo.tipo === "booleano") {
    control = (
      <select value={valor} onChange={(e) => onValor(e.target.value)} className={ESTILO_CAMPO}>
        <option value="">— sin valor —</option>
        <option value="true">Sí</option>
        <option value="false">No</option>
      </select>
    );
  } else if (campo.tipo === "epoch") {
    control = (
      <input
        type="datetime-local"
        value={valor}
        onChange={(e) => onValor(e.target.value)}
        className={ESTILO_CAMPO}
      />
    );
  } else if (campo.tipo === "entero") {
    control = (
      <input
        type="number"
        value={valor}
        min={campo.minimo ?? undefined}
        max={campo.maximo ?? undefined}
        step={1}
        onChange={(e) => onValor(e.target.value)}
        className={ESTILO_CAMPO}
      />
    );
  } else {
    const pista: Record<string, string> = {
      id: "sólo dígitos",
      ids: "ids separados por coma",
      cursor: "vacío = primera página",
      region: "MX",
      decimal: "1.2",
    };
    control = (
      <input
        value={valor}
        onChange={(e) => onValor(e.target.value)}
        spellCheck={false}
        autoComplete="off"
        inputMode={campo.tipo === "id" ? "numeric" : campo.tipo === "decimal" ? "decimal" : undefined}
        maxLength={campo.tipo === "region" ? 2 : undefined}
        placeholder={pista[campo.tipo] ?? ""}
        className={`${ESTILO_CAMPO} font-mono`}
      />
    );
  }

  return (
    <label className="block text-sm">
      {titulo}
      {control}
      {campo.tipo === "epoch" && (
        <span className="mt-1 block text-xs text-zinc-500">Hora de este equipo.</span>
      )}
      {ayuda}
    </label>
  );
}

function PanelTikTok() {
  const [catalogo, setCatalogo] = useState<InvestigacionTikTokCatalogo | null>(null);
  const [errorCatalogo, setErrorCatalogo] = useState<string | null>(null);
  const [nombre, setNombre] = useState("");
  const [valores, setValores] = useState<Record<string, string>>({});
  const [marcadas, setMarcadas] = useState<Record<string, string[]>>({});
  const [cargando, setCargando] = useState(false);
  const [resp, setResp] = useState<InvestigacionTikTokResp | null>(null);
  const [error, setError] = useState<string | null>(null);

  const elegir = useCallback((c: InvestigacionTikTokConsulta) => {
    const inicio = valoresIniciales(c);
    setNombre(c.nombre);
    setValores(inicio.valores);
    setMarcadas(inicio.marcadas);
    // El error era de la consulta anterior. La RESPUESTA se queda a la vista a
    // propósito: de ahí se copian los ids que pide la consulta siguiente.
    setError(null);
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    catalogoInvestigacionTikTok(ctl.signal)
      .then((cat) => {
        setCatalogo(cat);
        const primera = cat.consultas.find((c) => !c.en_cuarentena);
        if (primera) elegir(primera);
      })
      .catch((e) => {
        if (!ctl.signal.aborted) {
          setErrorCatalogo(mensajeDeError(e, "No se pudo leer el catálogo (¿sesión de admin?)."));
        }
      });
    return () => ctl.abort();
  }, [elegir]);

  const consulta = useMemo(
    () => catalogo?.consultas.find((c) => c.nombre === nombre) ?? null,
    [catalogo, nombre],
  );
  const grupos = useMemo(() => {
    const porGrupo = new Map<string, InvestigacionTikTokConsulta[]>();
    (catalogo?.consultas ?? []).forEach((c) => {
      porGrupo.set(c.grupo, [...(porGrupo.get(c.grupo) ?? []), c]);
    });
    return Array.from(porGrupo.entries());
  }, [catalogo]);
  const armado = useMemo(
    () => (consulta ? armarParams(consulta, valores, marcadas) : null),
    [consulta, valores, marcadas],
  );

  const consultar = useCallback(async () => {
    if (!consulta || !armado || !armado.ok || cargando || consulta.en_cuarentena) return;
    setCargando(true);
    setError(null);
    setResp(null);
    try {
      // Sólo viajan el NOMBRE de la consulta y sus parámetros: la ruta, el
      // método y la tienda los pone el backend.
      setResp(await investigarTikTok(consulta.nombre, armado.params));
    } catch (e) {
      setError(mensajeDeError(e, "La consulta falló."));
    } finally {
      setCargando(false);
    }
  }, [consulta, armado, cargando]);

  // El cursor sólo se ofrece si la respuesta a la vista es de ESTA consulta:
  // el de «Buscar pedidos» no pagina «Buscar paquetes».
  const respDeOtra = !!resp && !!consulta && resp.consulta !== consulta.nombre;
  const siguiente = resp?.ok && !respDeOtra ? cursorSiguiente(resp.result) : null;
  const paginable = consulta?.campos.some((f) => f.nombre === "page_token") ?? false;
  const llavesTapadas = resp?.llaves_redactadas ?? [];

  return (
    <>
      <Aviso tono="azul">
        {catalogo?.aviso
          ?? "Sólo lectura: se elige una consulta de una lista cerrada; aquí no se escribe ninguna ruta."}
        {catalogo && (
          <> Límite: {catalogo.limite.llamadas} consultas por {catalogo.limite.por_segundos} s
            entre todos (la cuota de TikTok es la misma que usa la operación); páginas de hasta{" "}
            {catalogo.pagina_max} resultados.</>
        )}
      </Aviso>

      {errorCatalogo && <Aviso tono="ambar">{errorCatalogo}</Aviso>}
      {catalogo && catalogo.abiertas_descartadas > 0 && (
        <Aviso tono="ambar">
          INVESTIGACION_TIKTOK_ABIERTAS trae {catalogo.abiertas_descartadas} nombre(s) que no
          están en el catálogo: no se abrió ninguna consulta en cuarentena.
        </Aviso>
      )}
      {catalogo && !catalogo.tiktok_configurado && (
        <Aviso tono="ambar">
          TikTok no está configurado en este ambiente: las consultas contestarán 503.
        </Aviso>
      )}

      <section className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        {/* ── Consulta ── */}
        <form
          className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5"
          onSubmit={(e) => {
            e.preventDefault();
            void consultar();
          }}
        >
          <label className="block text-sm">
            <span className="text-zinc-400">Consulta</span>
            <select
              value={nombre}
              onChange={(e) => {
                const c = catalogo?.consultas.find((x) => x.nombre === e.target.value);
                if (c) elegir(c);
              }}
              className={ESTILO_CAMPO}
            >
              {!catalogo && (
                <option value="">
                  {errorCatalogo ? "— no se pudo leer el catálogo —" : "— cargando el catálogo —"}
                </option>
              )}
              {grupos.map(([grupo, lista]) => (
                <optgroup key={grupo} label={grupo}>
                  {lista.map((c) => (
                    <option key={c.nombre} value={c.nombre}>
                      {c.titulo}{c.en_cuarentena ? " (en cuarentena)" : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>

          {consulta && (
            <div className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 text-xs">
              <p className="text-sm text-zinc-200">{consulta.pregunta}</p>
              <p className="font-mono text-zinc-500">
                <span className="text-zinc-300">{consulta.metodo}</span> {consulta.ruta}
                <span className="ml-2 rounded bg-zinc-800 px-1.5 py-0.5 font-sans text-zinc-400">
                  {consulta.estado}
                </span>
              </p>
              {consulta.nota && <p className="text-zinc-500">{consulta.nota}</p>}
            </div>
          )}

          {consulta && consulta.campos.length === 0 && (
            <p className="text-xs text-zinc-500">Esta consulta no lleva parámetros.</p>
          )}
          {consulta?.campos.map((f) => (
            <CampoTikTok
              key={`${consulta.nombre}.${f.nombre}`}
              campo={f}
              valor={valores[f.nombre] ?? ""}
              marcadas={marcadas[f.nombre] ?? []}
              onValor={(v) => setValores((antes) => ({ ...antes, [f.nombre]: v }))}
              onMarcadas={(v) => setMarcadas((antes) => ({ ...antes, [f.nombre]: v }))}
            />
          ))}

          {armado && !armado.ok && (
            <span className="block text-xs text-amber-400">{armado.error}</span>
          )}
          {consulta?.en_cuarentena && (
            <span className="block text-xs text-amber-400">
              Esta consulta está en cuarentena y el backend la rechaza.
            </span>
          )}

          <button
            type="submit"
            disabled={cargando || !consulta || !armado || !armado.ok || consulta.en_cuarentena}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2
                       text-sm font-medium hover:bg-indigo-500 disabled:opacity-50"
          >
            {cargando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Consultar
          </button>
          {siguiente && paginable && (
            <button
              type="button"
              onClick={() => setValores((antes) => ({ ...antes, page_token: siguiente }))}
              className="ml-3 rounded-lg bg-zinc-800 px-3 py-2 text-xs hover:bg-zinc-700"
            >
              Poner el cursor de la página siguiente
            </button>
          )}

          {catalogo && (
            <details className="text-xs text-zinc-400">
              <summary className="cursor-pointer text-zinc-300">
                Lo que esta página NO puede hacer ({catalogo.vetadas.length} rutas vetadas)
              </summary>
              <ul className="mt-2 max-h-72 space-y-1 overflow-auto">
                {catalogo.vetadas.map((v) => (
                  <li key={`${v.metodo} ${v.ruta}`}>
                    <code className="text-zinc-300">{v.metodo} {v.ruta}</code> — {v.que_hace}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {catalogo && (
            <details className="text-xs text-zinc-400">
              <summary className="cursor-pointer text-zinc-300">Los candados</summary>
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-zinc-950 p-3">
                {JSON.stringify(catalogo.regla, null, 2)}
              </pre>
            </details>
          )}
          {catalogo && (
            <details className="text-xs text-zinc-400">
              <summary className="cursor-pointer text-zinc-300">Qué significa cada código</summary>
              <dl className="mt-2 space-y-1">
                {Object.entries(catalogo.codigos).map(([cod, txt]) => (
                  <div key={cod} className="flex gap-3">
                    <dt className="w-24 shrink-0 font-mono text-zinc-300">{cod}</dt>
                    <dd>{txt}</dd>
                  </div>
                ))}
              </dl>
            </details>
          )}
        </form>

        {/* ── Respuesta ── */}
        <PanelRespuesta resp={resp} error={error} cargando={cargando}
                        esperando="Consultando a TikTok…">
          {respDeOtra && resp && (
            <p className="mb-3 text-xs text-zinc-400">
              Esta respuesta es de la consulta anterior (<code>{resp.consulta}</code>), no de la
              que está elegida.
            </p>
          )}
          {resp?.error && (
            <p className="mb-3 text-sm text-amber-200">{resp.error}</p>
          )}
          {resp?.error?.includes("[…]") && (
            <p className="mb-3 text-xs text-zinc-500">
              «[…]» = palabras del mensaje de TikTok que el servidor tapó: no están en su
              vocabulario de mensajes de API y podrían citar a alguien.
            </p>
          )}
          {llavesTapadas.length > 0 && (
            <details className="mb-3 text-xs text-zinc-400">
              <summary className="cursor-pointer text-zinc-300">
                Campos que salieron tapados ({llavesTapadas.length})
              </summary>
              <p className="mt-2 flex flex-wrap gap-1.5">
                {llavesTapadas.map((k) => (
                  <code key={k} className="rounded bg-zinc-800 px-1.5 py-0.5">{k}</code>
                ))}
              </p>
            </details>
          )}
        </PanelRespuesta>
      </section>
    </>
  );
}

// ── La página ────────────────────────────────────────────────────────────────

export default function InvestigacionPage() {
  const [canal, setCanal] = useState<Canal>("temu");
  // Cada panel se monta la primera vez que se abre y ya no se desmonta: cambiar
  // de canal no borra lo que se estaba consultando en el otro.
  const [visto, setVisto] = useState<Record<Canal, boolean>>({ temu: false, tiktok: false });

  useEffect(() => {
    const pedido = new URLSearchParams(window.location.search).get("canal");
    const inicial: Canal = pedido === "tiktok" ? "tiktok" : "temu";
    setCanal(inicial);
    setVisto((v) => ({ ...v, [inicial]: true }));
  }, []);

  const elegirCanal = (c: Canal) => {
    setCanal(c);
    setVisto((v) => ({ ...v, [c]: true }));
    const url = new URL(window.location.href);
    if (c === "temu") url.searchParams.delete("canal");
    else url.searchParams.set("canal", c);
    window.history.replaceState(null, "", url.toString());
  };

  const actual = CANALES.find((x) => x.id === canal) ?? CANALES[0];

  return (
    <>
      <AppNavbar />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <header className="mb-6">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <FlaskConical className="h-6 w-6 text-indigo-400" />
            Investigación · {actual.nombre}
          </h1>
          <p className="mt-1 text-sm text-zinc-400">{actual.resumen}</p>
          <div className="mt-4 inline-flex rounded-lg border border-zinc-800 bg-zinc-900/50 p-1"
               role="tablist" aria-label="Canal">
            {CANALES.map((x) => (
              <button
                key={x.id}
                type="button"
                role="tab"
                aria-selected={canal === x.id}
                onClick={() => elegirCanal(x.id)}
                className={`rounded-md px-4 py-1.5 text-sm font-medium ${canal === x.id
                  ? "bg-indigo-600 text-white"
                  : "text-zinc-400 hover:text-zinc-200"}`}
              >
                {x.nombre}
              </button>
            ))}
          </div>
        </header>

        <div className={canal === "temu" ? "" : "hidden"}>{visto.temu && <PanelTemu />}</div>
        <div className={canal === "tiktok" ? "" : "hidden"}>{visto.tiktok && <PanelTikTok />}</div>
      </main>
    </>
  );
}
