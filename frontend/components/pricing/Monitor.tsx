"use client";

// Monitor.tsx — La mesa de PRICING: el «Monitor de precios FULL» de José
// (artifact del 14-oct-2026) dentro del panel. Tarjetas por acción, filtros,
// «Reglas y umbrales» editables, la tabla (de lo que es a «Qué hacer») y el
// detalle de cada publicación con su gráfica y lo scrapeado.
//
// SOLO LECTURA: sugiere, no cambia ningún precio. Los umbrales se recuerdan en
// este navegador (localStorage), como en el artifact.

import { Fragment, useEffect, useMemo, useState } from "react";
import { EyeOff, Info } from "lucide-react";

import type { PricingComp, PricingFoto } from "@/lib/api";
import { pct1, pctS, pesos, pzs, reloj, type Reloj } from "./formato";
import Grafica from "./Grafica";
import { Mini } from "./Miniaturas";
import {
  ORDEN_ACC,
  REGLAS,
  SENTIDO,
  calcular,
  descuentoPara,
  etiqueta,
  hoyDe,
  type ClaveAccion,
  type Fila,
  type Reglas,
  type Sentido,
} from "./reglas";
import Rivales from "./Rivales";

const LLAVE_REGLAS = "pricing-reglas-v1";
const COLOR: Record<Sentido, string> = { up: "var(--up)", down: "var(--down)", hold: "var(--hold)", none: "var(--muted)" };
const CAT_T: Record<string, [string, string]> = {
  correcta: ["correcta", "p-ok"],
  incorrecta: ["incorrecta", "p-bad"],
  revisar: ["revisar", "p-warn"],
};
const ES_SV = (k: ClaveAccion) => k.startsWith("sv_");

interface Filtros {
  ventas: "todas" | "con" | "sin";
  cat: "todas" | "correcta" | "incorrecta" | "revisar" | "editable";
  comp: "todas" | "con" | "sin";
  cuenta: string;
  accion: ClaveAccion | null;
  q: string;
  orden: string;
}

const F0: Filtros = { ventas: "todas", cat: "todas", comp: "todas", cuenta: "todas", accion: null, q: "", orden: "accion" };

const editable = (f: Fila) => f.p.cat.v === "incorrecta" && !((f.p.sold ?? 0) > 0);

function pasa(f: Fila, F: Filtros, sinAccion = false): boolean {
  const { p, m, a } = f;
  if (F.ventas === "con" && !(m.u30 > 0)) return false;
  if (F.ventas === "sin" && m.u30 > 0) return false;
  if (F.cat === "editable" ? !editable(f) : F.cat !== "todas" && p.cat.v !== F.cat) return false;
  if (F.comp === "con" && m.ref == null) return false;
  if (F.comp === "sin" && m.ref != null) return false;
  if (F.cuenta !== "todas" && p.c !== F.cuenta) return false;
  if (!sinAccion && F.accion && a.k !== F.accion) return false;
  if (F.q.trim()) {
    const q = F.q.trim().toLowerCase();
    if (!((p.sku || "") + " " + (p.t || "") + " " + p.id).toLowerCase().includes(q)) return false;
  }
  return true;
}

// Por encabezado: llave → valor (los que no tienen dato van al final en las dos direcciones).
const VALOR: Record<string, (f: Fila) => number | null> = {
  precio: (f) => f.m.precio,
  comp: (f) => f.m.ref,
  dif: (f) => f.m.dif,
  stock: (f) => f.p.stock,
  ventas: (f) => f.m.u30,
  conv: (f) => f.m.conv,
  dias: (f) => (isFinite(f.m.dias) ? f.m.dias : null),
  v7: (f) => f.x.sug ?? f.x.hoy,
  ganan: (f) => (f.x.sug ?? f.x.hoy) - f.x.hoy,
};
const porValor = (k: string, dir: number) => (x: Fila, y: Fila) => {
  const a = VALOR[k](x);
  const b = VALOR[k](y);
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return dir * (a - b);
};
const ORDENES: Record<string, (x: Fila, y: Fila) => number> = {
  accion: (x, y) => ORDEN_ACC.indexOf(x.a.k) - ORDEN_ACC.indexOf(y.a.k) || y.p.stock - x.p.stock,
  precio_desc: porValor("precio", -1),
  precio_asc: porValor("precio", 1),
  v7: porValor("v7", -1),
  ganan: porValor("ganan", -1),
  stock: (x, y) => y.p.stock - x.p.stock,
  dias: (x, y) => x.m.dias - y.m.dias || y.p.stock - x.p.stock,
  arriba: (x, y) => (y.m.dif ?? -9) - (x.m.dif ?? -9),
  abajo: (x, y) => (x.m.dif ?? 9) - (y.m.dif ?? 9),
  ventas: (x, y) => y.m.u30 - x.m.u30,
  conv: (x, y) => (y.m.conv ?? -1) - (x.m.conv ?? -1),
};

/** Motivo corto (el completo va en el tooltip y en el detalle). */
function sinRef(c: PricingComp): string {
  if (c.adultos) return "No medible · adultos";
  if (c.busqueda_mala) return "Búsqueda equivocada";
  if (c.visual && (c.visual.no || c.visual.no_se)) return "Sin comparables (fotos)";
  if (/sin SKU/i.test(c.motivo || "")) return "Sin SKU";
  if (/sin búsqueda/i.test(c.motivo || "")) return "Sin búsqueda";
  return "Sin comparables";
}

function leerReglas(): Reglas {
  try {
    const guardadas = JSON.parse(window.localStorage.getItem(LLAVE_REGLAS) || "{}");
    return { ...REGLAS, ...(guardadas && typeof guardadas === "object" ? guardadas : {}) };
  } catch {
    return { ...REGLAS };
  }
}

function guardarReglas(R: Reglas) {
  try {
    window.localStorage.setItem(LLAVE_REGLAS, JSON.stringify(R));
  } catch {
    // ventana privada o almacenamiento bloqueado: las reglas duran lo que la página
  }
}

export default function Monitor({ foto }: { foto: PricingFoto }) {
  const [R, setR] = useState<Reglas>(REGLAS);
  const [F, setF] = useState<Filtros>(F0);
  const [abiertos, setAbiertos] = useState<Set<number>>(() => new Set());
  const [rivSku, setRivSku] = useState<string | null>(null);

  useEffect(() => setR(leerReglas()), []);
  const cambiarReglas = (parcial: Partial<Reglas> | null) => {
    setR((prev) => {
      const nuevo = parcial ? { ...prev, ...parcial } : { ...REGLAS };
      guardarReglas(nuevo);
      return nuevo;
    });
  };

  const rel = useMemo(() => reloj(foto.meta.d0), [foto.meta.d0]);
  const { filas, convTipica } = useMemo(() => calcular(foto.pubs, R), [foto.pubs, R]);
  const series = { pubs: foto.meta.img.pubs || "p", riv: foto.meta.img.riv || "s" };

  const base = useMemo(() => filas.filter((f) => pasa(f, F, true)), [filas, F]);
  const vista = useMemo(() => {
    const ord = F.orden.startsWith("h:")
      ? porValor(F.orden.split(":")[1], +F.orden.split(":")[2])
      : ORDENES[F.orden] ?? ORDENES.accion;
    return filas.filter((f) => pasa(f, F)).sort(ord);
  }, [filas, F]);

  const conteo = useMemo(() => {
    const n: Partial<Record<ClaveAccion, number>> = {};
    base.forEach((f) => {
      n[f.a.k] = (n[f.a.k] || 0) + 1;
    });
    return n;
  }, [base]);

  let uh = 0, ph = 0, us = 0, ps = 0;
  vista.forEach((f) => {
    const x = f.x;
    const s2 = x.sug ?? x.hoy;
    const p2 = x.pSug ?? x.pHoy;
    uh += x.hoy;
    ph += x.hoy * x.pHoy;
    us += s2;
    ps += s2 * p2;
  });
  const difTot = ph ? ps / ph - 1 : 0;

  const alternar = (i: number) =>
    setAbiertos((prev) => {
      const s = new Set(prev);
      if (s.has(i)) s.delete(i);
      else s.add(i);
      return s;
    });
  const ordenarPor = (k: string) =>
    setF((prev) => ({ ...prev, orden: prev.orden === `h:${k}:-1` ? `h:${k}:1` : `h:${k}:-1` }));
  const ariaSort = (k: string): "ascending" | "descending" | "none" =>
    F.orden === `h:${k}:1` ? "ascending" : F.orden === `h:${k}:-1` ? "descending" : "none";

  const fuentes: Record<string, number> = {};
  foto.pubs.forEach((p) => {
    const fu = p.comp?.fuente;
    if (fu) fuentes[fu] = (fuentes[fu] || 0) + 1;
  });
  const fuente = Object.entries(fuentes).sort((a, b) => b[1] - a[1])[0]?.[0];
  const hoyLargo = new Date(foto.meta.hoy + "T12:00:00").toLocaleDateString("es-MX", { day: "numeric", month: "long", year: "numeric" });

  const seg = <K extends "ventas" | "cat" | "comp" | "cuenta">(campo: K, opciones: [Filtros[K], string][]) => (
    <div className="seg">
      {opciones.map(([v, t]) => (
        <button key={String(v)} type="button" aria-pressed={F[campo] === v} onClick={() => setF((prev) => ({ ...prev, [campo]: v }))}>
          {t}
        </button>
      ))}
    </div>
  );

  return (
    <div className="envoltura">
      <header className="cabecera">
        <div>
          <h1>Pricing · Monitor de precios FULL</h1>
          <p className="sub">
            {foto.pubs.length} publicaciones FULL de BEKURA y SANCORFASHION · precio con descuento y visitas de ML y ventas de
            kubera al {hoyLargo}
            {fuente ? ` · competencia: ${fuente}` : ""}
          </p>
        </div>
        <div className="sello">
          <span className="badge">
            <EyeOff size={13} aria-hidden /> Oculta · solo admin
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Info size={14} aria-hidden /> Solo lectura: esta pantalla no cambia ningún precio
          </span>
          <span>Foto del {foto.meta.generado}</span>
        </div>
      </header>

      <section className="resumen" aria-label="Acciones sugeridas">
        {ORDEN_ACC.filter((k) => conteo[k]).map((k) => (
          <button
            key={k}
            type="button"
            className={`chip-acc${ES_SV(k) ? " sv" : ""}`}
            aria-pressed={F.accion === k}
            onClick={() => setF((prev) => ({ ...prev, accion: k }))}
          >
            <span className="dot" style={{ background: COLOR[SENTIDO[k]] }} />
            <span>{etiqueta(k, R)}</span>
            <b>{conteo[k]}</b>
          </button>
        ))}
        <button type="button" className="chip-acc" aria-pressed={!F.accion} onClick={() => setF((prev) => ({ ...prev, accion: null }))}>
          <span>Todas</span>
          <b>{base.length}</b>
        </button>
      </section>

      <section className="filtros" aria-label="Filtros">
        <div className="grupo">
          <span>Ventas 30 d</span>
          {seg("ventas", [["todas", "Todas"], ["con", "Con ventas"], ["sin", "Sin ventas"]])}
        </div>
        <div className="grupo">
          <span>Categoría</span>
          {seg("cat", [["todas", "Todas"], ["correcta", "Correctas"], ["incorrecta", "Incorrectas"], ["revisar", "Revisar"], ["editable", "Editables"]])}
        </div>
        <div className="grupo">
          <span>Competencia</span>
          {seg("comp", [["todas", "Todas"], ["con", "Comparables"], ["sin", "Sin referencia"]])}
        </div>
        <div className="grupo">
          <span>Cuenta</span>
          {seg("cuenta", [["todas", "Ambas"], ["BEKURA", "BEKURA"], ["SANCORFASHION", "SANCORFASHION"]])}
        </div>
        <div className="grupo">
          <label htmlFor="pm-q" className="sm">
            Buscar
          </label>
          <input
            type="search"
            id="pm-q"
            placeholder="SKU, título o MLM…"
            value={F.q}
            onChange={(e) => setF((prev) => ({ ...prev, q: e.target.value }))}
          />
        </div>
        <div className="grupo">
          <label htmlFor="pm-orden" className="sm">
            Ordenar
          </label>
          <select id="pm-orden" value={F.orden.startsWith("h:") ? "" : F.orden} onChange={(e) => setF((prev) => ({ ...prev, orden: e.target.value }))}>
            <option value="" hidden>
              Por columna
            </option>
            <option value="accion">Por acción</option>
            <option value="stock">Más stock FULL</option>
            <option value="dias">Se agota antes</option>
            <option value="arriba">Más arriba de la competencia</option>
            <option value="abajo">Más abajo de la competencia</option>
            <option value="ventas">Más ventas 30 d</option>
            <option value="conv">Mayor conversión</option>
            <option value="precio_desc">Precio: mayor a menor</option>
            <option value="precio_asc">Precio: menor a mayor</option>
            <option value="v7">Más venta en 7 d con lo sugerido</option>
            <option value="ganan">Más piezas ganadas en 7 d</option>
          </select>
        </div>
      </section>

      <PanelReglas R={R} cambiar={cambiarReglas} />

      <p className="total">
        Próximos 7 días de las {vista.length} publicaciones a la vista: al precio de hoy{" "}
        <b>
          {pzs(uh)} pzs · {pesos(ph)}
        </b>
        ; con lo sugerido{" "}
        <b>
          {pzs(us)} pzs · {pesos(ps)}
        </b>{" "}
        ({difTot >= 0 ? "+" : ""}
        {Math.round(difTot * 100)} % en pesos).
      </p>

      <div className="tabla-wrap">
        <table>
          <thead>
            <tr>
              <th>Publicación</th>
              <th className="n orden" aria-sort={ariaSort("precio")} onClick={() => ordenarPor("precio")}>
                Precio hoy
              </th>
              <th
                className="n orden"
                aria-sort={ariaSort("comp")}
                onClick={() => ordenarPor("comp")}
                title="Media (mediana), mínimo y máximo de los rivales que cuentan. Toca la media de una fila para ver lo scrapeado"
              >
                Competencia
              </th>
              <th className="n orden" aria-sort={ariaSort("dif")} onClick={() => ordenarPor("dif")}>
                Vs. comp.
              </th>
              <th className="n orden" aria-sort={ariaSort("stock")} onClick={() => ordenarPor("stock")}>
                Stock
              </th>
              <th className="n orden" aria-sort={ariaSort("ventas")} onClick={() => ordenarPor("ventas")}>
                Ventas 30 d
              </th>
              <th className="n orden" aria-sort={ariaSort("conv")} onClick={() => ordenarPor("conv")}>
                Conversión
              </th>
              <th className="n orden" aria-sort={ariaSort("dias")} onClick={() => ordenarPor("dias")}>
                Se agota
              </th>
              <th
                className="n orden"
                aria-sort={ariaSort("v7")}
                onClick={() => ordenarPor("v7")}
                title="Piezas y pesos en 7 días: al precio de hoy y al sugerido"
              >
                Venta 7 d
              </th>
              <th>Categoría</th>
              <th className="que">Qué hacer</th>
            </tr>
          </thead>
          <tbody>
            {vista.length === 0 ? (
              <tr>
                <td colSpan={11} className="vacio">
                  Ninguna publicación cumple estos filtros.
                </td>
              </tr>
            ) : (
              vista.map((f) => (
                <Fragment key={f.p.id}>
                  <FilaPub f={f} R={R} rel={rel} abierta={abiertos.has(f.i)} tieneRiv={!!(f.p.sku && foto.riv[f.p.sku])}
                    serie={series.pubs} onAlternar={() => alternar(f.i)} onRivales={setRivSku} />
                  {abiertos.has(f.i) && (
                    <tr className="det">
                      <td colSpan={11}>
                        <Detalle f={f} R={R} rel={rel} nRiv={f.p.sku ? foto.riv[f.p.sku]?.r.length ?? 0 : 0} onRivales={setRivSku} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Notas R={R} convTipica={convTipica} fallas={foto.meta.fallas?.length ?? 0} />

      <Rivales sku={rivSku} filas={filas} riv={foto.riv} series={series} onCerrar={() => setRivSku(null)} />
    </div>
  );
}

function FilaPub({
  f,
  R,
  rel,
  abierta,
  tieneRiv,
  serie,
  onAlternar,
  onRivales,
}: {
  f: Fila;
  R: Reglas;
  rel: Reloj;
  abierta: boolean;
  tieneRiv: boolean;
  serie: string;
  onAlternar: () => void;
  onRivales: (sku: string) => void;
}) {
  const { p, m } = f;
  const c = p.comp || {};
  const cat = CAT_T[p.cat.v] ?? CAT_T.revisar;
  const H = hoyDe(p);
  const verRiv = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (p.sku) onRivales(p.sku);
  };
  return (
    <tr
      className={`fila${abierta ? " abierta" : ""}`}
      onClick={(e) => {
        if ((e.target as Element).closest("a, button, input, select, summary")) return;
        onAlternar();
      }}
    >
      <td>
        <div className="pub-cel">
          <Mini serie={serie} i={p.img} px={44} alt={p.t} />
          <div>
            <div className="sku">{p.sku || "sin SKU"}</div>
            <a className="tit" href={p.u} target="_blank" rel="noopener noreferrer" title={p.t}>
              {p.t}
            </a>
            <div className="cuenta">
              {p.c}
              {p.st !== "active" ? " · " + p.st : ""}
            </div>
          </div>
        </div>
      </td>
      <td className="n">
        <div className="big">{pesos(m.precio)}</div>
        {m.desc > 0.004 ? (
          <div className="sm">
            <s>{pesos(m.normal)}</s> −{Math.round(m.desc * 100)} %
          </div>
        ) : (
          <div className="sm">sin descuento</div>
        )}
      </td>
      <td className="n">
        {m.ref != null ? (
          <>
            <div>
              <span className="lbl-comp">media</span>
              <button className="ver-riv" type="button" onClick={verRiv} title={`Media (mediana) de ${c.n ?? "—"} comparables · ver lo scrapeado`}>
                {pesos(c.med)}
              </button>
            </div>
            {c.convertido ? (
              <div className="sm" title="Menos de 2 rivales en la misma cantidad: precio que dio el revisor convirtiendo packs por unidad">
                packs convertidos por el revisor
              </div>
            ) : (
              <div className="sm mono">
                {c.min ? `mín ${pesos(c.min)} · máx ${pesos(c.max)}` : "sin mín/máx"} · {c.n ?? "—"} comp.
              </div>
            )}
          </>
        ) : (
          <span className="sinref">
            <span className="pill p-none" title={c.motivo || ""}>
              {sinRef(c)}
            </span>
            {tieneRiv && (
              <button className="ver-riv chico" type="button" onClick={verRiv} title="Ver lo scrapeado">
                ver
              </button>
            )}
          </span>
        )}
      </td>
      <td className="n">
        {m.dif == null ? (
          <span className="sm">—</span>
        ) : (
          <>
            <span className={`pos ${m.pos}`}>{pctS(m.dif)}</span>
            <div className="sm">{m.pos === "nivel" ? "al nivel" : m.pos}</div>
          </>
        )}
      </td>
      <td className="n">
        <div className="big">{p.stock}</div>
        <div className="sm">pzs en FULL</div>
      </td>
      <td className="n">
        <div className="mono">{m.u30} pzs</div>
        <div className="sm">{m.vel.toFixed(1)} al día</div>
      </td>
      <td className="n mono">
        {pct1(m.conv)}
        <div className="sm">{m.vis30.toLocaleString("es-MX")} visitas</div>
      </td>
      <td className="n">
        {!(p.stock > 0) ? (
          <span className="sm">agotada</span>
        ) : isFinite(m.dias) ? (
          <>
            {Math.round(m.dias)} días<div className="sm">{rel.fFecha(H + m.dias)}</div>
          </>
        ) : (
          <span className="sm">sin ventas</span>
        )}
      </td>
      <td className="n v7">
        <CeldaV7 f={f} />
      </td>
      <td>
        <span className={`pill ${cat[1]}`}>{cat[0]}</span>
        {editable(f) && (
          <>
            {" "}
            <span className="pill p-acc">editable</span>
          </>
        )}
      </td>
      <td className="que">
        <CeldaAccion f={f} R={R} />
      </td>
    </tr>
  );
}

function CeldaV7({ f }: { f: Fila }) {
  const x = f.x;
  const hoy = (
    <div>
      <span className="lbl">hoy</span>
      {pzs(x.hoy)} pzs · {pesos(x.hoy * x.pHoy)}
    </div>
  );
  if (x.sug == null)
    return (
      <>
        {hoy}
        <div>
          <span className="lbl">sug.</span>
          <span className="sm">sin cambio</span>
        </div>
      </>
    );
  const cls = x.sug > x.hoy + 0.05 ? "mas" : x.sug < x.hoy - 0.05 ? "menos" : "";
  const tip = x.estimado
    ? "Estimado: sin ventas en 30 días, se usan sus visitas y la conversión típica al precio de mercado"
    : "Ritmo de hoy ajustado por el cambio de precio";
  return (
    <>
      {hoy}
      <div title={tip}>
        <span className="lbl">sug.</span>
        <span className={cls}>
          {x.estimado ? "≈" : ""}
          {pzs(x.sug)} pzs · {pesos(x.sug * (x.pSug ?? 0))}
        </span>
      </div>
    </>
  );
}

function CeldaAccion({ f, R }: { f: Fila; R: Reglas }) {
  const { m, a } = f;
  const texto = a.t ?? etiqueta(a.k, R);
  if (!a.precio) return <span className={`pill p-${SENTIDO[a.k]}`}>{texto}</span>;
  const d = descuentoPara(m.normal, a.precio);
  const cambio = a.precio / m.precio - 1;
  // SIN VENTAS: el ajuste se lee como la PROMOCIÓN que hay que poner en ML —
  // precio normal tachado → precio con promoción y su % — y debajo cuánto
  // cambia contra lo que se cobra hoy.
  if (ES_SV(a.k)) {
    const normal = m.normal && m.normal > 0 ? m.normal : m.precio;
    const desc = a.descNuevo ?? (normal > 0 ? 1 - a.precio / normal : 0);
    return (
      <>
        <span className={`pill p-${SENTIDO[a.k]}`}>{texto}</span>
        <div className="promo" title={`Promoción en ML: precio normal ${pesos(normal)} con ${Math.round(desc * 100)} % de descuento`}>
          <span className="lbl">promo</span>
          <s>{pesos(normal)}</s> → <b>{pesos(a.precio)}</b> <span className="pct">−{Math.round(desc * 100)} %</span>
        </div>
        <div className="luego">
          hoy {pesos(m.precio)}
          {m.desc > 0.004 ? ` (−${Math.round(m.desc * 100)} %)` : " sin promo"} ·{" "}
          {cambio > 0 ? "+" : "−"}
          {Math.abs(Math.round(cambio * 100))} %
        </div>
        {a.luego && (
          <div
            className="luego"
            title={R.svLuego === "normal"
              ? `Si en 7 días no vende: precio normal ${pesos(a.luego.normal)} y se sigue cobrando ${pesos(a.luego.precio)} (${Math.round(a.luego.desc * 100)} % de descuento)`
              : `Si en 7 días no vende: cobrar ${pesos(a.luego.precio)} (precio normal ${pesos(a.luego.normal)} con ${Math.round(a.luego.desc * 100)} % de descuento)`}
          >
            7 d sin venta:{" "}
            {R.svLuego === "normal"
              ? `normal ${pesos(a.luego.normal)} (−${Math.round(a.luego.desc * 100)} %)`
              : `${pesos(a.luego.precio)} (−${Math.round(a.luego.desc * 100)} %)`}
          </div>
        )}
      </>
    );
  }
  const titulo = d
    ? d.subirNormal
      ? "Hay que subir el precio normal"
      : `Descuento de ${Math.round(d.desc * 100)} % sobre el precio normal de ${pesos(m.normal)}`
    : "";
  return (
    <>
      <span className={`pill p-${SENTIDO[a.k]}`}>{texto}</span>
      <div className="obj" title={titulo}>
        {pesos(a.precio)}{" "}
        <span className="sm">
          {pctS(cambio)}
          {d ? " · " + (d.subirNormal ? "subir normal" : "desc " + Math.round(d.desc * 100) + " %") : ""}
        </span>
      </div>
      {a.luego && (
        <div className="luego" title="Lo que sigue si en 7 días no vende">
          sin venta en 7 d →{" "}
          {R.svLuego === "normal"
            ? `normal ${pesos(a.luego.normal)} (−${Math.round(a.luego.desc * 100)} %)`
            : `${pesos(a.luego.precio)} (−${Math.round(a.luego.desc * 100)} %)`}
        </div>
      )}
    </>
  );
}

function Detalle({ f, R, rel, nRiv, onRivales }: { f: Fila; R: Reglas; rel: Reloj; nRiv: number; onRivales: (sku: string) => void }) {
  const { p, m, a } = f;
  const c = p.comp || {};
  const h = m.hist;
  const cat = p.cat;
  const catT = CAT_T[cat.v] ?? CAT_T.revisar;
  return (
    <div className="det-caja">
      <div className="det-top">
        <div>
          <h4>Qué hacer y por qué</h4>
          <p>
            <b>{a.t ?? etiqueta(a.k, R)}.</b> {a.por}
          </p>
        </div>
        <div>
          <h4>Competencia</h4>
          <p>
            {m.ref != null
              ? `${R.ref === "min" ? "El más barato" : "Mediana"} ${pesos(m.ref)} de ${c.n ?? "—"} comparables` +
                `${c.min ? " (de " + pesos(c.min) + " a " + pesos(c.max) + ")" : ""}. Fuente: ${c.fuente || "—"}${
                  c.convertido ? "; el revisor convirtió packs por unidad" : ""
                }.`
              : c.motivo || "Sin precio de competencia fiel."}
            {c.nota && <span className="sm"> {c.nota}</span>}
            {c.visual && (
              <>
                <br />
                <span className="sm">
                  Revisión con fotos:{" "}
                  {c.visual.no || c.visual.no_se
                    ? `de ${c.visual.n} rivales «mismo», ${c.visual.no} eran otro producto y ${c.visual.no_se} no mostraban el producto`
                    : `los ${c.visual.n} rivales coinciden`}
                  {c.visual.busqueda ? "; la búsqueda trajo otro producto" : ""}
                  {c.visual.distintas ? "; nuestras publicaciones de este SKU muestran productos distintos" : ""}.
                </span>
              </>
            )}
            {nRiv > 0 && p.sku && (
              <>
                <br />
                <button className="ver-riv chico" type="button" onClick={() => onRivales(p.sku as string)}>
                  Ver las {nRiv} publicaciones scrapeadas
                </button>
              </>
            )}
          </p>
        </div>
        <div>
          <h4>Categoría</h4>
          <p>
            <span className={`pill ${catT[1]}`}>{catT[0]}</span> {cat.actual || ""}. {cat.sug ? `Sugerida: ${cat.sug}. ` : ""}
            <span className="sm">{cat.motivo || ""}</span>
            {editable(f) ? (
              <>
                <br />
                <b>Se puede cambiar:</b> la publicación no tiene ventas.
              </>
            ) : cat.v === "incorrecta" ? (
              <>
                <br />
                <span className="sm">Con ventas, ML no deja cambiarla.</span>
              </>
            ) : null}
          </p>
        </div>
        {h && (
          <div>
            <h4>Historia de precio</h4>
            <p>
              Vendía sobre todo a {pesos(h.pA)} hasta el {rel.fFecha(h.dHi)}. Antes: {h.vA.toFixed(2)} al día
              {h.cA != null ? ", conversión " + pct1(h.cA) : ""}. Después: {h.vB.toFixed(2)} al día
              {h.cB != null ? ", conversión " + pct1(h.cB) : ""}.{h.dispara ? " Bajar el precio no ayudó." : " Bajar el precio sí ayudó."}
            </p>
          </div>
        )}
      </div>
      <div className="leyenda">
        <span>
          <i style={{ background: "var(--accent)" }} />
          venta · el número son las piezas
        </span>
        <span>
          <i style={{ background: "transparent", border: "1.5px solid var(--muted)" }} />
          cancelada
        </span>
        <span>
          <i style={{ background: "var(--accent-soft)", borderRadius: 2 }} />
          visitas de la semana
        </span>
        <span>
          <i style={{ background: "var(--proj)", border: "1px solid var(--line)", borderRadius: 2 }} />
          proyección para agotar
        </span>
      </div>
      <Grafica f={f} R={R} rel={rel} />
    </div>
  );
}

// ── Reglas y umbrales ───────────────────────────────────────────────────────

type CampoNum = { r: keyof Reglas; t: string; step: number; min?: number; max?: number; pct?: boolean; nota?: string };

const CAMPOS_JOSE: CampoNum[] = [
  { r: "altas", t: "Ventas altas desde (piezas al día)", step: 0.1, min: 0, nota: "«3 al día aprox»" },
  { r: "bajas", t: "Ventas bajas desde (piezas al día)", step: 0.1, min: 0, nota: "«1–2 al día»; abajo de esto son pocas ventas" },
  { r: "conv", t: "Conversión para subir (%)", step: 0.1, min: 0, pct: true, nota: "arriba de esto sube; en esto o abajo, no se mueve" },
  { r: "diasStock", t: "Stock para subir (días o menos)", step: 1, min: 0 },
  { r: "banda", t: "«Al nivel» de la competencia (± %)", step: 0.5, min: 0, pct: true },
  { r: "paso", t: "Cuánto subir (%)", step: 0.5, min: 0, pct: true },
  { r: "tolHist", t: "«Vende igual» tras bajar (± %)", step: 1, min: 0, pct: true },
  { r: "minHist", t: "Piezas a precio alto para juzgar", step: 1, min: 1 },
  { r: "elasticidad", t: "Elasticidad precio (venta 7 d)", step: 0.1, max: 0, nota: "−2 = bajar 10 % el precio vende ~23 % más" },
];

const CAMPOS_SV: CampoNum[] = [
  { r: "svAgresivo", t: "Descuento «super agresivo»: más de (%)", step: 1, min: 0, pct: true },
  { r: "svExtraAgresivo", t: "Si ya es agresivo: agregar esta semana (%)", step: 0.5, min: 0, pct: true, nota: "solo esto; nada más" },
  { r: "svMostrar", t: "Si en 7 días no vende: mostrar (%)", step: 1, min: 0, max: 95, pct: true },
  { r: "svExtra", t: "Con descuento (agresivo o menos): agregar (%)", step: 0.5, min: 0, pct: true },
  { r: "svCorte", t: "Sin descuento: ticket bajo, menor a ($)", step: 10, min: 0 },
  { r: "svBajo", t: "Sin descuento, ticket bajo (%)", step: 0.5, min: 0, pct: true },
  { r: "svAlto", t: "Sin descuento, ticket alto (%)", step: 0.5, min: 0, pct: true },
];

function Numero({ campo, R, cambiar }: { campo: CampoNum; R: Reglas; cambiar: (p: Partial<Reglas>) => void }) {
  const actual = R[campo.r] as number;
  const mostrar = (v: number) => String(campo.pct ? +(v * 100).toFixed(2) : v);
  const [texto, setTexto] = useState(mostrar(actual));
  useEffect(() => setTexto(mostrar(actual)), [actual]); // eslint-disable-line react-hooks/exhaustive-deps
  const id = `pm-r-${campo.r}`;
  return (
    <label htmlFor={id}>
      {campo.t}
      <input
        id={id}
        type="number"
        step={campo.step}
        min={campo.min}
        max={campo.max}
        value={texto}
        onChange={(e) => {
          setTexto(e.target.value);
          const v = parseFloat(e.target.value);
          if (e.target.value.trim() !== "" && isFinite(v)) cambiar({ [campo.r]: campo.pct ? v / 100 : v } as Partial<Reglas>);
        }}
        onBlur={() => setTexto(mostrar(actual))}
      />
      {campo.nota && <small>{campo.nota}</small>}
    </label>
  );
}

function PanelReglas({ R, cambiar }: { R: Reglas; cambiar: (p: Partial<Reglas> | null) => void }) {
  return (
    <details className="reglas">
      <summary>Reglas y umbrales</summary>

      <h3>Productos sin ventas · las 2 cuentas de ML (15-oct)</h3>
      <div className="reglas-grid">
        <label htmlFor="pm-r-sv">
          Aplicar las reglas de sin ventas
          <select id="pm-r-sv" value={R.sinVentas ? "si" : "no"} onChange={(e) => cambiar({ sinVentas: e.target.value === "si" })}>
            <option value="si">Sí: primero que las de competencia</option>
            <option value="no">No: el monitor como lo dejó José</option>
          </select>
          <small>cero piezas pagadas en 30 días, con stock en FULL</small>
        </label>
        {CAMPOS_SV.map((c) => (
          <Numero key={c.r} campo={c} R={R} cambiar={cambiar} />
        ))}
        <label htmlFor="pm-r-svmodo">
          Cómo se suma el % adicional
          <select id="pm-r-svmodo" value={R.svModo} onChange={(e) => cambiar({ svModo: e.target.value as Reglas["svModo"] })}>
            <option value="puntos">Al descuento: 62 % → 65 %</option>
            <option value="precio">Al precio de hoy: × 0.97</option>
          </select>
        </label>
        <label htmlFor="pm-r-svluego">
          Si no vende: cómo se llega a mostrar ese %
          <select id="pm-r-svluego" value={R.svLuego} onChange={(e) => cambiar({ svLuego: e.target.value as Reglas["svLuego"] })}>
            <option value="normal">Bajar el precio normal (se cobra lo mismo)</option>
            <option value="precio">Cobrar el precio normal menos ese %</option>
          </select>
        </label>
      </div>

      <h3>Con ventas · reglas de José (14-oct)</h3>
      <div className="reglas-grid">
        {CAMPOS_JOSE.map((c) => (
          <Numero key={c.r} campo={c} R={R} cambiar={cambiar} />
        ))}
        <label htmlFor="pm-r-ref">
          Precio de competencia de referencia
          <select id="pm-r-ref" value={R.ref} onChange={(e) => cambiar({ ref: e.target.value as Reglas["ref"] })}>
            <option value="med">Mediana de los comparables</option>
            <option value="min">El más barato</option>
          </select>
        </label>
      </div>
      <div className="reglas-acciones">
        <button className="boton" type="button" onClick={() => cambiar(null)}>
          Volver a las reglas originales
        </button>
        <span>
          El orden en que se aplican: sin stock → sin ventas (agresivo / con descuento / sin descuento) → bajar no ayudó → sin
          referencia → ventas altas → ventas bajas → pocas ventas (arriba / abajo / al nivel).
        </span>
      </div>
    </details>
  );
}

function Notas({ R, convTipica, fallas }: { R: Reglas; convTipica: number | null; fallas: number }) {
  const p = (x: number) => `${+(x * 100).toFixed(1)} %`;
  return (
    <section className="notas">
      <p>
        <b>Sin ventas (Brandon, 15-oct).</b> Las publicaciones de las dos cuentas con cero piezas pagadas en 30 días y stock en
        FULL siguen otras reglas, antes que las de competencia: con descuento de más de {p(R.svAgresivo)}, solo{" "}
        {p(R.svExtraAgresivo)} más esta semana (si vende, mantener; si no, que muestre {p(R.svMostrar)} de descuento); sin
        descuento, {p(R.svBajo)} si el ticket es menor a {pesos(R.svCorte)} y {p(R.svAlto)} si es mayor; con descuento de{" "}
        {p(R.svAgresivo)} o menos, {p(R.svExtra)} más.{" "}
        {R.svModo === "puntos"
          ? "El % adicional se suma al descuento sobre el precio normal (62 % → 65 %)."
          : "El % adicional se descuenta del precio de hoy."}{" "}
        La foto no sabe si ya se aplicó el ajuste de la semana: se ve en la siguiente foto, por las ventas.
      </p>
      <p>
        <b>De dónde sale cada número.</b> Precio hoy: lo que paga el comprador según ML (<span className="mono">sale_price</span>
        ); el tachado es el precio normal. Ventas y conversión: piezas pagadas de los últimos 30 días (órdenes de kubera) entre
        visitas de ML del mismo periodo. «Al día» usa los últimos 14 días si hubo 3 o más piezas; si no, 30. Se agota: piezas en
        FULL entre ventas al día.
      </p>
      <p>
        <b>Competencia.</b> Mediana de los rivales que la revisión del 14-oct marcó como el mismo producto en la misma cantidad
        (la regla del juez de OMNICANAL). Después se revisó cada rival con su FOTO contra la nuestra: lo que era otro producto se
        descartó y lo que no mostraba el producto quedó como dudoso (no cuenta). Si quedaron menos de 2 solo por packs, se usa el
        precio que dio el revisor convirtiendo packs, marcado «convertido». Las categorías de adultos no se miden: ML esconde esos
        productos al buscador. Toca el precio para ver todo lo scrapeado y por qué contó o no cada rival.
      </p>
      <p>
        <b>Categoría.</b> «Correcta» si la revisión lo dijo o si ML sugiere esa misma categoría para el título; «revisar», las
        discutibles. «Editable»: incorrecta y sin ventas, la única que ML deja cambiar.
      </p>
      <p>
        <b>Supuestos.</b> «Ventas bajas» va de {R.bajas} a menos de {R.altas} al día; abajo de {R.bajas} al día cuenta como pocas
        ventas. La conversión es la de 30 días (no hay CTR por publicación). Venta 7 d: «hoy» es el ritmo actual × 7; «sug.»
        ajusta ese ritmo por el cambio de precio con una elasticidad de {R.elasticidad} (medida con 31 pares de publicaciones de
        dos cuentas). Sin ventas, «≈» estima con sus visitas y la conversión típica al precio de mercado (
        {convTipica == null ? "—" : (convTipica * 100).toFixed(1) + " %"}, la mediana de las nuestras que venden y están al nivel
        o abajo). Nunca más que el stock. «Bajar no ayudó» compara los 30 días antes de la última venta a precio alto (≥ 5 %
        arriba del de hoy) contra lo que va desde entonces. Todos los umbrales se cambian en «Reglas y umbrales».
      </p>
      {fallas > 0 && <p className="sm">Lecturas de ML que fallaron al armar la foto: {fallas}.</p>}
    </section>
  );
}
