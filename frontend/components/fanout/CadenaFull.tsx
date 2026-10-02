"use client";

/**
 * CadenaFull — el camino de una pieza hacia FULL y fuera de él, por cuenta:
 * salidas de Odoo abiertas → en camino → FULL vendible → vendido hoy, y abajo lo
 * que ML mueve por su cuenta (ajustes, retiros, traslados, cuarentena).
 *
 * Lo de Odoo llega aparte (`/api/fanout/full/camino`): tarda y puede faltar sin
 * que el resto se caiga. FULL no toca Woo: aquí solo se mira.
 */
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import type { CaminoCuenta, CaminoFull, CuentaFull, CuentaSel, GrupoFull, ResumenFull } from "./tipos";
import { conSigno } from "./tipos";

const n = (v: number) => v.toLocaleString("es-MX");

const OPCIONES: { id: CuentaSel; texto: string }[] = [
  { id: "ambas", texto: "Ambas" },
  { id: "BEKURA", texto: "Kubera" },
  { id: "SANCORFASHION", texto: "San Corpe" },
];

function Nodo({ eyebrow, titulo, cifra, unidad, lineas, destacado, punteado }: {
  eyebrow: string;
  titulo: string;
  cifra: string;
  unidad: string;
  lineas: React.ReactNode[];
  destacado?: boolean;
  punteado?: boolean;
}) {
  return (
    <div className={`flex min-w-[200px] flex-1 flex-col gap-1.5 rounded-xl px-4 py-3.5 ${
      destacado ? "border-2 border-indigo-500 bg-white ring-4 ring-indigo-50"
        : punteado ? "border border-dashed border-slate-400 bg-slate-50" : "border border-slate-300 bg-white"}`}>
      <span className={`text-[10px] font-semibold uppercase tracking-[0.12em] ${destacado ? "text-indigo-700" : "text-slate-500"}`}>{eyebrow}</span>
      <span className="text-[15px] font-bold text-slate-900">{titulo}</span>
      <span className="flex flex-wrap items-baseline gap-1.5">
        <span className="text-3xl font-bold tabular-nums text-slate-900">{cifra}</span>
        <span className="text-[13px] text-slate-600">{unidad}</span>
      </span>
      {lineas.map((l, i) => <span key={i} className="text-xs leading-[18px] text-slate-600">{l}</span>)}
    </div>
  );
}

function Flecha() {
  return (
    <span className="hidden shrink-0 items-center justify-center text-slate-400 md:flex" aria-hidden>
      <ArrowRight size={20} />
    </span>
  );
}

function Mosaico({ titulo, avisos, piezas, texto, tono }: {
  titulo: string; avisos: number; piezas: number; texto: string; tono: "ambar" | "naranja" | "gris";
}) {
  const cls = tono === "ambar" ? "border-amber-200 bg-amber-50 text-amber-900"
    : tono === "naranja" ? "border-orange-200 bg-orange-50 text-orange-900" : "border-slate-200 bg-slate-50 text-slate-700";
  return (
    <div className={`flex min-w-[200px] flex-1 flex-col gap-0.5 rounded-xl border px-3.5 py-3 ${cls}`}>
      <span className="flex items-baseline justify-between gap-2">
        <span className="text-[13px] font-bold">{titulo}</span>
        <span className="text-xs">{n(avisos)} {avisos === 1 ? "aviso" : "avisos"}</span>
      </span>
      <span className="text-[22px] font-bold tabular-nums">{conSigno(piezas)} <span className="text-sm font-semibold">pzs</span></span>
      <span className="text-xs leading-[17px]">{texto}</span>
    </div>
  );
}

export default function CadenaFull({ d, camino, cuenta, onCuenta }: {
  d: ResumenFull;
  camino: CaminoFull | null;
  cuenta: CuentaSel;
  onCuenta: (c: CuentaSel) => void;
}) {
  const sel: CuentaFull[] = cuenta === "ambas" ? d.cuentas : d.cuentas.filter((c) => c.cuenta === cuenta);
  const hoy = (g: GrupoFull) => sel.reduce((s, c) => ({ avisos: s.avisos + c.hoy[g].avisos, piezas: s.piezas + c.hoy[g].piezas }),
    { avisos: 0, piezas: 0 });
  const nombre = cuenta === "ambas" ? "Kubera + San Corpe" : sel[0]?.nombre ?? cuenta;
  const piezas = sel.reduce((s, c) => s + c.piezas, 0);
  const publicaciones = sel.reduce((s, c) => s + c.publicaciones, 0);

  let cam: CaminoCuenta | null = null;
  if (camino?.ok && camino.cuentas) {
    const claves = cuenta === "ambas" ? Object.keys(camino.cuentas) : [cuenta];
    cam = claves.reduce<CaminoCuenta>((s, k) => {
      const c = camino.cuentas?.[k];
      if (!c) return s;
      return {
        abiertas: s.abiertas + c.abiertas, pzs_abiertas: s.pzs_abiertas + c.pzs_abiertas, en_proceso: s.en_proceso + c.en_proceso,
        enviadas: s.enviadas + c.enviadas, llegadas: s.llegadas + c.llegadas, en_camino: s.en_camino + c.en_camino,
        sin_numero: s.sin_numero + c.sin_numero,
      };
    }, { abiertas: 0, pzs_abiertas: 0, en_proceso: 0, enviadas: 0, llegadas: 0, en_camino: 0, sin_numero: 0 });
  }
  const odooTxt = !camino ? "Leyendo las salidas de Odoo…" : !camino.ok ? `Sin lectura de Odoo: ${camino.motivo ?? "no contestó"}.` : null;

  const vendido = hoy("vendido");
  const llego = hoy("llego");
  const cancelado = hoy("cancelado");
  const ajuste = hoy("ajuste");
  const retiro = hoy("retiro");
  const traslado = hoy("traslado");
  const cuarentena = hoy("cuarentena");
  const otro = hoy("otro");

  return (
    <section aria-labelledby="t-cadena-full" className="flex flex-col gap-4 rounded-2xl bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="t-cadena-full" className="text-[17px] font-bold text-slate-900">La cadena de FULL · {nombre}</h2>
          <p className="text-xs text-slate-600">Hoy hasta las {d.hora} · avisos de ML al minuto · stock según el sync</p>
        </div>
        <div role="group" aria-label="Cuenta de Mercado Libre" className="flex gap-1 rounded-xl bg-white p-1 ring-1 ring-slate-200">
          {OPCIONES.map((o) => (
            <button key={o.id} type="button" aria-pressed={cuenta === o.id} onClick={() => onCuenta(o.id)}
              className={`h-9 rounded-lg px-3.5 text-sm font-semibold ${cuenta === o.id ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
              {o.texto}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-stretch gap-2">
        <Nodo eyebrow="Bodega · Odoo" titulo="Salidas a FULL abiertas" cifra={cam ? n(cam.abiertas) : "—"}
          unidad={cam ? `salidas · ${n(cam.pzs_abiertas)} pzs por surtir` : ""}
          lineas={[odooTxt ?? "Restan en Odoo, y por eso en Woo, cuando bodega valida."]} />
        <Flecha />
        <Nodo punteado eyebrow="Entre Odoo y FULL" titulo="En camino" cifra={cam ? n(cam.en_camino) : "—"}
          unidad={cam ? "pzs enviadas, aún no vendibles" : ""}
          lineas={cam ? [
            `${n(cam.en_proceso)} ${cam.en_proceso === 1 ? "envío llegando" : "envíos llegando"} por tandas (1 a 4 días).`,
            cam.sin_numero ? (
              <Link href="/fulfillment" className="font-semibold text-amber-800 underline-offset-2 hover:underline">
                {n(cam.sin_numero)} {cam.sin_numero === 1 ? "salida" : "salidas"} sin número de envío: su llegada no se puede seguir.
              </Link>
            ) : "Todas las salidas tienen su número de envío.",
          ] : [odooTxt]} />
        <Flecha />
        <Nodo destacado eyebrow="Bodegas de Mercado Libre" titulo="FULL vendible" cifra={n(piezas)} unidad="pzs"
          lineas={[`En ${n(publicaciones)} publicaciones activas.`,
            <span key="l" className="font-semibold text-sky-800">{conSigno(llego.piezas)} pzs llegaron hoy</span>]} />
        <Flecha />
        <Nodo eyebrow="Pedidos FULL" titulo="Vendido hoy" cifra={conSigno(vendido.piezas)}
          unidad={`pzs en ${n(vendido.avisos)} ${vendido.avisos === 1 ? "venta" : "ventas"}`}
          lineas={["Cada venta crea su pedido en Woo sin restar stock.",
            `${conSigno(cancelado.piezas)} pzs regresaron por cancelación.`]} />
      </div>

      <div className="flex flex-wrap gap-2.5">
        <Mosaico titulo="Ajustes de ML" avisos={ajuste.avisos} piezas={ajuste.piezas} tono="ambar"
          texto="No mueven lo vendible: el libro diario lo confirma." />
        <Mosaico titulo="Retiros" avisos={retiro.avisos} piezas={retiro.piezas} tono="naranja"
          texto="Regresan a bodega: tienen que entrar por Odoo." />
        <Mosaico titulo="Traslados internos" avisos={traslado.avisos} piezas={traslado.piezas} tono="gris"
          texto="ML mueve piezas entre sus bodegas." />
        <Mosaico titulo="Cuarentena" avisos={cuarentena.avisos} piezas={cuarentena.piezas} tono="gris"
          texto="Devoluciones en revisión antes de volver a la venta." />
      </div>
      {otro.avisos > 0 && (
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-900">
          Hoy llegaron {n(otro.avisos)} avisos de un tipo que la tabla no conoce: revisa «Salud de la sincronización».
        </p>
      )}
    </section>
  );
}
