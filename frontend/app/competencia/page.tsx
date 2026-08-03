"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Loader2,
  AlertTriangle,
  ExternalLink,
  Eye,
  Trophy,
  Play,
  Info,
  Pencil,
  Check,
  X,
  ChevronRight,
  Search,
  Target,
  Crown,
  Sparkles,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import {
  corregirTerminoCompetencia,
  correrCompetencia,
  corridaCompetencia,
  detalleCompetencia,
  estadoCompetencia,
  tablaCompetencia,
} from "@/lib/api";
import type {
  CompetenciaCategoriaGrupo,
  CompetenciaCorrida,
  CompetenciaDetalle,
  CompetenciaEstado,
  CompetenciaFilaSku,
  CompetenciaResultado,
  TipoCompetencia,
} from "@/lib/types";

const COLOR = "#4F46E5";

const mxn = (v: number | null | undefined) =>
  v === null || v === undefined
    ? "—"
    : new Intl.NumberFormat("es-MX", {
        style: "currency",
        currency: "MXN",
        maximumFractionDigits: 0,
      }).format(v);

const num = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : new Intl.NumberFormat("es-MX").format(v);

const mes = (p: string | null | undefined) =>
  !p
    ? "—"
    : new Date(`${p.slice(0, 10)}T12:00:00`).toLocaleDateString("es-MX", {
        month: "long",
        year: "numeric",
      });

// Las tres mediciones y qué pregunta responde cada una.
const TIPOS: { id: TipoCompetencia; label: string; pregunta: string; icon: typeof Search }[] = [
  {
    id: "general",
    label: "Búsqueda general",
    pregunta: "¿Me encuentran cuando buscan el tipo de producto?",
    icon: Search,
  },
  {
    id: "titulo",
    label: "Competencia directa",
    pregunta: "¿Dónde quedo contra el mismo producto?",
    icon: Target,
  },
  {
    id: "categoria",
    label: "Top de la categoría",
    pregunta: "¿Quiénes son los más vendidos de mi categoría?",
    icon: Crown,
  },
];

/** Posición como medalla: lo primero que el ojo busca en la tabla. */
function Posicion({ pos, total }: { pos: number | null; total: number | null }) {
  if (pos === null || pos === undefined)
    return (
      <span className="text-xs text-slate-400" title="No apareces en esta medición">
        fuera
      </span>
    );
  const tono =
    pos <= 3
      ? "bg-emerald-100 text-emerald-800"
      : pos <= 10
        ? "bg-amber-100 text-amber-800"
        : "bg-slate-100 text-slate-600";
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className={`rounded px-1.5 py-0.5 text-xs font-bold tabular-nums ${tono}`}>
        #{pos}
      </span>
      {total ? <span className="text-[10px] text-slate-400">/{total}</span> : null}
    </span>
  );
}

export default function CompetenciaPage() {
  const [tabla, setTabla] = useState<CompetenciaCategoriaGrupo[]>([]);
  const [corrida, setCorrida] = useState<CompetenciaCorrida | null>(null);
  const [estado, setEstado] = useState<CompetenciaEstado | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [enCurso, setEnCurso] = useState(false);

  // Detalle desplegado: SKU + qué medición se está viendo.
  const [abierto, setAbierto] = useState<string | null>(null);
  const [tipoVista, setTipoVista] = useState<TipoCompetencia>("general");
  const [detalle, setDetalle] = useState<CompetenciaDetalle | null>(null);
  const [cargandoDetalle, setCargandoDetalle] = useState(false);

  // Edición del término general.
  const [editando, setEditando] = useState<string | null>(null);
  const [borrador, setBorrador] = useState("");

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const cargar = useCallback(async (signal?: AbortSignal) => {
    setCargando(true);
    setError(null);
    try {
      const r = await tablaCompetencia(signal);
      setTabla(r.categorias);
      setCorrida(r.corrida);
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo cargar la tabla.");
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void cargar(ac.signal);
    estadoCompetencia(ac.signal).then(setEstado).catch(() => {});
    return () => ac.abort();
  }, [cargar]);

  useEffect(
    () => () => {
      if (pollRef.current) clearInterval(pollRef.current);
    },
    [],
  );

  // ── Detalle de un SKU ────────────────────────────────────────────────
  const abrir = async (sku: string, tipo: TipoCompetencia) => {
    if (abierto === sku && tipoVista === tipo) {
      setAbierto(null);
      return;
    }
    setAbierto(sku);
    setTipoVista(tipo);
    setCargandoDetalle(true);
    try {
      setDetalle(await detalleCompetencia(sku));
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo cargar el detalle.");
      setDetalle(null);
    } finally {
      setCargandoDetalle(false);
    }
  };

  // ── Término general ──────────────────────────────────────────────────
  const guardarTermino = async (sku: string) => {
    const t = borrador.trim();
    if (!t) return;
    try {
      await corregirTerminoCompetencia(sku, t);
      setEditando(null);
      await cargar();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo guardar el término.");
    }
  };

  // ── Corrida manual ───────────────────────────────────────────────────
  const correr = async () => {
    setError(null);
    setEnCurso(true);
    try {
      await correrCompetencia();
      pollRef.current = setInterval(async () => {
        const r = await corridaCompetencia();
        if (r.en_curso?.estado !== "corriendo") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setEnCurso(false);
          if (r.en_curso?.error) setError(r.en_curso.error);
          await cargar();
        }
      }, 5000);
    } catch (e) {
      setEnCurso(false);
      setError(e instanceof Error ? e.message : "No se pudo arrancar la corrida.");
    }
  };

  const totalSkus = tabla.reduce((a, c) => a + c.skus.length, 0);

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />

      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
        {/* Encabezado */}
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight text-slate-900">
              <Trophy size={20} style={{ color: COLOR }} />
              Competencia
            </h1>
            <p className="mt-0.5 max-w-3xl text-sm text-slate-500">
              Foto <strong>mensual</strong> de Mercado Libre: {totalSkus} SKUs en{" "}
              {tabla.length} categorías. No se guarda histórico — cada corrida
              reemplaza la anterior.
            </p>
          </div>

          <div className="flex items-center gap-2">
            {corrida && (
              <div className="text-right text-xs text-slate-500">
                <div>
                  Última medición: <strong>{mes(corrida.periodo)}</strong>
                </div>
                <div>
                  {corrida.resultados} publicaciones · {corrida.visitas_ok} con visitas
                  {corrida.costo_apify_usd ? ` · $${corrida.costo_apify_usd} USD` : ""}
                </div>
              </div>
            )}
            <button
              onClick={correr}
              disabled={enCurso}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              title="En producción esto lo dispara el cron mensual de Railway"
            >
              {enCurso ? (
                <Loader2 size={15} className="animate-spin" />
              ) : (
                <Play size={15} />
              )}
              Medir ahora
            </button>
          </div>
        </div>

        {/* Estado de las fuentes */}
        {estado && (!estado.supabase || !estado.scraper_apify) && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <div>
              {!estado.supabase && (
                <div>
                  <code>SUPABASE_DB_URL</code> no está configurada: no hay dónde
                  guardar la corrida.
                </div>
              )}
              {!estado.scraper_apify && (
                <div>
                  <code>APIFY_API_KEY</code> ausente: sin título, precio, imagen ni
                  descripción de la competencia (la API de ML los niega con 403).
                </div>
              )}
            </div>
          </div>
        )}

        {enCurso && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm text-indigo-800">
            <Loader2 size={15} className="animate-spin" />
            Midiendo. Cada SKU lanza búsquedas de Apify y una llamada de visitas por
            publicación; tarda varios minutos.
          </div>
        )}

        {corrida?.avisos && corrida.avisos.length > 0 && (
          <details className="mb-4 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
            <summary className="cursor-pointer font-medium text-slate-700">
              {corrida.avisos.length} avisos de la última medición
            </summary>
            <div className="mt-2 space-y-1">
              {corrida.avisos.map((a, i) => (
                <div key={i} className="flex items-start gap-1.5">
                  <Info size={13} className="mt-0.5 shrink-0 text-slate-400" />
                  {a}
                </div>
              ))}
            </div>
          </details>
        )}

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
            <AlertTriangle size={15} /> {error}
          </div>
        )}

        {cargando && (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
            <Loader2 size={18} className="animate-spin" /> Cargando…
          </div>
        )}

        {!cargando && tabla.length === 0 && (
          <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
            No hay SKUs vigilados todavía. Corre la siembra:
            <code className="mx-1 rounded bg-slate-100 px-1.5 py-0.5">
              python scripts/competencia_cron.py --sembrar --dry-run
            </code>
          </div>
        )}

        {/* ── Tabla por categoría, con sus SKUs dentro ───────────────── */}
        {!cargando &&
          tabla.map((cat) => (
            <section
              key={cat.categoria_id ?? "sin"}
              className="mb-5 overflow-hidden rounded-xl border border-slate-200 bg-white"
            >
              <header className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-2.5">
                <h2 className="text-sm font-semibold text-slate-800">
                  {cat.categoria_nombre}
                  {cat.categoria_id && (
                    <span className="ml-2 font-mono text-xs font-normal text-slate-400">
                      {cat.categoria_id}
                    </span>
                  )}
                </h2>
                <span className="text-xs text-slate-500">
                  {cat.skus.length} {cat.skus.length === 1 ? "SKU" : "SKUs"}
                </span>
              </header>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-3 py-2">SKU / producto</th>
                      <th className="w-56 px-3 py-2">Término general</th>
                      <th className="w-28 px-3 py-2 text-center" title="¿Me encuentran cuando buscan el tipo de producto?">
                        Búsq. general
                      </th>
                      <th className="w-28 px-3 py-2 text-center" title="¿Dónde quedo contra el mismo producto?">
                        Directa
                      </th>
                      <th className="w-28 px-3 py-2 text-center" title="Ranking oficial de más vendidos de la categoría">
                        Top categoría
                      </th>
                      <th className="w-28 px-3 py-2 text-right">Mi precio</th>
                      <th className="w-28 px-3 py-2 text-right" title="Mediana de precio de los rivales en la búsqueda general">
                        Mediana riv.
                      </th>
                      <th className="w-24 px-3 py-2 text-right">Visitas 30d</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {cat.skus.map((s) => (
                      <FilaSku
                        key={s.sku}
                        s={s}
                        abierto={abierto === s.sku}
                        editando={editando === s.sku}
                        borrador={borrador}
                        onEditar={() => {
                          setEditando(s.sku);
                          setBorrador(s.termino_general ?? "");
                        }}
                        onCancelar={() => setEditando(null)}
                        onBorrador={setBorrador}
                        onGuardar={() => guardarTermino(s.sku)}
                        onAbrir={(tipo) => abrir(s.sku, tipo)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Detalle del SKU abierto, si pertenece a esta categoría */}
              {abierto && cat.skus.some((s) => s.sku === abierto) && (
                <Detalle
                  detalle={detalle}
                  cargando={cargandoDetalle}
                  tipo={tipoVista}
                  onTipo={setTipoVista}
                />
              )}
            </section>
          ))}
      </main>
    </div>
  );
}

// ── Fila de SKU ────────────────────────────────────────────────────────

function FilaSku({
  s,
  abierto,
  editando,
  borrador,
  onEditar,
  onCancelar,
  onBorrador,
  onGuardar,
  onAbrir,
}: {
  s: CompetenciaFilaSku;
  abierto: boolean;
  editando: boolean;
  borrador: string;
  onEditar: () => void;
  onCancelar: () => void;
  onBorrador: (v: string) => void;
  onGuardar: () => void;
  onAbrir: (t: TipoCompetencia) => void;
}) {
  return (
    <tr className={abierto ? "bg-indigo-50/40" : "hover:bg-slate-50"}>
      <td className="px-3 py-2">
        <div className="flex items-start gap-1.5">
          <ChevronRight
            size={14}
            className={`mt-0.5 shrink-0 text-slate-400 transition ${abierto ? "rotate-90" : ""}`}
          />
          <div className="min-w-0">
            <div className="font-mono text-xs font-semibold text-slate-700">{s.sku}</div>
            <div className="line-clamp-1 text-slate-600">{s.nombre}</div>
          </div>
        </div>
      </td>

      {/* Término general: propuesto por IA, corregible a mano */}
      <td className="px-3 py-2">
        {editando ? (
          <div className="flex items-center gap-1">
            <input
              value={borrador}
              onChange={(e) => onBorrador(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onGuardar();
                if (e.key === "Escape") onCancelar();
              }}
              className="w-40 rounded border border-slate-300 px-2 py-1 text-xs"
              autoFocus
            />
            <button onClick={onGuardar} className="text-emerald-600 hover:text-emerald-700">
              <Check size={14} />
            </button>
            <button onClick={onCancelar} className="text-slate-400 hover:text-slate-600">
              <X size={14} />
            </button>
          </div>
        ) : (
          <button
            onClick={onEditar}
            className="group inline-flex items-center gap-1.5 text-left"
            title="Editar el término general"
          >
            <span className={s.termino_general ? "text-slate-700" : "text-slate-400 italic"}>
              {s.termino_general || "sin término"}
            </span>
            {s.termino_origen === "ia" && s.termino_general && (
              <span title="Propuesto por IA — edítalo y queda fijo">
                <Sparkles size={11} className="text-indigo-400" />
              </span>
            )}
            <Pencil
              size={11}
              className="opacity-0 transition group-hover:opacity-100 text-slate-400"
            />
          </button>
        )}
      </td>

      <td className="px-3 py-2 text-center">
        <button onClick={() => onAbrir("general")} className="hover:opacity-70">
          <Posicion pos={s.pos_general} total={s.total_general} />
        </button>
      </td>
      <td className="px-3 py-2 text-center">
        <button onClick={() => onAbrir("titulo")} className="hover:opacity-70">
          <Posicion pos={s.pos_titulo} total={s.total_titulo} />
        </button>
      </td>
      <td className="px-3 py-2 text-center">
        <button onClick={() => onAbrir("categoria")} className="hover:opacity-70">
          <Posicion pos={s.pos_categoria} total={s.total_categoria} />
        </button>
      </td>

      <td className="px-3 py-2 text-right tabular-nums">{mxn(s.mi_precio)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-slate-500">
        {mxn(s.mediana_general)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {s.visitas_general === null || s.visitas_general === undefined ? (
          "—"
        ) : (
          <span className="inline-flex items-center gap-1">
            <Eye size={11} className="text-slate-400" />
            {num(s.visitas_general)}
          </span>
        )}
      </td>
    </tr>
  );
}

// ── Detalle: las publicaciones de una medición ─────────────────────────

function Detalle({
  detalle,
  cargando,
  tipo,
  onTipo,
}: {
  detalle: CompetenciaDetalle | null;
  cargando: boolean;
  tipo: TipoCompetencia;
  onTipo: (t: TipoCompetencia) => void;
}) {
  if (cargando)
    return (
      <div className="flex items-center gap-2 border-t border-slate-200 px-4 py-6 text-sm text-slate-500">
        <Loader2 size={15} className="animate-spin" /> Cargando detalle…
      </div>
    );
  if (!detalle) return null;

  const filas: CompetenciaResultado[] = detalle.resultados?.[tipo] ?? [];
  const meta = TIPOS.find((t) => t.id === tipo)!;
  const pos = detalle.posiciones.find((p) => p.tipo === tipo);

  return (
    <div className="border-t-2 border-indigo-200 bg-slate-50/60">
      {/* Selector de medición */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        {TIPOS.map((t) => {
          const Icono = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => onTipo(t.id)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                tipo === t.id
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              <Icono size={13} />
              {t.label}
            </button>
          );
        })}
        <span className="ml-2 text-xs text-slate-500">{meta.pregunta}</span>
      </div>

      {pos && (
        <div className="px-4 pb-2 text-xs text-slate-600">
          {pos.termino && (
            <>
              Búsqueda: <strong>«{pos.termino}»</strong> ·{" "}
            </>
          )}
          {pos.mi_posicion
            ? `apareces en la posición #${pos.mi_posicion} de ${pos.total_resultados}`
            : `no apareces entre los ${pos.total_resultados} resultados`}
        </div>
      )}

      {filas.length === 0 ? (
        <div className="px-4 py-6 text-sm text-slate-500">
          Sin resultados para esta medición.
          {tipo === "categoria" &&
            " Mercado Libre no publica ranking de más vendidos para todas las categorías."}
        </div>
      ) : (
        <div className="overflow-x-auto px-2 pb-3">
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="w-10 px-2 py-1.5">#</th>
                <th className="w-14 px-2 py-1.5">Foto</th>
                <th className="px-2 py-1.5">Título / descripción</th>
                <th className="w-28 px-2 py-1.5 text-right">Precio</th>
                <th className="w-24 px-2 py-1.5 text-right">Visitas 30d</th>
                <th className="w-20 px-2 py-1.5 text-right">Vendidos</th>
                <th className="w-36 px-2 py-1.5">Vendedor</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200/70">
              {filas.map((r) => (
                <tr
                  key={r.id ?? r.externo_id}
                  className={r.es_nuestro ? "bg-indigo-100/70 font-medium" : "bg-white"}
                >
                  <td className="px-2 py-1.5 font-mono text-xs text-slate-400">
                    {r.posicion ?? "—"}
                  </td>
                  <td className="px-2 py-1.5">
                    {r.imagen ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={r.imagen} alt="" className="h-9 w-9 rounded object-cover" />
                    ) : (
                      <div className="flex h-9 w-9 items-center justify-center rounded bg-slate-100 text-[8px] text-slate-400">
                        s/foto
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    {r.url ? (
                      <a
                        href={r.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-start gap-1 text-slate-800 hover:text-indigo-600"
                      >
                        <span className="line-clamp-1">{r.titulo || r.externo_id}</span>
                        <ExternalLink size={11} className="mt-0.5 shrink-0 opacity-50" />
                      </a>
                    ) : (
                      <span className="text-slate-500">
                        {r.titulo || r.externo_id}
                        {!r.titulo && (
                          <span className="ml-1 text-[10px] text-slate-400">
                            (la API de ML no permite leer esta publicación)
                          </span>
                        )}
                      </span>
                    )}
                    {r.descripcion && (
                      <div className="line-clamp-1 text-[11px] text-slate-500">
                        {r.descripcion}
                      </div>
                    )}
                    {r.es_nuestro && (
                      <span className="mt-0.5 inline-block rounded bg-indigo-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                        NUESTRO {r.sku_nuestro}
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{mxn(r.precio)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {num(r.visitas_30d)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-600">
                    {num(r.vendidos)}
                  </td>
                  <td className="truncate px-2 py-1.5 text-slate-600">
                    {r.seller || r.marca || "—"}
                    {r.es_full && (
                      <span className="ml-1 rounded bg-emerald-600 px-1 py-0.5 text-[9px] text-white">
                        FULL
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-2 pt-2 text-[11px] text-slate-400">
            La descripción es la corta derivada de atributos; Mercado Libre no expone
            el texto largo de publicaciones ajenas. Las visitas vienen de la API de ML
            y las unidades vendidas del scraper.
          </p>
        </div>
      )}
    </div>
  );
}
