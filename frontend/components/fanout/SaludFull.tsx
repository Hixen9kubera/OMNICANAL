"use client";

/**
 * SaludFull — ¿la sincronización de FULL está sana? Avisos por hora de hoy y una
 * lista corta: de cuándo es la foto del sync, el último aviso de ML, el cuadre de
 * ayer, las salidas sin número de envío, los envíos que no terminan de llegar y
 * las filas padre que se dejan fuera del total.
 */
import Link from "next/link";
import type { CaminoFull, ResumenFull } from "./tipos";
import { CUADRE_CLS, conSigno } from "./tipos";

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const n = (v: number) => v.toLocaleString("es-MX");

/** «12:00» si es de hoy; si no, «1 oct 22:10». */
function cuando(hora: string | null, hoy: string): string {
  if (!hora) return "—";
  if (hora.slice(0, 10) === hoy) return hora.slice(11, 16);
  const [, m, dd] = hora.slice(0, 10).split("-").map(Number);
  return `${dd} ${MESES[m - 1]} ${hora.slice(11, 16)}`;
}

/** Minutos entre «YYYY-MM-DD HH:MM:SS» y la hora de la página (las dos de CDMX). */
function minutosDesde(hora: string | null, hoy: string, ahora: string): number | null {
  if (!hora) return null;
  const a = Date.parse(`${hora.replace(" ", "T")}Z`);
  const b = Date.parse(`${hoy}T${ahora}:00Z`);
  return Number.isNaN(a) || Number.isNaN(b) ? null : Math.round((b - a) / 60_000);
}

function Renglon({ punto, titulo, texto }: { punto: "bien" | "ojo" | "mal"; titulo: React.ReactNode; texto: React.ReactNode }) {
  const color = punto === "bien" ? "bg-emerald-500" : punto === "ojo" ? "bg-amber-500" : "bg-rose-600";
  return (
    <li className="flex items-start gap-2.5">
      <span className={`mt-[5px] h-2.5 w-2.5 shrink-0 rounded-full ${color}`} aria-hidden />
      <span className="flex min-w-0 flex-col">
        <span className="text-[13px] font-semibold text-slate-900">{titulo}</span>
        <span className="text-xs leading-[18px] text-slate-600">{texto}</span>
      </span>
    </li>
  );
}

export function PorHoraFull({ porHora }: { porHora: ResumenFull["por_hora"] }) {
  const max = Math.max(1, ...porHora.map((h) => h.n));
  return (
    <section aria-labelledby="t-horas-full" className="flex flex-col gap-2 rounded-2xl bg-white p-5 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="t-horas-full" className="text-[15px] font-bold text-slate-900">Avisos por hora</h2>
        <span className="text-xs text-slate-600">hoy · las dos cuentas</span>
      </div>
      <div className="flex h-28 items-end gap-1 border-b border-slate-200" role="img"
        aria-label={porHora.map((h) => `${h.h}: ${h.n}`).join(", ")}>
        {porHora.map((h) => (
          <div key={h.h} className="flex h-full flex-1 flex-col items-center justify-end gap-0.5">
            <span className="text-[10px] tabular-nums text-slate-600">{h.n || ""}</span>
            <div className="w-full max-w-[18px] rounded-t bg-indigo-500" style={{ height: `${Math.round((h.n / max) * 84)}px` }} />
          </div>
        ))}
      </div>
      <div className="flex gap-1">
        {porHora.map((h, i) => (
          <span key={h.h} className="flex-1 text-center text-[10px] tabular-nums text-slate-500">{i % 3 === 0 ? h.h : ""}</span>
        ))}
      </div>
    </section>
  );
}

export default function SaludFull({ d, camino }: { d: ResumenFull; camino: CaminoFull | null }) {
  const s = d.salud;
  const minFoto = minutosDesde(s.foto_al, d.hoy, d.hora);
  const minAviso = minutosDesde(s.ultimo_aviso, d.hoy, d.hora);
  const peorAyer = s.ayer.reduce<"cuadra" | "revisar" | "no_cuadra">((p, a) =>
    a.estado === "no_cuadra" || p === "no_cuadra" ? "no_cuadra" : a.estado === "revisar" || p === "revisar" ? "revisar" : "cuadra", "cuadra");
  const cams = camino?.ok && camino.cuentas ? camino.cuentas : null;
  const sinNumero = cams ? Object.values(cams).reduce((t, c) => t + c.sin_numero, 0) : 0;

  return (
    <section aria-labelledby="t-salud-full" className="flex flex-col gap-3 rounded-2xl bg-white p-5 shadow-sm">
      <h2 id="t-salud-full" className="text-[15px] font-bold text-slate-900">Salud de la sincronización</h2>
      <ul className="flex flex-col gap-3">
        <Renglon punto={minFoto != null && minFoto <= 120 ? "bien" : "ojo"}
          titulo={`Foto del sync: ${cuando(s.foto_al, d.hoy)}`}
          texto="El stock de cada publicación FULL; el sync anota cuando cambia." />
        <Renglon punto={minAviso != null && minAviso <= 180 ? "bien" : "ojo"}
          titulo={`Último aviso de ML: ${cuando(s.ultimo_aviso, d.hoy)}`}
          texto={`${n(d.avisos_hoy)} avisos hoy; ${s.sin_sku_hoy ? `${n(s.sin_sku_hoy)} sin SKU` : "ninguno sin SKU"} (${n(s.sin_sku_7d)} en 7 días).`} />
        {s.ayer.length > 0 && (
          <Renglon punto={peorAyer === "cuadra" ? "bien" : peorAyer === "revisar" ? "ojo" : "mal"}
            titulo={`Cuadre de ayer: ${CUADRE_CLS[peorAyer].texto.toLowerCase()}`}
            texto={<>{s.ayer.map((a) => `${a.nombre} ${conSigno(a.dif)}`).join(" · ")} pzs, avisos contra foto sin ajustes ni retiros. <a href="#libro" className="font-semibold text-indigo-700 underline-offset-2 hover:underline">Ver el libro</a></>} />
        )}
        {cams && sinNumero > 0 && (
          <Renglon punto="ojo" titulo={`${n(sinNumero)} salidas a FULL sin número de envío`}
            texto={<>Kubera {n(cams.BEKURA?.sin_numero ?? 0)} · San Corpe {n(cams.SANCORFASHION?.sin_numero ?? 0)}
              {cams[""]?.sin_numero ? ` · ${n(cams[""].sin_numero)} sin cuenta` : ""}. Sin número no se ve cuándo llegan. <Link href="/fulfillment" className="font-semibold text-indigo-700 underline-offset-2 hover:underline">Fulfillment</Link></>} />
        )}
        {camino?.ok && (camino.faltan ?? []).slice(0, 3).map((f) => (
          <Renglon key={f.salida} punto="ojo" titulo={`${f.salida} · ${f.nombre}: faltan ${n(f.falta)} de ${n(f.enviadas)}`}
            texto={`Llegaron ${n(f.llegadas)}${f.dias != null ? `; validada hace ${f.dias} ${f.dias === 1 ? "día" : "días"}` : ""}. Las tandas tardan de 1 a 4 días.`} />
        ))}
        {camino && !camino.ok && (
          <Renglon punto="ojo" titulo="Sin lectura de Odoo" texto={camino.motivo ?? "No contestó; se reintenta en 5 minutos."} />
        )}
        {s.dobles.publicaciones > 0 && (
          <Renglon punto="ojo" titulo={`${n(s.dobles.publicaciones)} publicaciones guardadas en dos filas`}
            texto={`Su SKU y el padre o un hermano mal escrito. Cuenta sólo la del SKU que declara Mercado Libre; la otra trae un número viejo (${n(s.dobles.piezas)} pzs fuera del total).`} />
        )}
        {s.tipos_nuevos.map((t) => (
          <Renglon key={t.tipo} punto="mal" titulo={`Tipo de aviso nuevo: ${t.tipo}`}
            texto={`${n(t.n)} en 9 días. La tabla no sabe qué hace con el stock: hay que clasificarlo.`} />
        ))}
      </ul>
    </section>
  );
}
