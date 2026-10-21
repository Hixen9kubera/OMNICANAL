"use client";

// Seguimiento.tsx — «¿Se movieron?» en PRICING (v0.678.0, pedido de Brandon 21-oct-2026): semana por semana,
// las publicaciones a las que se les aplicó un cambio (los descuentos por lote de «Hecho» y lo movido desde el
// panel) — piezas vendidas y visitas, y el antes / después de cada una. Solo lee.
//
// La semana en curso se compara por su RITMO (lo que lleva × 7 ÷ días transcurridos), no por su total: si no,
// un miércoles siempre parecería una caída.

import { useEffect, useMemo, useRef, useState } from "react";

import { mensajeDeError, pricingSeguimiento, type PricingSeguimiento, type PricingSeguimientoPub } from "@/lib/api";

const NOMBRE_CUENTA: Record<string, string> = { BEKURA: "Kubera", SANCORFASHION: "San Corpe" };
type Vista = "todas" | "vendio" | "visitas" | "quietas";
const VISTAS: [Vista, string][] = [
  ["todas", "Todas"],
  ["vendio", "Vendieron después"],
  ["visitas", "Más visitas"],
  ["quietas", "Sin movimiento"],
];
const TIPO: Record<string, string> = { descuento: "Descuento", precio: "Precio", deshacer: "Deshacer", oferta: "Oferta" };

const n0 = (x: number | null | undefined) => (x == null ? "—" : Math.round(x).toLocaleString("es-MX"));
const n1 = (x: number | null | undefined) => (x == null ? "—" : (Math.round(x * 10) / 10).toLocaleString("es-MX"));
const pesos = (x: number | null | undefined) => (x == null ? "—" : "$" + Math.round(x).toLocaleString("es-MX"));
const corta = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("es-MX", { day: "numeric", month: "short" });
const delta = (a: number | null, b: number | null) => (a == null || b == null || b === 0 ? null : a / b - 1);

/** El cambio contra antes. Si antes había 0 y ahora hay algo, eso es lo que importa: «de 0». */
function Delta({ d, a, b }: { d: number | null; a?: number | null; b?: number | null }) {
  if (d == null && b === 0 && (a ?? 0) > 0) return <span className="sg-sube">de 0</span>;
  if (d == null) return <span className="sm">—</span>;
  const t = (d > 0 ? "+" : "") + Math.round(d * 100) + " %";
  return <span className={d > 0.005 ? "sg-sube" : d < -0.005 ? "sg-baja" : "sm"}>{t}</span>;
}

/** Barras chiquitas de una serie semanal; la semana en curso, rayada. */
function Mini({ xs, enCurso, titulo }: { xs: (number | null)[]; enCurso: boolean; titulo: string }) {
  const max = Math.max(1, ...xs.map((x) => x ?? 0));
  return (
    <span className="sg-mini" title={titulo}>
      {xs.map((x, i) => (
        <span
          key={i}
          className={`sg-barra${x == null ? " nd" : ""}${enCurso && i === xs.length - 1 ? " curso" : ""}`}
          style={{ height: `${x ? Math.max(8, (x / max) * 100) : 4}%` }}
        />
      ))}
    </span>
  );
}

export default function Seguimiento({
  abierto,
  onCerrar,
  titulos,
}: {
  abierto: boolean;
  onCerrar: () => void;
  /** Título de cada publicación de la foto de PRICING (las que no estén ahí salen con su SKU). */
  titulos: Map<string, string>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [d, setD] = useState<PricingSeguimiento | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lecturas, setLecturas] = useState(0);
  const fresco = useRef(false);
  const [grupo, setGrupo] = useState("todos");
  const [cuenta, setCuenta] = useState("todas");
  const [vista, setVista] = useState<Vista>("todas");
  const [buscar, setBuscar] = useState("");

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (abierto && !dlg.open) dlg.showModal();
    if (!abierto && dlg.open) dlg.close();
  }, [abierto]);

  // Se lee al abrir; mientras ML entrega las visitas (en segundo plano), se vuelve a preguntar cada 5 s.
  useEffect(() => {
    if (!abierto) return;
    const ctl = new AbortController();
    let t: number | undefined;
    const leer = (f: boolean) => {
      pricingSeguimiento(f, ctl.signal)
        .then((x) => {
          if (ctl.signal.aborted) return;
          setD(x);
          setError(null);
          if (x.visitas.estado === "leyendo") t = window.setTimeout(() => leer(false), 5000);
        })
        .catch((e) => !ctl.signal.aborted && setError(mensajeDeError(e, "No se pudo leer el seguimiento.")));
    };
    const f = fresco.current;
    fresco.current = false;
    leer(f);
    return () => {
      ctl.abort();
      window.clearTimeout(t);
    };
  }, [abierto, lecturas]);

  const sems = d?.semanas ?? [];
  const ult = sems.length - 1;
  const q = buscar.trim().toLowerCase();
  const base = useMemo(
    () =>
      (d?.pubs ?? [])
        .filter((p) => grupo === "todos" || p.grupo === grupo)
        .filter((p) => cuenta === "todas" || p.cuenta === cuenta)
        .filter((p) => !q || (p.sku || "").toLowerCase().includes(q) || p.id.toLowerCase().includes(q) || (titulos.get(p.id) || "").toLowerCase().includes(q)),
    [d, grupo, cuenta, q, titulos],
  );
  const pasa = (p: PricingSeguimientoPub, v: Vista) =>
    v === "todas" ? true : v === "vendio" ? p.mov.vendio : v === "visitas" ? p.mov.mas_visitas === true : p.mov.sin_movimiento;
  const cuenta_ = (v: Vista) => base.filter((p) => pasa(p, v)).length;
  const filas = base
    .filter((p) => pasa(p, vista))
    .sort((a, b) => b.despues.piezas - a.despues.piezas || (b.despues.visitas_sem ?? 0) - (a.despues.visitas_sem ?? 0));

  // La tabla por semana, del conjunto que se está viendo.
  const porSemana = sems.map((s, k) => {
    const piezas = base.reduce((t, p) => t + (p.semanas.piezas[k] ?? 0), 0);
    const conVisitas = base.filter((p) => p.semanas.visitas[k] != null);
    const visitas = conVisitas.length ? conVisitas.reduce((t, p) => t + (p.semanas.visitas[k] ?? 0), 0) : null;
    const conVenta = base.filter((p) => (p.semanas.piezas[k] ?? 0) > 0).length;
    const cambios = base.filter((p) => {
      const f = p.cambio.fecha.slice(0, 10);
      return f >= s.inicio && f <= s.fin;
    }).length;
    const ritmo = (x: number | null) => (x == null ? null : s.en_curso ? (x * 7) / s.dias : x);
    return { s, piezas, visitas, conVenta, cambios, rPiezas: ritmo(piezas), rVisitas: ritmo(visitas) };
  });
  const vendianAntes = base.filter((p) => p.antes.piezas > 0).length;

  return (
    <dialog
      ref={ref}
      className="of-dlg"
      aria-labelledby="sg-titulo"
      onClose={onCerrar}
      onClick={(e) => {
        if (e.target === ref.current) ref.current?.close();
      }}
    >
      <div className="dlg-cab">
        <div>
          <h3 id="sg-titulo">Seguimiento semanal · ¿se movieron?</h3>
          <div className="sm">
            Publicaciones a las que se les aplicó un cambio: piezas vendidas y visitas por semana (lunes a domingo). Antes = los{" "}
            {d?.ventana_dias ?? 28} días previos al cambio; después = desde el cambio, llevado a «por semana».
            {d ? ` · leído ${d.leido.slice(11, 16)}` : ""}
          </div>
        </div>
        <div className="of-cab-botones">
          <button
            type="button"
            className="ed-cancelar"
            onClick={() => {
              fresco.current = true;
              setLecturas((n) => n + 1);
            }}
          >
            Volver a leer
          </button>
          <button className="cerrar" type="button" onClick={() => ref.current?.close()}>
            Cerrar
          </button>
        </div>
      </div>

      <div className="of-cuerpo">
        {error && <p className="ed-error">{error}</p>}
        {!d && !error && <p className="sm">Leyendo ventas de kubera y visitas de Mercado Libre…</p>}
        {d && (
          <>
            <div className="of-filtros">
              <div className="seg" aria-label="Grupo de cambios">
                <button type="button" aria-pressed={grupo === "todos"} onClick={() => setGrupo("todos")}>
                  Todos<span className="cnt">{d.pubs.length}</span>
                </button>
                {d.grupos.map((g) => (
                  <button key={g.clave} type="button" aria-pressed={grupo === g.clave} onClick={() => setGrupo(g.clave)}>
                    {g.clave.startsWith("lote:") ? `Descuentos del ${corta(g.clave.slice(5))}` : "Desde el panel"}
                    <span className="cnt">{g.n}</span>
                  </button>
                ))}
              </div>
              <div className="seg" aria-label="Cuenta">
                {["todas", "BEKURA", "SANCORFASHION"].map((c) => (
                  <button key={c} type="button" aria-pressed={cuenta === c} onClick={() => setCuenta(c)}>
                    {c === "todas" ? "Las dos" : NOMBRE_CUENTA[c]}
                  </button>
                ))}
              </div>
              <input className="of-buscar" type="search" value={buscar} onChange={(e) => setBuscar(e.target.value)} placeholder="SKU o título" aria-label="Buscar por SKU o título" />
            </div>

            {d.visitas.estado === "leyendo" && (
              <p className="of-ayuda">
                Leyendo las visitas de Mercado Libre ({d.visitas.hechas} de {d.visitas.total})… la tabla se completa sola.
              </p>
            )}
            {!d.kubera && <p className="ed-error">Kubera no contestó: las ventas pueden venir incompletas.</p>}

            <div className="sg-tarjetas">
              <div className="sg-tarjeta">
                <b>{n0(base.length)}</b>
                <span>publicaciones con cambio</span>
              </div>
              <div className="sg-tarjeta">
                <b className="sg-sube">{n0(cuenta_("vendio"))}</b>
                <span>
                  vendieron desde el cambio <span className="sm">(en sus {d.ventana_dias} días previos vendían {n0(vendianAntes)})</span>
                </span>
              </div>
              <div className="sg-tarjeta">
                <b className="sg-sube">{n0(cuenta_("visitas"))}</b>
                <span>con más visitas por semana (+10 % o más)</span>
              </div>
              <div className="sg-tarjeta">
                <b>{n0(cuenta_("quietas"))}</b>
                <span>sin movimiento: ni venta ni más visitas</span>
              </div>
            </div>

            <h4 className="sg-h">Semana por semana</h4>
            <div className="of-tabla-env">
              <table className="of-tabla sg-semanas">
                <thead>
                  <tr>
                    <th>Semana</th>
                    <th className="der">Piezas</th>
                    <th className="der">vs anterior</th>
                    <th className="der">Publicaciones con venta</th>
                    <th className="der">Visitas</th>
                    <th className="der">vs anterior</th>
                    <th className="der">Conversión</th>
                  </tr>
                </thead>
                <tbody>
                  {porSemana.map((w, k) => {
                    const prev = k > 0 ? porSemana[k - 1] : null;
                    return (
                      <tr key={w.s.clave} className={w.cambios ? "sg-con-cambio" : undefined}>
                        <td>
                          <b>{w.s.clave.slice(5)}</b> · {corta(w.s.inicio)} – {corta(w.s.fin)}
                          {w.s.en_curso && <div className="sm">en curso: {w.s.dias} de 7 días (se compara por ritmo)</div>}
                          {w.cambios > 0 && <div className="sm sg-marca">← {w.cambios} cambio{w.cambios > 1 ? "s" : ""} esta semana</div>}
                        </td>
                        <td className="der mono">
                          {n0(w.piezas)}
                          {w.s.en_curso && <div className="sm">ritmo {n0(w.rPiezas)}/sem</div>}
                        </td>
                        <td className="der mono">
                          <Delta d={prev ? delta(w.rPiezas, prev.rPiezas) : null} a={w.rPiezas} b={prev?.rPiezas} />
                        </td>
                        <td className="der mono">{n0(w.conVenta)}</td>
                        <td className="der mono">
                          {n0(w.visitas)}
                          {w.s.en_curso && w.visitas != null && <div className="sm">ritmo {n0(w.rVisitas)}/sem</div>}
                        </td>
                        <td className="der mono">
                          <Delta d={prev ? delta(w.rVisitas, prev.rVisitas) : null} a={w.rVisitas} b={prev?.rVisitas} />
                        </td>
                        <td className="der mono">{w.visitas ? ((w.piezas / w.visitas) * 100).toFixed(2) + " %" : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="of-filtros">
              <div className="seg" aria-label="Qué publicaciones">
                {VISTAS.map(([k, t]) => (
                  <button key={k} type="button" aria-pressed={vista === k} onClick={() => setVista(k)}>
                    {t}
                    <span className="cnt">{cuenta_(k)}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="of-tabla-env">
              <table className="of-tabla">
                <thead>
                  <tr>
                    <th>Publicación</th>
                    <th>Cambio</th>
                    <th>Piezas por semana</th>
                    <th>Visitas por semana</th>
                    <th className="der">Piezas/sem antes → después</th>
                    <th className="der">Visitas/sem antes → después</th>
                    <th>Movimiento</th>
                  </tr>
                </thead>
                <tbody>
                  {filas.slice(0, 400).map((p) => (
                    <tr key={p.id}>
                      <td className="sg-pub">
                        <div className="mono sm">
                          {p.sku || "sin SKU"} · {NOMBRE_CUENTA[p.cuenta || ""] || p.cuenta}
                        </div>
                        <a href={`https://articulo.mercadolibre.com.mx/${p.id.replace("MLM", "MLM-")}`} target="_blank" rel="noopener noreferrer">
                          {titulos.get(p.id) || p.id}
                        </a>
                      </td>
                      <td>
                        {TIPO[p.cambio.tipo] || p.cambio.tipo} · {corta(p.cambio.fecha.slice(0, 10))}
                        <div className="mono sm">
                          {pesos(p.cambio.antes)} → {pesos(p.cambio.nuevo)}
                          {p.cambio.n > 1 ? ` · ${p.cambio.n} cambios` : ""}
                        </div>
                        {p.cambio.quien && <div className="sm">{p.cambio.quien}</div>}
                      </td>
                      <td>
                        <Mini xs={p.semanas.piezas} enCurso={!!sems[ult]?.en_curso} titulo={p.semanas.piezas.map((x, k) => `${sems[k]?.clave.slice(5)}: ${x ?? "—"}`).join(" · ")} />
                        <div className="mono sm">{p.semanas.piezas.slice(-4).map((x) => n0(x)).join(" · ")}</div>
                      </td>
                      <td>
                        <Mini xs={p.semanas.visitas} enCurso={!!sems[ult]?.en_curso} titulo={p.semanas.visitas.map((x, k) => `${sems[k]?.clave.slice(5)}: ${x ?? "—"}`).join(" · ")} />
                        <div className="mono sm">{p.semanas.visitas.slice(-4).map((x) => n0(x)).join(" · ")}</div>
                      </td>
                      <td className="der mono">
                        {n1(p.antes.piezas_sem)} → <b>{n1(p.despues.piezas_sem)}</b>
                        <div className="sm">
                          {p.antes.piezas} en {p.antes.dias} d → {p.despues.piezas} en {p.despues.dias} d
                        </div>
                      </td>
                      <td className="der mono">
                        {n1(p.antes.visitas_sem)} → <b>{n1(p.despues.visitas_sem)}</b>
                        <div className="sm">
                          <Delta d={delta(p.despues.visitas_sem, p.antes.visitas_sem)} a={p.despues.visitas_sem} b={p.antes.visitas_sem} />
                        </div>
                      </td>
                      <td>
                        {p.mov.vendio && <span className="of-chip si">Vendió</span>}{" "}
                        {p.mov.mas_visitas && <span className="of-chip activa">Más visitas</span>}{" "}
                        {p.mov.sin_movimiento && <span className="of-chip no">Sin movimiento</span>}
                        {p.mov.mas_visitas == null && !p.mov.vendio && <span className="sm">sin visitas de ML</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filas.length > 400 && <p className="sm">Se muestran 400 de {filas.length}: usa el buscador.</p>}
              {!filas.length && <p className="sm" style={{ padding: 12 }}>No hay publicaciones en esta vista.</p>}
            </div>
          </>
        )}
      </div>
    </dialog>
  );
}
