"use client";

/**
 * FULLFILMENT · Variaciones — 2 o 3 formas alternativas de los cuatro
 * componentes clave (gráfica de tasa, embudo, rail, días de proceso), con
 * datos SIMULADOS para juzgar la forma. Es una pantalla de decisión de diseño:
 * cuando se elija una forma de cada componente, esta pantalla se retira.
 *
 * Todas respetan la misma regla: el rechazo sólo se pinta cuando el
 * marketplace lo dice; lo que está en recepción va en ámbar y lo que no se
 * puede calcular, rayado. Sin librerías de gráficas: SVG y divs a mano, como
 * el resto del panel.
 */

import type { ReactNode } from "react";
import { DIAS, ENVIOS, TABLERO } from "./datosDiseno";
import { Ceja, FONDO_RAYADO, FONDO_RAYADO_AMBAR, RAYADO, Rail, Tarjeta, num, pasosDe } from "./ui";

export default function Variaciones() {
  return (
    <div className="mt-4 flex flex-col gap-3">
      <VariacionA />
      <VariacionB />
      <VariacionC />
      <VariacionD />
    </div>
  );
}

function Opcion({ titulo, unidad, desc, children, pie }: {
  titulo: string; unidad?: string; desc?: string; children: ReactNode; pie?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-slate-200 p-3.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[12.5px] font-extrabold text-slate-900">{titulo}</span>
        {unidad && <span className="font-mono text-[10px] text-slate-400">{unidad}</span>}
      </div>
      {desc && <p className="mt-1 text-[11.5px] text-slate-500">{desc}</p>}
      {children}
      {pie && <div className="mt-2.5 text-[11.5px] text-slate-500">{pie}</div>}
    </div>
  );
}

function Muestra({ fondo, borde, texto, cuadro }: { fondo: string; borde?: string; texto: string; cuadro?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`${cuadro ? "h-2.5 w-2.5" : "h-2 w-3"} rounded-sm`}
            style={{ background: fondo, border: borde }} />
      {texto}
    </span>
  );
}

// ── A · la gráfica obligatoria ──────────────────────────────────────────────
const SEMANAS = [
  { s: "S34", recibido: 1_210, rechazado: 40, recepcion: 0 },
  { s: "S35", recibido: 1_690, rechazado: 96, recepcion: 0 },
  { s: "S36", recibido: 940, rechazado: 18, recepcion: 0 },
  { s: "S37", recibido: 1_380, rechazado: 0, recepcion: 620 },
  { s: "S38", recibido: null, rechazado: null, recepcion: null },
];

const CUADROS: { t: string; tipo: "100" | "99" | "96" | "88" | "62" | "97" | "enlazar" | "recepcion" }[] = [
  { t: "100% · 420 pz", tipo: "100" }, { t: "99% · 1,240 pz", tipo: "99" }, { t: "100% · 310 pz", tipo: "100" },
  { t: "96% · 890 pz", tipo: "96" }, { t: "sin enlazar — no se puede calcular", tipo: "enlazar" },
  { t: "88% · 1,690 pz · 202 rechazadas", tipo: "88" }, { t: "100% · 180 pz", tipo: "100" },
  { t: "en recepción · 620 pz sin contar", tipo: "recepcion" }, { t: "97% · 740 pz", tipo: "97" },
  { t: "62% · 430 pz · 163 rechazadas", tipo: "62" }, { t: "100% · 260 pz", tipo: "100" },
  { t: "99% · 1,120 pz", tipo: "99" }, { t: "sin enlazar", tipo: "enlazar" }, { t: "100% · 95 pz", tipo: "100" },
  { t: "94% · 1,310 pz", tipo: "96" }, { t: "100% · 540 pz", tipo: "100" },
  { t: "en recepción · 310 pz", tipo: "recepcion" }, { t: "98% · 680 pz", tipo: "97" },
  { t: "sin enlazar", tipo: "enlazar" }, { t: "100% · 220 pz", tipo: "100" },
];
const ESTILO_CUADRO: Record<(typeof CUADROS)[number]["tipo"], React.CSSProperties> = {
  "100": { background: "#047857" },
  "99": { background: "#059669" },
  "97": { background: "#10b981" },
  "96": { background: "#34d399" },
  "88": { background: "#f59e0b" },
  "62": { background: "#e11d48" },
  enlazar: { background: "repeating-linear-gradient(135deg,#f8fafc 0 4px,#eef2f7 4px 8px)", border: "1px dashed #cbd5e1" },
  recepcion: { background: "#FEF3C7", border: "1px solid #FCD34D" },
};

function VariacionA() {
  const max = Math.max(...SEMANAS.map((s) => (s.recibido ?? 0) + (s.rechazado ?? 0) + (s.recepcion ?? 0)));
  const alto = (v: number) => (v / max) * 120;
  return (
    <Tarjeta>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <Ceja>Variación A · la gráfica obligatoria</Ceja>
          <h2 className="mt-1 text-[17px] font-extrabold text-slate-900">Tasa de éxito: entregadas contra rechazadas</h2>
        </div>
        <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[10.5px] font-bold uppercase tracking-[.04em] text-slate-500">
          datos simulados para juzgar la forma
        </span>
      </div>

      <div className="mt-4 grid gap-3.5 lg:grid-cols-3">
        <Opcion titulo="A1 · barras apiladas por semana" unidad="piezas"
                desc="El volumen manda. Se ve cuánto se mandó y en qué proporción entró."
                pie={<div className="flex flex-wrap gap-2.5 text-[10.5px]">
                  <Muestra fondo="#059669" texto="recibido" />
                  <Muestra fondo="#e11d48" texto="rechazado explícito" />
                  <Muestra fondo={FONDO_RAYADO_AMBAR} borde="1px solid #FCD34D" texto="en recepción" />
                </div>}>
          <div className="mt-3 flex h-[150px] items-end gap-2.5">
            {SEMANAS.map((s) => (
              <div key={s.s} className="flex flex-1 flex-col justify-end gap-0.5">
                {s.recibido === null ? (
                  <div title="semana en curso, sin recepciones cerradas" className="h-[46px] rounded-[3px]" style={RAYADO} />
                ) : (
                  <>
                    {s.rechazado > 0 && (
                      <div title={`rechazado ${num(s.rechazado)}`} className="rounded-t-[3px] bg-rose-600"
                           style={{ height: Math.max(6, alto(s.rechazado)) }} />
                    )}
                    {s.recepcion! > 0 && (
                      <div title={`en recepción ${num(s.recepcion)} — trabajo en curso, NO rechazo`}
                           className="rounded-t-[3px] border border-amber-300"
                           style={{ height: alto(s.recepcion!), background: FONDO_RAYADO_AMBAR }} />
                    )}
                    <div title={`recibido ${num(s.recibido)}`} className="bg-emerald-600" style={{ height: alto(s.recibido) }} />
                  </>
                )}
                <div className="mt-1 text-center text-[10px] text-slate-400">{s.s}</div>
              </div>
            ))}
          </div>
        </Opcion>

        <Opcion titulo="A2 · la tasa como línea, con meta" unidad="% recibido"
                desc="La tasa manda. Una cuenta por línea, y las semanas sin cerrar quedan rayadas."
                pie={<div className="flex flex-wrap gap-2.5 text-[10.5px]">
                  <Muestra fondo="#38BDF8" texto="Kubera" />
                  <Muestra fondo="#8B5CF6" texto="San Corpe" />
                  <span className="text-slate-400">· el volumen se pierde de vista: un 100% de 12 piezas pesa igual que uno de 1,900</span>
                </div>}>
          <svg viewBox="0 0 320 150" role="img" className="mt-3 w-full">
            <title>Tasa de recepción por semana y cuenta, contra una meta del 97%</title>
            <defs>
              <pattern id="ray-a2" width="8" height="8" patternTransform="rotate(135)" patternUnits="userSpaceOnUse">
                <rect width="8" height="8" fill="#f8fafc" />
                <line x1="0" y1="0" x2="0" y2="8" stroke="#dde3ec" strokeWidth="4" />
              </pattern>
            </defs>
            <rect x="252" y="8" width="60" height="112" fill="url(#ray-a2)" stroke="#cbd5e1" strokeDasharray="4 3" />
            <text x="282" y="70" textAnchor="middle" fontSize="9" fill="#94a3b8">sin cerrar</text>
            <line x1="24" y1="30" x2="312" y2="30" stroke="#cbd5e1" strokeDasharray="4 4" />
            <text x="0" y="33" fontSize="8" fill="#94a3b8">meta 97%</text>
            <line x1="24" y1="120" x2="312" y2="120" stroke="#eef1f6" />
            <polyline points="42,36 94,26 146,44 198,30 250,38" fill="none" stroke="#38BDF8" strokeWidth="2.2" strokeLinejoin="round" />
            <polyline points="42,58 94,48 146,66 198,52 250,44" fill="none" stroke="#8B5CF6" strokeWidth="2.2" strokeLinejoin="round" />
            {[[42, 36], [94, 26], [146, 44], [198, 30], [250, 38]].map(([x, y]) => <circle key={`k${x}`} cx={x} cy={y} r="3" fill="#38BDF8" />)}
            {[[42, 58], [94, 48], [146, 66], [198, 52], [250, 44]].map(([x, y]) => <circle key={`s${x}`} cx={x} cy={y} r="3" fill="#8B5CF6" />)}
            {["S34", "S35", "S36", "S37", "S38"].map((s, i) => (
              <text key={s} x={42 + i * 52} y="134" textAnchor="middle" fontSize="9" fill="#94a3b8">{s}</text>
            ))}
          </svg>
        </Opcion>

        <Opcion titulo="A3 · un cuadro por envío" unidad="envíos"
                desc="El envío manda. Cada cuadro es uno: se ven los casos malos, no el promedio."
                pie={<div className="flex flex-wrap gap-2.5 text-[10.5px]">
                  <Muestra cuadro fondo="#047857" texto="100%" />
                  <Muestra cuadro fondo="#f59e0b" texto="<95%" />
                  <Muestra cuadro fondo="#e11d48" texto="<80%" />
                  <Muestra cuadro fondo={FONDO_RAYADO} borde="1px dashed #cbd5e1" texto="sin enlazar" />
                  <Muestra cuadro fondo="#FEF3C7" borde="1px solid #FCD34D" texto="en recepción" />
                </div>}>
          <div className="mt-3 grid grid-cols-10 gap-1">
            {CUADROS.map((c, i) => (
              <div key={i} title={c.t} className="aspect-square rounded" style={ESTILO_CUADRO[c.tipo]} />
            ))}
          </div>
        </Opcion>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        Las tres respetan la misma regla: <b>el rechazo sólo se pinta cuando el marketplace lo dice</b>. Lo que está
        en recepción va en ámbar y lo que no se puede calcular, rayado.
      </p>
    </Tarjeta>
  );
}

// ── B · embudo de piezas ────────────────────────────────────────────────────
function VariacionB() {
  const enviadas = num(TABLERO.enviadoFull.piezas);
  const barraHueco = (texto: string, ancho: string) => (
    <div className="flex h-[26px] items-center rounded-md px-2.5 text-[11px] font-bold text-slate-400"
         style={{ ...RAYADO, width: ancho }}>{texto}</div>
  );
  const flecha = (t = "↓", tenue = true) => (
    <div className={`pl-2.5 text-[10px] ${tenue ? "text-slate-300" : "text-slate-400"}`}>{t}</div>
  );
  const tarjeta = (r: string, v: string, s: string, tono: "hueco" | "dato" | "neutro") => (
    <div className={`min-w-0 rounded-[10px] px-[7px] py-2 ${
      tono === "dato" ? "border border-emerald-200 bg-emerald-50" : tono === "neutro" ? "border border-slate-200 bg-white" : ""}`}
         style={tono === "hueco" ? RAYADO : undefined}>
      <div className={`text-[9px] font-bold uppercase ${tono === "dato" ? "text-emerald-700" : "text-slate-400"}`}>{r}</div>
      <div className={`mt-1 truncate font-extrabold tabular-nums ${
        tono === "dato" ? "text-[12.5px] text-emerald-800" : tono === "neutro" ? "text-[13px] text-slate-900" : "text-[15px] text-slate-400"}`}>{v}</div>
      <div className={`truncate text-[9px] ${tono === "dato" ? "text-emerald-700" : "text-slate-400"}`}>{s}</div>
    </div>
  );

  return (
    <Tarjeta>
      <Ceja>Variación B · embudo de piezas</Ceja>
      <h2 className="mt-1 text-[17px] font-extrabold text-slate-900">Dónde se pierde la mercancía</h2>
      <div className="mt-4 grid gap-3.5 lg:grid-cols-3">
        <Opcion titulo="B1 · escalera vertical con % de caída"
                desc="Cada escalón es una barra más corta; entre escalones, la pérdida.">
          <div className="mt-3 flex flex-col gap-[3px]">
            {barraHueco("solicitadas · sin registro", "100%")}
            {flecha()}
            {barraHueco("validadas · sin registro", "92%")}
            {flecha()}
            <div className="flex h-[30px] w-[84%] items-center justify-between rounded-md bg-emerald-600 px-2.5 text-[11.5px] font-extrabold text-white">
              enviadas<span className="font-mono">{enviadas}</span>
            </div>
            {flecha(`↓ −${(100 - TABLERO.enviadoFull.pctDePedido).toFixed(1)}% de lo pedido en la orden`, false)}
            {barraHueco("recibidas · sin registro", "70%")}
            {flecha()}
            <div className="flex h-[26px] w-[52%] items-center rounded-md border border-emerald-200 bg-emerald-50 px-2.5 text-[11px] font-bold text-emerald-800">
              vendidas · existe por SKU
            </div>
          </div>
        </Opcion>

        <Opcion titulo="B2 · cinco tarjetas en fila"
                desc="El molde del «Recorrido de la pieza» de Inventario, ya conocido por el equipo."
                pie="Ventaja: nadie tiene que aprender nada nuevo. Desventaja: las cinco cajas pesan igual, y una de ellas vale 116,895 piezas.">
          <div className="mt-3 grid grid-cols-5 gap-1">
            {tarjeta("Solicit.", "—", "sin registro", "hueco")}
            {tarjeta("Valid.", "—", "sin registro", "hueco")}
            {tarjeta("Enviadas", enviadas, `${TABLERO.enviadoFull.pctDePedido}% pedido`, "dato")}
            {tarjeta("Recib.", "—", "sin registro", "hueco")}
            {tarjeta("Vendidas", "existe", "por SKU", "neutro")}
          </div>
        </Opcion>

        <Opcion titulo="B3 · una sola barra que se descompone"
                desc="Una barra al 100% = lo pedido. Los tramos son lo que sobrevive a cada paso."
                pie="Es la más honesta sobre el tamaño del hueco: hoy más de la mitad de la barra está rayada.">
          <div className="mt-3.5 flex h-[34px] overflow-hidden rounded-lg border border-slate-200">
            <div title={`enviadas · ${enviadas}`} className="bg-emerald-600" style={{ width: "49%" }} />
            <div title="en recepción · dato nuevo desde el 14 sep" style={{ width: "14%", background: FONDO_RAYADO_AMBAR }} />
            <div title="recibidas y rechazadas · sin registro" style={{ width: "26%", background: FONDO_RAYADO }} />
            <div title="recorte de bodega · sin registro"
                 style={{ width: "11%", background: "repeating-linear-gradient(135deg,#fef2f2 0 5px,#fee2e2 5px 10px)" }} />
          </div>
          <div className="mt-2.5 flex flex-col gap-1 text-[11px] text-slate-500">
            <Muestra fondo="#059669" texto="salió de bodega y está medido" />
            <Muestra fondo={FONDO_RAYADO_AMBAR} texto="en recepción — no es pérdida" />
            <Muestra fondo={FONDO_RAYADO} texto="ni siquiera sabemos si se perdió" />
          </div>
        </Opcion>
      </div>
    </Tarjeta>
  );
}

// ── C · rail de etapas ──────────────────────────────────────────────────────
function VariacionC() {
  const e = ENVIOS[0];   // un envío en recepción: tiene los tres tonos
  const pasos = pasosDe(e);
  const deltas = ["02 sep 11:02", "+6 h", "+16 h", "+2 d", "en curso", "sin dato", "sin dato"];
  return (
    <Tarjeta>
      <Ceja>Variación C · rail de etapas del envío</Ceja>
      <h2 className="mt-1 text-[17px] font-extrabold text-slate-900">Siete etapas, una fecha o un estado en cada una</h2>
      <div className="mt-4 flex flex-col gap-3.5">
        <Opcion titulo="C1 · celdas parejas (la que usa la tabla de Envíos)"
                pie="Legible en una tabla de 40 renglones y no promete movimiento físico donde no lo hay.">
          <div className="mt-2.5"><Rail pasos={pasos} /></div>
        </Opcion>

        <Opcion titulo="C2 · línea con nodos y el tiempo entre etapas"
                pie="Contesta «qué tramo alarga la semana» de un vistazo. Ocupa el doble de alto: sirve para el detalle, no para la tabla.">
          <div className="relative mt-4 h-[66px]">
            <div className="absolute inset-x-0 top-[9px] h-[3px] rounded-full bg-slate-100" />
            <div className="absolute left-0 top-[9px] h-[3px] w-[57%] rounded-full bg-emerald-600" />
            <div className="relative flex justify-between">
              {pasos.map((p, i) => {
                const alin = i === 0 ? "text-left" : i === pasos.length - 1 ? "text-right" : "text-center";
                const nodo = p.tono === "dato"
                  ? { background: "#059669", border: "3px solid #fff", boxShadow: "0 0 0 1px #059669" }
                  : p.tono === "espera"
                    ? { background: "#f59e0b", border: "3px solid #fff", boxShadow: "0 0 0 1px #f59e0b" }
                    : { background: "#fff", border: "2px dashed #cbd5e1" };
                const color = p.tono === "dato" ? "text-slate-600" : p.tono === "espera" ? "text-amber-700" : "text-slate-400";
                return (
                  <div key={p.t} className={`w-[14%] ${alin}`}>
                    <div className={`h-3.5 w-3.5 rounded-full ${i === 0 ? "ml-0.5" : i === pasos.length - 1 ? "ml-auto mr-0.5" : "mx-auto"}`}
                         style={nodo} />
                    <div className={`mt-1.5 text-[9.5px] font-bold uppercase ${color}`}>{p.t}</div>
                    <div className={`font-mono text-[10px] ${p.tono === "espera" ? "text-amber-700" : p.tono === "hueco" ? "text-slate-400" : "text-slate-500"}`}>
                      {deltas[i]}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </Opcion>
      </div>
    </Tarjeta>
  );
}

// ── D · qué días se procesan ────────────────────────────────────────────────
function VariacionD() {
  const max = Math.max(...DIAS.flatMap((d) => [d.ordenes, d.salidas]));
  const minS = Math.min(...DIAS.filter((d) => d.dia !== "sáb").map((d) => d.salidas));   // días hábiles
  const maxDelta = Math.max(...DIAS.map((d) => Math.abs(d.salidas - d.ordenes)));
  return (
    <Tarjeta>
      <Ceja>Variación D · qué días se procesan</Ceja>
      <h2 className="mt-1 text-[17px] font-extrabold text-slate-900">El calendario ideal contra la distribución real</h2>
      <div className="mt-4 grid gap-3.5 lg:grid-cols-2">
        <Opcion titulo="D1 · barras pareadas orden / salida"
                desc="El desfase salta a la vista: se pide en jueves y se sale en viernes y lunes."
                pie={<div className="flex flex-wrap gap-2.5 text-[10.5px]">
                  <Muestra fondo="#059669" texto="orden creada" />
                  <Muestra fondo="#0369a1" texto="salida hecha" />
                </div>}>
          <div className="mt-3.5 grid h-[130px] grid-cols-6 items-end gap-2">
            {DIAS.map((d) => (
              <div key={d.dia} className="flex h-full items-end gap-0.5">
                <div title={`orden creada ${d.dia} · ${d.ordenes}`} className="flex-1 rounded-t-[3px] bg-emerald-600"
                     style={{ height: `${(d.ordenes / max) * 100}%` }} />
                <div title={`salida hecha ${d.dia} · ${d.salidas}${d.salidas === minS ? " — el mínimo" : ""}`}
                     className="flex-1 rounded-t-[3px] bg-sky-700"
                     style={{ height: `${(d.salidas / max) * 100}%`, outline: d.salidas === minS ? "2px solid #e11d48" : undefined }} />
              </div>
            ))}
          </div>
          <div className="mt-1 grid grid-cols-6 gap-2 text-center text-[11px] font-semibold text-slate-500">
            {DIAS.map((d) => <span key={d.dia}>{d.dia}</span>)}
          </div>
        </Opcion>

        <Opcion titulo="D2 · el desfase como una sola cifra por día"
                desc="Sólo la diferencia: qué días se acumula papel y qué días se desahoga."
                pie={<>Es la lectura más directa del hallazgo: <b>el papel entra martes y jueves, la mercancía sale viernes y lunes</b>. Pierde los volúmenes absolutos.</>}>
          <div className="mt-3.5 flex flex-col gap-2">
            {DIAS.map((d) => {
              const delta = d.salidas - d.ordenes;
              const ancho = `${(Math.abs(delta) / maxDelta) * 100}%`;
              return (
                <div key={d.dia} className="flex items-center gap-2.5">
                  <span className="w-7 text-[11px] font-bold text-slate-500">{d.dia}</span>
                  <div className="flex flex-1 items-center">
                    <div className="flex w-1/2 justify-end">
                      {delta > 0 && (
                        <div title={`${delta} salidas más que órdenes`} className="h-[18px] rounded-l bg-sky-700" style={{ width: ancho }} />
                      )}
                    </div>
                    <div className="w-1/2 border-l border-slate-200">
                      {delta < 0 && (
                        <div title={`${-delta} órdenes más que salidas`} className="h-[18px] rounded-r bg-emerald-600" style={{ width: ancho }} />
                      )}
                    </div>
                  </div>
                  <span className={`w-[42px] text-right font-mono text-[11.5px] font-bold ${delta > 0 ? "text-sky-700" : "text-emerald-700"}`}>
                    {delta > 0 ? `+${delta}` : `−${-delta}`}
                  </span>
                </div>
              );
            })}
          </div>
        </Opcion>
      </div>
    </Tarjeta>
  );
}
