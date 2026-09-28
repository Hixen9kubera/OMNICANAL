"use client";

/**
 * /radar — Radar de precios (F1: solo Mercado Libre, SOLO LECTURA).
 *
 * Compara el precio cobrado de cada SKU contra el mismo producto en el mercado
 * y contra lo que la venta deja (contribución por pieza). No cambia ningún
 * precio: las propuestas llegan en F6. El costo del producto no entra.
 *
 * Oculta: solo admin. Tres capas, de fuera hacia dentro — el backend
 * (`solo_admin` + core/rbac.py) es la autoridad; `AccesoRadar` no monta nada
 * si el rol no es admin; y un 401/403 de la API también se pinta como
 * "No disponible" en vez de como error.
 *
 * Los filtros viven en la URL (se pueden compartir y el "atrás" del navegador
 * los conserva). La paginación, el orden y los conteos los hace el servidor.
 */

import { useCallback, useEffect, useState } from "react";
import { Search } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import Pagination from "@/components/Pagination";
import AccesoRadar from "@/components/radar/Acceso";
import { FranjaCompletitud, ParametrosRadar } from "@/components/radar/Completitud";
import { TablaRadar, TarjetasDireccion } from "@/components/radar/TablaRadar";
import {
  CUENTAS_ML,
  DIRECCIONES,
  ETIQUETA_CLASE,
  LLAVE_FILTROS_RADAR,
  META_DIRECCION,
  entero,
  etiquetaContenedor,
  fechaHora,
  fraccionComoPct,
  num,
} from "@/components/radar/formato";
import {
  AvisoSoloLectura,
  CajaError,
  Cargando,
  NoDisponible,
  SubnavRadar,
} from "@/components/radar/ui";
import {
  ApiError,
  mensajeDeError,
  radarPrecios,
  type RadarClase,
  type RadarDireccion,
  type RadarListaResp,
} from "@/lib/api";

const LIMITE = 100;

interface Filtros {
  direccion: RadarDireccion | null;
  cuenta: string;
  clase: RadarClase | "";
  contenedor: string;
  q: string;
  pagina: number;
}

const VACIOS: Filtros = { direccion: null, cuenta: "", clase: "", contenedor: "", q: "", pagina: 1 };

function leerFiltros(qs: URLSearchParams): Filtros {
  const d = qs.get("direccion");
  const c = qs.get("clase");
  const pag = Number(qs.get("pagina") || 1);
  return {
    direccion: d && (DIRECCIONES as string[]).includes(d) ? (d as RadarDireccion) : null,
    cuenta: qs.get("cuenta") || "",
    clase: c && c in ETIQUETA_CLASE ? (c as RadarClase) : "",
    contenedor: qs.get("contenedor") || "",
    q: qs.get("q") || "",
    pagina: Number.isInteger(pag) && pag > 0 ? pag : 1,
  };
}

function escribirFiltros(f: Filtros): string {
  const qs = new URLSearchParams();
  if (f.direccion) qs.set("direccion", f.direccion);
  if (f.cuenta) qs.set("cuenta", f.cuenta);
  if (f.clase) qs.set("clase", f.clase);
  if (f.contenedor) qs.set("contenedor", f.contenedor);
  if (f.q) qs.set("q", f.q);
  if (f.pagina > 1) qs.set("pagina", String(f.pagina));
  return qs.toString();
}

export default function RadarPage() {
  return (
    <>
      <AppNavbar />
      <AccesoRadar>
        <RadarLista />
      </AccesoRadar>
    </>
  );
}

function RadarLista() {
  // `null` hasta leer la URL: así la primera petición ya lleva los filtros.
  const [f, setF] = useState<Filtros | null>(null);
  const [texto, setTexto] = useState("");
  const [datos, setDatos] = useState<RadarListaResp | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noDisponible, setNoDisponible] = useState(false);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    const inicial = leerFiltros(new URLSearchParams(window.location.search));
    setF(inicial);
    setTexto(inicial.q);
  }, []);

  // La búsqueda espera a que se deje de teclear.
  useEffect(() => {
    if (!f) return;
    const limpio = texto.trim();
    if (limpio === f.q) return;
    const t = setTimeout(() => setF((prev) => (prev ? { ...prev, q: limpio, pagina: 1 } : prev)), 350);
    return () => clearTimeout(t);
  }, [texto, f]);

  // Los filtros a la URL (sin navegar) y a la sesión, para el "Volver".
  useEffect(() => {
    if (!f) return;
    const qs = escribirFiltros(f);
    window.history.replaceState(window.history.state, "", qs ? `?${qs}` : window.location.pathname);
    try {
      window.sessionStorage.setItem(LLAVE_FILTROS_RADAR, qs);
    } catch {
      // Sin almacenamiento (ventana privada, bloqueado): el Volver cae a /radar.
    }
  }, [f]);

  useEffect(() => {
    if (!f) return;
    const ctl = new AbortController();
    setCargando(true);
    setError(null);
    radarPrecios(
      {
        cuenta: f.cuenta || null,
        clase: f.clase || null,
        direccion: f.direccion,
        contenedor: f.contenedor || null,
        q: f.q || null,
        limite: LIMITE,
        pagina: f.pagina,
      },
      ctl.signal,
    )
      .then((d) => {
        if (!ctl.signal.aborted) setDatos(d);
      })
      .catch((e) => {
        if (ctl.signal.aborted) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setNoDisponible(true);
          return;
        }
        setError(mensajeDeError(e, "No se pudo leer el radar. Revisa que el backend esté arriba."));
      })
      .finally(() => {
        if (!ctl.signal.aborted) setCargando(false);
      });
    return () => ctl.abort();
  }, [f, intento]);

  const cambiar = useCallback((parcial: Partial<Filtros>) => {
    setF((prev) => (prev ? { ...prev, pagina: 1, ...parcial } : prev));
  }, []);

  const limpiar = useCallback(() => {
    setTexto("");
    setF({ ...VACIOS });
  }, []);

  if (noDisponible) return <NoDisponible />;

  const hayFiltro = !!f && (!!f.direccion || !!f.cuenta || !!f.clase || !!f.contenedor || !!f.q);
  const total = datos?.total ?? 0;
  const limite = datos?.limite || LIMITE;
  const pagina = datos?.pagina || f?.pagina || 1;
  const totalPaginas = Math.max(1, Math.ceil(total / limite));
  const contenedores = datos?.contenedores ?? [];
  const pasoMax = fraccionComoPct(num(datos?.parametros?.paso_max));
  const items = datos?.items ?? [];

  let resumen = "";
  if (datos) {
    const base = `${entero(total)} SKU${total === 1 ? "" : "s"}`;
    if (f?.direccion) resumen = `${META_DIRECCION[f.direccion].etiqueta} · ${base}`;
    else if (typeof datos.con_referencia === "number") {
      resumen = `${base} con publicación activa · ${entero(datos.con_referencia)} con referencia de mercado`;
    } else resumen = `${base} con publicación activa`;
  }

  return (
    <>
      <SubnavRadar ambiente={datos?.ambiente} />
      <main className="mx-auto flex max-w-[1800px] flex-col gap-5 px-4 py-6 sm:px-6">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:gap-6">
          <div className="flex flex-1 flex-col gap-1.5">
            <h1 className="text-[26px] font-bold tracking-tight text-slate-900">Radar de precios</h1>
            <p className="max-w-[820px] text-[15px] text-[#4A5163]">
              Mercado Libre · publicaciones activas de BEKURA y SANCOR. Cada precio se compara contra el
              mismo producto en el mercado y contra lo que la venta deja; el costo del producto no entra.
            </p>
          </div>
          <div className="flex flex-col items-start gap-1 lg:items-end">
            <AvisoSoloLectura />
            {datos?.generado_en && (
              <span className="text-xs text-slate-400">Datos al {fechaHora(datos.generado_en)}</span>
            )}
          </div>
        </div>

        <FranjaCompletitud completitud={datos?.completitud} />

        <TarjetasDireccion
          conteos={datos?.conteos}
          activa={f?.direccion ?? null}
          parametros={datos?.parametros}
          onElegir={(d) => cambiar({ direccion: d })}
        />

        <div className="flex flex-wrap items-end gap-3.5">
          <Selector
            id="f-cuenta"
            etiqueta="Cuenta"
            valor={f?.cuenta ?? ""}
            onCambio={(v) => cambiar({ cuenta: v })}
            opciones={[{ valor: "", etiqueta: "Las dos" }, ...CUENTAS_ML.map((c) => ({ valor: c.valor, etiqueta: c.etiqueta }))]}
          />
          <Selector
            id="f-clase"
            etiqueta="Clase del SKU"
            valor={f?.clase ?? ""}
            onCambio={(v) => cambiar({ clase: v as RadarClase | "" })}
            opciones={[
              { valor: "", etiqueta: "Todas" },
              ...(Object.keys(ETIQUETA_CLASE) as RadarClase[]).map((c) => ({ valor: c, etiqueta: ETIQUETA_CLASE[c] })),
            ]}
          />
          <Selector
            id="f-cont"
            etiqueta="Contenedor"
            valor={f?.contenedor ?? ""}
            onCambio={(v) => cambiar({ contenedor: v })}
            deshabilitado={contenedores.length === 0 && !f?.contenedor}
            opciones={[
              { valor: "", etiqueta: contenedores.length === 0 && datos ? "Sin datos de contenedor" : "Todos" },
              ...contenedores.map((c) => ({ valor: c, etiqueta: etiquetaContenedor(c) ?? c })),
              // Un contenedor de la URL que ya no viene en la lista se conserva visible.
              ...(f?.contenedor && !contenedores.includes(f.contenedor)
                ? [{ valor: f.contenedor, etiqueta: etiquetaContenedor(f.contenedor) ?? f.contenedor }]
                : []),
            ]}
          />
          <div className="flex w-full flex-col gap-1 sm:w-[300px]">
            <label htmlFor="f-buscar" className="text-xs font-semibold text-[#4A5163]">Buscar</label>
            <div className="relative">
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden />
              <input
                id="f-buscar"
                type="search"
                value={texto}
                onChange={(e) => setTexto(e.target.value)}
                placeholder="SKU o título"
                className="h-[38px] w-full rounded-lg border border-[#D0D5DD] bg-white pl-9 pr-3 text-sm text-slate-900 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
              />
            </div>
          </div>
          <div className="flex-1" />
          <div className="text-right text-[13px] leading-normal text-[#4A5163]">
            Se decide por SKU con la cuenta que más vende
            {pasoMax && (
              <>
                <br />
                Paso máximo ±{pasoMax}
              </>
            )}
          </div>
        </div>

        {error && <CajaError mensaje={error} onReintentar={() => setIntento((n) => n + 1)} />}

        <section aria-label="Publicaciones" className="overflow-hidden rounded-xl border border-[#E4E7EE] bg-white">
          <div className="flex flex-wrap items-center gap-3 border-b border-[#E4E7EE] px-4 py-3">
            <span className="font-semibold text-slate-900">{resumen || "Radar de precios"}</span>
            {hayFiltro && (
              <button
                type="button"
                onClick={limpiar}
                className="rounded px-2 py-1 text-[13px] font-semibold text-indigo-700 hover:bg-indigo-50"
              >
                Quitar filtros
              </button>
            )}
            {cargando && datos && <span className="text-xs text-slate-400">Actualizando…</span>}
            <div className="flex-1" />
            <span className="text-xs text-[#4A5163]">Ordenado por piezas en stock × contribución</span>
          </div>

          {cargando && !datos ? (
            <Cargando texto="Leyendo el radar…" />
          ) : !datos ? (
            <div className="px-4 py-12 text-center text-sm text-slate-500">Sin datos que mostrar.</div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center gap-2 px-4 py-14 text-center">
              <span className="text-sm font-semibold text-slate-700">
                {hayFiltro ? "Ningún SKU coincide con el filtro." : "El radar no tiene publicaciones activas de Mercado Libre."}
              </span>
              {hayFiltro && (
                <button type="button" onClick={limpiar} className="text-sm font-semibold text-indigo-700 hover:underline">
                  Quitar filtros
                </button>
              )}
            </div>
          ) : (
            <div className={cargando ? "opacity-60 transition-opacity" : "transition-opacity"}>
              <TablaRadar items={items} parametros={datos.parametros} />
            </div>
          )}
        </section>

        {datos && total > 0 && (
          <Pagination
            pag={{
              page: pagina,
              per_page: limite,
              total,
              total_pages: totalPaginas,
              tiene_anterior: pagina > 1,
              tiene_siguiente: pagina < totalPaginas,
            }}
            color="#4f46e5"
            textoColor="#ffffff"
            onPage={(p) => {
              setF((prev) => (prev ? { ...prev, pagina: p } : prev));
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
          />
        )}

        <ParametrosRadar parametros={datos?.parametros} />
      </main>
    </>
  );
}

function Selector({
  id,
  etiqueta,
  valor,
  opciones,
  onCambio,
  deshabilitado = false,
}: {
  id: string;
  etiqueta: string;
  valor: string;
  opciones: { valor: string; etiqueta: string }[];
  onCambio: (v: string) => void;
  deshabilitado?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-semibold text-[#4A5163]">{etiqueta}</label>
      <select
        id={id}
        value={valor}
        disabled={deshabilitado}
        onChange={(e) => onCambio(e.target.value)}
        className="h-[38px] min-w-[150px] rounded-lg border border-[#D0D5DD] bg-white px-2.5 text-sm text-slate-900 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
      >
        {opciones.map((o) => (
          <option key={`${o.valor}|${o.etiqueta}`} value={o.valor}>{o.etiqueta}</option>
        ))}
      </select>
    </div>
  );
}
