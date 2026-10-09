"use client";

/**
 * EstadoBodegas — el encabezado de la pestaña Bodegas: las bodegas de kubera (las
 * de fuente kubera en `almacen.almacenes`, con sus tres banderas), las cuatro
 * banderas de la 0064/0065 en `ops.automatizacion_flags` (sin fila manda su
 * variable, o apagada) y la salud de la sincronización (última pasada de
 * stock_watch, vendidas sin orden, la lectura de Odoo, el libro de kubera y la
 * vigía). Ya no hay TEX3, REVISION, formatos ni «qué falta» (limpieza de la
 * Fase 1). Lo arma `GET /api/fanout/bodegas`. Solo lee.
 */
import { Renglon } from "./SaludFull";
import type { AlmacenBodega, BanderaBodega, ResumenBodegas } from "./tipos";
import { haceSegundos, horaCorta } from "./tipos";

const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("es-MX"));
const pl = (k: number, uno: string, varios: string) => `${k.toLocaleString("es-MX")} ${k === 1 ? uno : varios}`;

function Chip({ encendida, texto }: { encendida: boolean; texto: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
      encendida ? "bg-emerald-50 text-emerald-800" : "bg-slate-100 text-slate-700"}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${encendida ? "bg-emerald-600" : "bg-slate-500"}`} aria-hidden />
      {texto}: {encendida ? "encendida" : "apagada"}
    </span>
  );
}

function fuenteBandera(b: BanderaBodega, hoy: string): string {
  if (b.fuente === "error") return "No se pudo leer la tabla: se toma apagada.";
  if (b.fuente === "variable") {
    // El backend toma el valor de la variable cuando no hay fila: el texto dice el que tiene hoy.
    return b.variable
      ? `Sin fila: manda ${b.variable}, que hoy vale ${b.encendida ? "true" : "false"}.`
      : "Sin fila = apagada.";
  }
  const quien = [b.por, b.actualizado ? horaCorta(b.actualizado, hoy) : null].filter(Boolean).join(" · ");
  return `Fila en ops.automatizacion_flags${quien ? ` (${quien})` : ""}${b.motivo ? `: ${b.motivo}` : ""}.`;
}

function TarjetaKubera({ almacenes, tablas }: { almacenes: AlmacenBodega[]; tablas: boolean }) {
  const propias = almacenes.filter((a) => a.fuente === "kubera");
  return (
    <section aria-labelledby="t-kubera" className="flex flex-col gap-3 rounded-2xl bg-white p-5 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="t-kubera" className="text-[15px] font-bold text-slate-900">Bodegas de kubera</h2>
        <span className="text-xs text-slate-600">libro propio, no Odoo</span>
      </div>
      {!tablas ? (
        <p className="text-sm text-slate-600">Sin la migración 0064 no hay bodegas de kubera que leer.</p>
      ) : propias.length === 0 ? (
        <p className="text-sm text-slate-600">No hay ninguna bodega de kubera dada de alta.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {propias.map((a) => (
            <li key={a.codigo} className="flex flex-col gap-1.5">
              <span className="text-[13px] text-slate-800">
                <span className="font-mono font-semibold text-slate-900">{a.codigo}</span> · {a.nombre}
                {a.admite_ov && !a.surte_ventas ? " · de práctica" : ""}
              </span>
              <div className="flex flex-wrap gap-1.5">
                <Chip encendida={a.surte_ventas} texto="Surte ventas" />
                <Chip encendida={a.cuenta_para_woo} texto="Cuenta para Woo" />
                <Chip encendida={a.admite_ov} texto="Admite OV" />
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs leading-[18px] text-slate-600">
        TEXCO, TEX2 y DROP son de Odoo y se leen en vivo en la tabla. Las columnas «kubera» sólo aparecen para las
        bodegas de kubera que tienen saldo.
      </p>
    </section>
  );
}

function TarjetaBanderas({ banderas, hoy }: { banderas: BanderaBodega[]; hoy: string }) {
  return (
    <section aria-labelledby="t-banderas" className="flex flex-col gap-3 rounded-2xl bg-white p-5 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="t-banderas" className="text-[15px] font-bold text-slate-900">Banderas de la 0064/0065</h2>
        <span className="text-xs text-slate-600">sin fila = su variable o apagada</span>
      </div>
      <ul className="flex flex-col gap-2.5">
        {banderas.map((b) => (
          <li key={b.flag} className="flex items-start gap-2.5">
            <span className={`mt-px shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              b.encendida ? "bg-emerald-50 text-emerald-800" : "bg-slate-100 text-slate-700"}`}>
              {b.encendida ? "Encendida" : "Apagada"}
            </span>
            <span className="flex min-w-0 flex-col">
              <span className="break-all font-mono text-xs font-semibold text-slate-900">{b.flag}</span>
              <span className="text-xs leading-[18px] text-slate-600">{b.que}. {fuenteBandera(b, hoy)}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TarjetaSalud({ d }: { d: ResumenBodegas }) {
  const sw = d.stock_watch;
  const edad = sw.edad_s;
  const puntoSw = edad == null ? "mal" : edad <= 45 * 60 ? "bien" : edad <= 2 * 3600 ? "ojo" : "mal";
  const pe = sw.pendientes;
  const c = d.conteos;
  const o = d.odoo;
  return (
    <section aria-labelledby="t-salud-bodegas" className="flex flex-col gap-3 rounded-2xl bg-white p-5 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="t-salud-bodegas" className="text-[15px] font-bold text-slate-900">Sincronización</h2>
        <span className="text-xs text-slate-600">Odoo → Woo</span>
      </div>
      <ul className="flex flex-col gap-2.5">
        <Renglon punto={puntoSw}
          titulo={`Última pasada de stock_watch: ${sw.ultima ? horaCorta(sw.ultima, d.hoy) : "nunca"}`}
          texto={<>
            {haceSegundos(edad)} · pasa cada 20 min · {sw.absoluto ? "modo absoluto (Woo copia a Odoo)" : "modo delta"}
            {sw.solo_registro ? " · solo registro: anota sin escribir" : ""}
            {!sw.habilitado ? " · este proceso no lo corre" : ""}
            {puntoSw !== "bien" ? ". Una foto vieja es un freno (más de 300 cambios) o una pasada abortada." : ""}
          </>} />
        <Renglon punto={pe.ciega ? "mal" : "bien"}
          titulo={!pe.aplica ? "No se restan vendidas sin orden" : pe.ciega ? "Vendidas sin orden: no se pudieron medir"
            : `Vendidas sin orden: ${pl(pe.ventas ?? 0, "venta espera", "ventas esperan")} su orden en Odoo`}
          texto={!pe.aplica ? "STOCK_WATCH_RESTA_PENDIENTES apagada (o modo delta): Woo copia a Odoo tal cual."
            : pe.ciega ? "stock_watch no copia nada de Odoo a Woo mientras no pueda medirlas."
            : <>stock_watch las resta antes de copiar (ventana de {n(sw.dias)} días)
              {pe.recientes ? ` · ${pl(pe.recientes, "SKU tiene", "SKUs tienen")} una venta u orden posterior a la pasada: quedan «por copiar»` : ""}
              {pe.recientes_error ? " · no se pudieron leer las ventas posteriores a la pasada, así que una venta reciente puede salir como «de más»" : ""}.</>} />
        <Renglon punto={!o.ok ? "mal" : o.viejo || !o.tras_pasada ? "ojo" : "bien"}
          titulo={!o.ok ? "Odoo no respondió" : `Odoo leído ${haceSegundos(o.edad_s)}${o.viejo ? " (lectura vieja)" : ""}`}
          texto={!o.ok ? (o.motivo || "Sin lectura de Odoo.")
            : <>{!o.tras_pasada ? "Anterior a la última pasada: no se compara contra la foto · " : ""}
              TEX2 con existencias: {pl(o.skus_tex2 ?? 0, "producto", "productos")} · caché de 10 min
              {o.archivados_tex2 ? ` · ${pl(o.archivados_tex2, "archivado", "archivados")} con ${n(o.piezas_archivadas_tex2)} pzs que stock_watch no cuenta` : ""}
              {o.duplicados ? ` · ${pl(o.duplicados, "código duplicado", "códigos duplicados")}` : ""}</>} />
        {d.tablas.ok && c && (
          <Renglon punto="bien"
            titulo={`Libro de kubera: ${pl(c.movimientos ?? 0, "movimiento", "movimientos")}`}
            texto={`${pl(c.ov_abiertas ?? 0, "OV abierta", "OV abiertas")} (borrador o confirmada) · conteo con caché de 5 min`} />
        )}
        {d.tablas.ok && d.tablas.vigia && (
          <Renglon punto={d.vigia.length ? "mal" : "bien"}
            titulo={d.vigia.length ? `La vigía ve ${pl(d.vigia.length, "descuadre", "descuadres")}` : "La vigía no ve descuadres"}
            texto={d.vigia.length
              ? d.vigia.slice(0, 4).map((v) => `${v.problema}${v.sku ? ` · ${v.sku}` : ""}${v.almacen ? ` (${v.almacen})` : ""}`).join("; ")
              : "ops.stock_apartado_descuadre_v: los apartados y las devoluciones por renglón de OV cuadran."} />
        )}
      </ul>
    </section>
  );
}

export default function EstadoBodegas({ d }: { d: ResumenBodegas }) {
  return (
    <div className="grid items-start gap-4 lg:grid-cols-3">
      <TarjetaKubera almacenes={d.almacenes} tablas={d.tablas.ok} />
      <TarjetaBanderas banderas={d.banderas} hoy={d.hoy} />
      <TarjetaSalud d={d} />
    </div>
  );
}
