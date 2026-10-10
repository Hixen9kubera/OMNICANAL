"use client";

/**
 * EL DOCUMENTO de una orden de venta propia. Crear y ver son LA MISMA
 * interfaz —una sin datos, otra con datos—, al modo del formulario de una
 * orden de venta de Odoo pero más limpio: barra con el folio y las acciones,
 * la traza, los datos, los renglones con sus totales, los PDF, y a la derecha
 * el chat de movimientos.
 *
 * EL MODELO (plan v3 de Eduardo; contrato en docs/MIGRACION_0064_0065_GUIA_AGENTE.md)
 * y lo que le hace a esta pantalla:
 *
 *  · La orden sólo vive en BODEGAS DE KUBERA y la bodega va POR RENGLÓN. Ya no
 *    hay «Almacén» de encabezado: cada renglón dice de dónde sale y enseña el
 *    saldo de su SKU en ESA bodega.
 *  · Confirmar APARTA todo o nada. Si un renglón no alcanza, no se aparta
 *    ninguno y el servidor dice cuál: ya no existe la reserva parcial ni el
 *    «reintentar».
 *  · SE EDITAN EL BORRADOR Y LA CONFIRMADA. La confirmada, desde la 0071
 *    (Brandon, 9-oct-2026: «que cualquiera pueda editar una orden ya
 *    confirmada, en caso de que se requiera hacer cualquier cambio»). Es el
 *    MISMO formulario, y lo que cambia sale de que la confirmada ya aparta:
 *      – guardar VUELVE A APARTAR, todo o nada: si un renglón no alcanza no se
 *        guarda ningún cambio y el servidor dice cuál (un 409 que NO mueve la
 *        `rev`: se enseña su texto, como el de confirmar);
 *      – cada renglón por entregar necesita su bodega y la orden no puede
 *        quedarse sin ninguno: se dice aquí, antes de mandar;
 *      – el renglón que YA SALIÓ se ve, pero no se toca ni se quita;
 *      – cada cambio queda en el chat (`editada`) con quién lo hizo.
 *    Lo entregado y lo cancelado siguen congelados por la base (un trigger, no
 *    una costumbre), y la pantalla no ofrece campos que el servidor va a
 *    rechazar. Si una confirmada NO se puede editar, se dice el porqué del
 *    backend (el «¿salió?» pendiente, el modo prueba, el rol…).
 *  · Entregar es POR RENGLÓN y una sola vez por renglón: se dice cuántas
 *    piezas salieron de cada uno; lo que no sale se suelta. Un renglón que
 *    todavía no sale se deja pendiente y la orden sigue confirmada.
 *
 * Lo que NO se ve y explica por qué el archivo es como es:
 *
 *  · Los permisos no se deducen aquí. `orden.permisos` lo decide el backend
 *    (depende del estado y del rol); la pantalla sólo enciende o apaga, y un
 *    botón apagado SIEMPRE dice por qué (`permisos.porque`).
 *  · El formulario vive en `forma` y lo que hay en el servidor en `base`.
 *    «Guardar cambios» manda SÓLO la diferencia: lo que no se manda no se toca.
 *  · `rev` es el candado optimista, y un 409 tiene varios sentidos. Siempre se
 *    relee. Si la `rev` cambió, «la orden cambió mientras tanto»: se CONSERVA
 *    lo que el usuario estaba escribiendo (fusión campo por campo contra la
 *    `base` vieja; los renglones, a TRES BANDAS, para no borrar los que agregó
 *    la otra persona) y se avisa suave. Si la `rev` NO cambió, el 409 es de
 *    negocio —no alcanzó el stock, la venta ya tiene orden, modo prueba— y se
 *    enseña el `detail` del servidor TAL CUAL: él sabe qué SKU no alcanzó.
 *    Nunca se reintenta a ciegas.
 *  · La acción de un DIÁLOGO viaja con la `rev` de cuando se abrió, no con la
 *    de ahora. El documento sigue releyendo debajo del diálogo (el chat avisa)
 *    y éste se repinta solo: con la `rev` de ahora se confirmaría lo que nadie
 *    vio al abrirlo. Si la orden cambió, el servidor contesta 409, no pasa
 *    nada y el aviso queda a la vista con el diálogo ya cerrado.
 *  · El chat también avisa cuando otra persona —o el barrido— movió la orden.
 *    Como el aviso del chat puede llegar ANTES que la respuesta de la acción
 *    que yo mismo lancé, mientras hay una acción en vuelo no se relee: se
 *    apunta y se revisa al terminar. Y el aviso trae la `rev` a la que hay que
 *    LLEGAR: si la relectura se quedó corta (iba en vuelo desde antes) o falló,
 *    se insiste; el documento no se queda en una rev intermedia.
 *  · La clave del alta va atada a lo que se mandó con ella: el doble clic y el
 *    reintento de LO MISMO tras un corte de red no crean dos órdenes; si la
 *    persona corrige el formulario tras el fallo, es otro envío y otra clave.
 *  · Salir con cambios sin guardar SIEMPRE pregunta. Los botones de aquí usan
 *    su diálogo; lo que no pasa por aquí («Atrás» del navegador, las pestañas,
 *    el navbar) lo ataja la página, que lee `sucioRef`.
 *  · «Sin dato» nunca se pinta como 0: un SKU sin fila de saldo en una bodega
 *    dice «sin dato» y no «libre 0». Y lo que TODAVÍA no se sabe (el renglón
 *    vino de una venta, o se le cambió la bodega) se pregunta al catálogo en
 *    vez de inventarlo.
 */

import {
  useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MutableRefObject,
  type ReactNode,
} from "react";
import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle, ArrowLeft, Ban, CircleCheck, CircleHelp, Download, Info, Lock, MoreHorizontal,
  Package, PackageCheck, PackageX, PencilLine, RefreshCw, Save, Trash2, Truck, Undo2, X,
} from "lucide-react";
import { API_BASE, ApiError, mensajeDeError } from "@/lib/api";
import { BotonCerrar, Ventana } from "@/components/fulfillment/ui";
import { ChatOrden } from "@/components/ordenes/ChatOrden";
import { Traza } from "@/components/ordenes/Traza";
import { ArchivosOrden, Confirmacion, PieConfirmacion } from "./ArchivosOrden";
import { BuscadorSku } from "./BuscadorSku";
import { FilaVenta, origenDeVenta } from "./VentasMarketplace";
import {
  borrarOrden, buscarSkus, buscarVenta, cancelarOrden, confirmarOrden, crearOrden, entregarOrden,
  esConflicto, guardarOrden, leerOrden, responderSalio, salioTarde,
} from "./api";
import {
  Aviso, Boton, CANALES, CLASE_CAMPO, CLASE_ROTULO, Campo, ChipEstado, ChipReserva, DialogoMotivo,
  MOTIVO_MINIMO, ROTULO_ESTADO, ROTULO_VIA, aInputLocal, bodegaSugerida, bodegasDeOrdenes,
  claveDeIntento, deInputLocal, dinero, fechaHora, fechaLarga, num, quien, reservaDe, rotuloBodega,
  rotuloCanal, rotuloCuenta, type ClaveIntento,
} from "./ui";
import type {
  Bodega, DatosOrden, DevolucionEstado, EntregaLinea, EstadoModulo, EstadoOrden, Existencia,
  LineaEntrada, LineaOrden, Orden, Permisos, RespOrden, SkuOpcion, VentaMarketplace,
} from "./tipos";

// ── El formulario ─────────────────────────────────────────────────────────────
// Lo de esta sección es lógica PURA (sin React ni red). Se exporta para que la
// ejerciten las pruebas de `pruebas/ordenes.prueba.cjs`: aquí vive lo que más
// caro sale si falla callado (qué se manda a guardar, qué se conserva, qué sale).

/**
 * Lo que se sabe del saldo de un SKU, bodega por bodega. Tres casos distintos
 * que NO se colapsan:
 *   · la bodega trae su saldo            → se sabe cuánto hay;
 *   · la bodega está y vale `null`       → se sabe que el SKU NO tiene fila de saldo ahí;
 *   · la bodega no está en el mapa       → todavía no se sabe (no se ha preguntado).
 */
export type Saldos = Record<string, Existencia | null>;

/** Un renglón en pantalla. `cantidad` y `precio` son texto: es lo que se está tecleando. */
export interface Renglon {
  /** Llave LOCAL del renglón (para React). No viaja al servidor ni se compara. */
  uid: string;
  sku: string;
  titulo: string | null;
  imagen: string | null;
  cantidad: string;
  precio: string;
  /** Código de la bodega de kubera de la que sale. Vacío = todavía sin elegir. */
  almacen: string;
  // Lo de abajo es informativo (lo manda el backend o el buscador); no se guarda.
  /** Piezas apartadas: 0 o la cantidad (se aparta todo o nada). */
  reservado: number;
  /** Piezas que salieron al entregar este renglón. `null` = todavía no sale. */
  entregado: number | null;
  entregado_at: string | null;
  entregado_por: string | null;
  saldos: Saldos;
  /** `false` = el catálogo no lo conoce. `null` = todavía no se sabe (viene de una venta). */
  conocido: boolean | null;
}

export interface Forma {
  cliente: string;
  canal: string;
  mp_canal: string;
  mp_cuenta: string;
  mp_orden: string;
  descripcion: string;
  guia: string;
  paqueteria: string;
  /** Valor de `<input type="datetime-local">`, hora de CDMX. */
  fecha_venta: string;
  entrega_limite: string;
  /** Vacío = el total SIGUE a la suma de los renglones. */
  total: string;
  comision: string;
  precio_origen: "manual" | "marketplace";
  lineas: Renglon[];
}

const CAMPOS_TEXTO = ["cliente", "canal", "descripcion", "guia", "paqueteria"] as const;
/** La ORDEN DE MARKETPLACE es una llave de tres piezas: viajan juntas o no viajan. */
const CAMPOS_MP = ["mp_canal", "mp_cuenta", "mp_orden"] as const;
const CAMPOS_FECHA = ["fecha_venta", "entrega_limite"] as const;

/** Los mismos topes que valida el backend (`_TEXTOS` de services/ordenes_venta.py). */
const TOPE = { cliente: 120, mp_orden: 80, guia: 80, paqueteria: 80, descripcion: 500 } as const;
const MAX_DESCRIPCION = TOPE.descripcion;

/** Lo que la base exige de motivo (`ov_ordenes_canc_m_chk`, `ov_ordenes_borrada_m_chk`). */
export const MOTIVO_CANCELAR_CONFIRMADA = 5;
export const MOTIVO_BORRAR = 10;

export const formaVacia = (): Forma => ({
  cliente: "", canal: "", mp_canal: "", mp_cuenta: "", mp_orden: "", descripcion: "",
  guia: "", paqueteria: "", fecha_venta: "", entrega_limite: "", total: "", comision: "",
  precio_origen: "manual", lineas: [],
});

let consecutivo = 0;
const nuevoUid = (): string => {
  consecutivo += 1;
  return `r${consecutivo}`;
};

/** Un renglón recién capturado: una pieza, sin precio, sin apartar y sin nada que se sepa de su saldo. */
export function renglonNuevo(datos: Partial<Omit<Renglon, "uid">> & { sku: string }): Renglon {
  return {
    uid: nuevoUid(), titulo: null, imagen: null, cantidad: "1", precio: "", almacen: "",
    reservado: 0, entregado: null, entregado_at: null, entregado_por: null, saldos: {}, conocido: null,
    ...datos,
  };
}

/** Texto → número. Vacío (o basura) = `null`: «no se capturó», que no es lo mismo que 0. */
function numero(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

const centavos = (n: number) => Math.round(n * 100) / 100;

function sumaDe(lineas: Renglon[]): number {
  return centavos(lineas.reduce((a, l) => a + (numero(l.cantidad) ?? 0) * (numero(l.precio) ?? 0), 0));
}

const cuentasDe = (canal: string): string[] => CANALES.find((c) => c.id === canal)?.cuentas ?? [];
/** La cuenta que no hay que preguntar: la del canal que sólo tiene una. */
const cuentaUnica = (canal: string): string => {
  const cuentas = cuentasDe(canal);
  return cuentas.length === 1 ? cuentas[0] : "";
};

const skuDe = (l: { sku: string }): string => l.sku.trim().toLowerCase();

/**
 * ¿Este renglón YA SALIÓ de la bodega? Entonces está congelado (la base no deja
 * cambiarlo ni borrarlo, ni con la puerta de la 0071): al editar una confirmada
 * se ve, pero no se toca. Salir con CERO piezas (se soltó) también es haber salido.
 */
export const renglonSalio = (l: Pick<Renglon, "entregado" | "entregado_at">): boolean =>
  l.entregado !== null || !!l.entregado_at;

/**
 * Lo que la orden YA tiene apartado de ese SKU en ESA bodega, según lo guardado
 * (`base`). Es la cuenta que hace el servidor al guardar una confirmada: por
 * (SKU, bodega), lo que ahora piden los renglones menos lo que ya apartaban. Un
 * renglón nuevo —o uno al que se le cambió la bodega— no tiene nada apartado
 * donde está ahora: ahí le toca apartar su cantidad completa. En un borrador
 * nadie aparta y siempre da 0.
 */
export function apartadoEn(l: Pick<Renglon, "sku" | "almacen">, base: Renglon[]): number {
  if (!l.almacen) return 0;
  return base.reduce((s, b) => (
    !renglonSalio(b) && skuDe(b) === skuDe(l) && b.almacen === l.almacen ? s + b.reservado : s), 0);
}

/** Qué pasa con un SKU que se elige en el buscador de renglones: ver `dondeAgregar`. */
export type Agregado =
  | { tipo: "sumar"; renglon: Renglon }
  | { tipo: "nuevo"; almacen: string }
  | { tipo: "ya_salio"; bodegas: string[] };

/**
 * Dónde cae un SKU que se elige en el buscador. Repetir un SKU suma una pieza
 * al renglón que ya está; si no está, entra como renglón nuevo en la bodega
 * `sugerida`.
 *
 * Lo que cambió al poder editar una confirmada: un renglón que YA SALIÓ está
 * congelado y no se le suma nada (el servidor lo rechazaría). Si el SKU sólo
 * está en renglones que ya salieron, entra como renglón NUEVO, pero en otra
 * bodega —SKU + bodega no se repite en la orden—; y si no queda ninguna donde
 * ponerlo, no entra (quien llama dice por qué).
 */
export function dondeAgregar(lineas: Renglon[], sku: string, sugerida: string, elegibles: Bodega[]): Agregado {
  const k = sku.trim().toLowerCase();
  const mismos = lineas.filter((l) => skuDe(l) === k);
  const vivos = mismos.filter((l) => !renglonSalio(l));
  const ya = vivos.find((l) => l.almacen === sugerida) ?? vivos[0];
  if (ya) return { tipo: "sumar", renglon: ya };
  // Aquí `mismos` sólo trae renglones que ya salieron (o nada).
  const tomadas = mismos.map((l) => l.almacen);
  if (!tomadas.includes(sugerida)) return { tipo: "nuevo", almacen: sugerida };
  const libres = elegibles.filter((b) => !tomadas.includes(b.codigo));
  if (!libres.length) return { tipo: "ya_salio", bodegas: tomadas };
  return { tipo: "nuevo", almacen: libres.length === 1 ? libres[0].codigo : "" };
}

/**
 * Lo que contestó el catálogo, puesto por bodega. El buscador trae UNA entrada
 * por bodega de kubera donde el SKU tiene fila de saldo, así que una bodega de
 * kubera que no viene es «sin fila de saldo» (`null`), no «no se sabe».
 */
export function saldosDe(existencias: Existencia[] | null | undefined, bodegas: Bodega[]): Saldos {
  const s: Saldos = {};
  for (const b of bodegas) if (b.fuente === "kubera") s[b.codigo] = null;
  for (const e of existencias ?? []) s[e.almacen] = e;
  return s;
}

/** El saldo del renglón EN SU BODEGA: el saldo, `null` (sin fila de saldo) o `undefined` (no se sabe aún). */
export function saldoDe(l: Pick<Renglon, "almacen" | "saldos">): Existencia | null | undefined {
  return l.almacen ? l.saldos[l.almacen] : undefined;
}

/**
 * Los SKUs de los que falta saber el saldo en alguna bodega elegible (para
 * preguntarlo al catálogo). Los que el catálogo ya dijo que no conoce no se
 * vuelven a preguntar.
 */
export function skusSinSaldo(lineas: Renglon[], elegibles: Bodega[]): string[] {
  if (!elegibles.length) return [];
  const faltan = new Set<string>();
  for (const l of lineas) {
    if (l.conocido === false || !skuDe(l)) continue;
    if (elegibles.some((b) => !(b.codigo in l.saldos))) faltan.add(skuDe(l));
  }
  return [...faltan];
}

/**
 * Los SKUs de los renglones por entregar que NO están guardados donde están
 * ahora: los nuevos y los que cambiaron de bodega (la llave del renglón es SKU +
 * bodega). De ésos hay que VOLVER a preguntarle el saldo al catálogo cuando un
 * guardado rebota.
 *
 * Por qué: al releer la orden el servidor sólo manda el saldo de los renglones
 * GUARDADOS, cada uno en su bodega (ver `formaDe`); del resto la pantalla
 * conserva lo que el catálogo dijo cuando se agregaron, y ya no vuelve a
 * preguntar (una vez por SKU). En un borrador da igual —guardar no aparta y la
 * respuesta trae el saldo fresco—, pero en una confirmada el guardado que falla
 * por «no alcanzó» es justo el momento en que ese dato es viejo: quedaba «libre
 * 10», en gris, junto al aviso del servidor que decía «hay 1 libre».
 *
 * Los que ya salieron no cuentan (están congelados y ya no apartan), ni los que
 * el catálogo dijo que no conoce (no tiene saldo que decir de ellos).
 */
export function skusSinGuardar(base: Renglon[], lineas: Renglon[]): string[] {
  const llave = (l: Renglon) => `${skuDe(l)}|${l.almacen}`;
  const guardados = new Set(base.map(llave));
  const faltan = new Set<string>();
  for (const l of lineas) {
    if (renglonSalio(l) || l.conocido === false || !skuDe(l)) continue;
    if (!guardados.has(llave(l))) faltan.add(skuDe(l));
  }
  return [...faltan];
}

export function formaDe(o: Orden): Forma {
  const lineas: Renglon[] = [...o.lineas].sort((a, b) => a.linea - b.linea).map((l) => ({
    uid: `s${l.id}`, sku: l.sku, titulo: l.titulo, imagen: l.imagen,
    cantidad: String(l.cantidad), precio: Number(l.precio_unitario).toFixed(2),
    almacen: l.almacen ?? "",
    reservado: l.reservado ?? 0, entregado: l.entregado ?? null,
    entregado_at: l.entregado_at ?? null, entregado_por: l.entregado_por ?? null,
    // El backend manda el saldo del SKU en la bodega DEL RENGLÓN; `libre: null`
    // con bodega puesta es «sin fila de saldo ahí», y eso también es saber algo.
    saldos: !l.almacen ? {}
      : { [l.almacen]: l.libre === null || l.libre === undefined ? null
          : { almacen: l.almacen, fisico: l.fisico ?? l.libre, apartado: l.apartado ?? 0, libre: l.libre } },
    conocido: l.conocido,
  }));
  const total = Number(o.total);
  const comision = Number(o.comision);
  // Un total manual que HOY coincide con la suma se trata como «sigue a la
  // suma»: al cambiar un renglón, el total cambia con él. El precio que vino
  // del marketplace se queda clavado: es el de la venta, no una cuenta.
  const sigue = o.precio_origen !== "marketplace" && Math.abs(total - sumaDe(lineas)) < 0.005;
  return {
    cliente: o.cliente ?? "", canal: o.canal ?? "",
    mp_canal: o.mp_canal ?? "", mp_cuenta: o.mp_cuenta ?? "", mp_orden: o.mp_orden ?? "",
    descripcion: o.descripcion ?? "", guia: o.guia ?? "", paqueteria: o.paqueteria ?? "",
    fecha_venta: aInputLocal(o.fecha_venta), entrega_limite: aInputLocal(o.entrega_limite),
    total: sigue ? "" : total.toFixed(2),
    comision: comision ? comision.toFixed(2) : "",
    precio_origen: o.precio_origen, lineas,
  };
}

/** ¿El renglón quedó igual para efectos de guardar? (cantidad, precio y bodega; el SKU ya casó). */
const mismoRenglon = (a: Renglon, b: Renglon): boolean =>
  (numero(a.cantidad) ?? 0) === (numero(b.cantidad) ?? 0) && (numero(a.precio) ?? 0) === (numero(b.precio) ?? 0)
  && a.almacen === b.almacen;

function mismasLineas(a: Renglon[], b: Renglon[]): boolean {
  return a.length === b.length && a.every((x, i) => x.sku === b[i].sku && mismoRenglon(x, b[i]));
}

function lineasDe(lineas: Renglon[]): LineaEntrada[] {
  return lineas.map((l) => ({
    sku: l.sku.trim(), cantidad: numero(l.cantidad) ?? 0, precio_unitario: numero(l.precio) ?? 0,
    titulo: l.titulo, imagen: l.imagen, almacen: l.almacen || null,
  }));
}

/**
 * La ORDEN DE MARKETPLACE tal como viaja: canal + cuenta + id, los TRES o
 * NINGUNO (`ov_ordenes_mp_chk`). La define el ID de la venta: sin id no hay
 * orden de marketplace, aunque los selectores de canal y cuenta se hayan
 * quedado puestos (siguen al «Canal» de arriba para ahorrar un clic).
 */
export function mpDe(f: Pick<Forma, "mp_canal" | "mp_cuenta" | "mp_orden">): [string | null, string | null, string | null] {
  const orden = f.mp_orden.trim();
  if (!orden) return [null, null, null];
  return [f.mp_canal.trim() || null, f.mp_cuenta.trim() || null, orden];
}

/** El cuerpo COMPLETO, para el alta. */
export function datosCompletos(f: Forma): DatosOrden {
  const t = (s: string) => s.trim() || null;
  const [mp_canal, mp_cuenta, mp_orden] = mpDe(f);
  return {
    cliente: t(f.cliente), canal: t(f.canal), mp_canal, mp_cuenta, mp_orden,
    descripcion: t(f.descripcion), guia: t(f.guia), paqueteria: t(f.paqueteria),
    fecha_venta: deInputLocal(f.fecha_venta), entrega_limite: deInputLocal(f.entrega_limite),
    total: numero(f.total), comision: numero(f.comision) ?? 0, precio_origen: f.precio_origen,
    lineas: lineasDe(f.lineas),
  };
}

/** SÓLO lo que cambió respecto a `base`. Un objeto vacío = nada que guardar. */
export function cambiosDe(base: Forma, f: Forma): DatosOrden {
  const d: DatosOrden = {};
  for (const k of CAMPOS_TEXTO) if (f[k].trim() !== base[k].trim()) d[k] = f[k].trim() || null;
  const mp = mpDe(f);
  const mpBase = mpDe(base);
  if (mp.some((v, i) => v !== mpBase[i])) [d.mp_canal, d.mp_cuenta, d.mp_orden] = mp;
  for (const k of CAMPOS_FECHA) if (f[k] !== base[k]) d[k] = deInputLocal(f[k]);
  const cambianLineas = !mismasLineas(base.lineas, f.lineas);
  if (cambianLineas) d.lineas = lineasDe(f.lineas);
  const total = numero(f.total);
  if (total !== numero(base.total)) d.total = total;
  // El total que sigue a la suma hay que pedirlo: «lo que no se manda no se toca».
  else if (total === null && cambianLineas) d.total = null;
  const comision = numero(f.comision) ?? 0;
  if (comision !== (numero(base.comision) ?? 0)) d.comision = comision;
  if (f.precio_origen !== base.precio_origen) d.precio_origen = f.precio_origen;
  return d;
}

/**
 * Un renglón que tocamos los dos: campo por campo, lo que YO cambié gana y lo
 * que no toqué es del servidor. Que una persona elija bodegas mientras otra
 * corrige cantidades es justo lo que pasa ahora que la bodega va por renglón:
 * pisar el renglón entero perdería el cambio de la otra en silencio.
 */
function mezclarRenglon(viejo: Renglon | undefined, mio: Renglon, nuevo: Renglon): Renglon {
  const mia = (campo: "cantidad" | "precio"): string => (
    !viejo || (numero(viejo[campo]) ?? 0) !== (numero(mio[campo]) ?? 0) ? mio[campo] : nuevo[campo]);
  return {
    ...nuevo,
    cantidad: mia("cantidad"), precio: mia("precio"),
    almacen: !viejo || viejo.almacen !== mio.almacen ? mio.almacen : nuevo.almacen,
    // Lo que yo ya sabía de otras bodegas se conserva; lo del servidor es más fresco.
    saldos: { ...mio.saldos, ...nuevo.saldos },
  };
}

/**
 * Fusión a TRES BANDAS de los renglones: lo que hay AHORA en el servidor
 * (`nuevos`) más lo que YO cambié respecto a lo que tenía cargado (`viejos` →
 * `mios`).
 *
 * Por qué no «mi lista entera con el saldo fresco», que es lo que había: el
 * guardado manda la lista COMPLETA de renglones, así que quedarme con la mía
 * BORRABA en el siguiente Guardar los renglones que la otra persona agregó
 * mientras yo escribía, y el candado optimista quedaba anulado justo donde
 * más duele.
 *
 * Los renglones casan por SKU. Sólo cuando un SKU va repartido en VARIAS
 * bodegas (la base lo permite: la llave es SKU + bodega) se distinguen por su
 * bodega; si casaran siempre por SKU + bodega, cambiarle la bodega a un
 * renglón se leería como «quité uno y agregué otro» y la fusión lo duplicaría.
 *
 * La regla es la misma del encabezado —lo que YO toqué gana; lo que no toqué
 * es del servidor—, renglón por renglón:
 *   · lo agregó la otra persona                  → entra;
 *   · lo quitó la otra persona y yo no lo toqué  → se va;
 *   · lo quitó la otra persona y yo lo cambié    → se queda con lo mío (lo veo y decido);
 *   · lo quité yo                                → se va, aunque el otro lo haya cambiado;
 *   · lo cambiamos los dos (o lo agregamos)      → campo por campo: lo que yo toqué, mío;
 *   · lo agregué yo                              → entra, al final.
 * Y una que nació con la edición de la confirmada, donde una persona corrige
 * mientras Bodega entrega:
 *   · YA SALIÓ en el servidor                    → se queda como está allá, lo
 *     haya cambiado o quitado yo: está congelado y lo mío no se podría guardar.
 */
export function fusionarLineas(viejos: Renglon[], mios: Renglon[], nuevos: Renglon[]): Renglon[] {
  const repartidos = new Set<string>();
  for (const lista of [viejos, mios, nuevos]) {
    const vistos = new Set<string>();
    for (const l of lista) {
      if (vistos.has(skuDe(l))) repartidos.add(skuDe(l));
      vistos.add(skuDe(l));
    }
  }
  const k = (l: Renglon) => (repartidos.has(skuDe(l)) ? `${skuDe(l)}|${l.almacen}` : skuDe(l));
  const viejo = new Map(viejos.map((l) => [k(l), l]));
  const mio = new Map(mios.map((l) => [k(l), l]));
  const enServidor = new Set(nuevos.map(k));
  const salida: Renglon[] = [];
  for (const n of nuevos) {
    const v = viejo.get(k(n));
    const m = mio.get(k(n));
    if (renglonSalio(n)) { salida.push(n); continue; }   // ya salió: manda el servidor
    if (v && !m) continue;   // lo quité yo
    salida.push(m ? mezclarRenglon(v, m, n) : n);
  }
  const usados = new Set(salida.map((l) => l.uid));
  for (const m of mios) {
    if (enServidor.has(k(m))) continue;
    const v = viejo.get(k(m));
    // Ya no está en el servidor: o es mío y nuevo, o la otra persona lo quitó.
    if (v && mismoRenglon(v, m)) continue;
    salida.push(usados.has(m.uid) ? { ...m, uid: nuevoUid() } : m);
    usados.add(m.uid);
  }
  return salida;
}

/**
 * ¿Esta orden se edita? Lo decide EL PERMISO del backend (`permisos.editar`):
 * un borrador, o una CONFIRMADA mientras él diga que sí. Él es quien sabe del
 * rol, de la bandera, del «¿salió?» pendiente y de si la base ya tiene la 0071;
 * aquí no se vuelve a deducir nada de eso. Lo único que se revisa además es lo
 * que la base no le acepta a NADIE —una orden que ya se entregó o se canceló,
 * una borrada, una confirmada que espera el «¿salió?»—, para no pintar campos
 * que el trigger va a rechazar (el mismo cinturón que `accionesDe` le pone a
 * DELIVERED y a Cancelar con la marca del canal). El envío a FULL
 * (`tipo = 'full'`) se captura en Crear FULL: aquí sólo se consulta.
 */
export function seEdita(
  o: Pick<Orden, "estado" | "tipo" | "borrada_at" | "permisos"> & { canal_cancelo_at?: string | null },
): boolean {
  return !!o.permisos.editar && (o.estado === "borrador" || o.estado === "confirmada")
    && o.tipo !== "full" && !o.borrada_at
    && !(o.estado === "confirmada" && o.canal_cancelo_at);
}

/**
 * La orden cambió en el servidor mientras el usuario escribía: la forma nueva
 * es la del servidor, MÁS los campos que el usuario había tocado. Si ya no
 * puede editar (alguien la entregó o la canceló, o el canal la canceló con el
 * paquete en camino), lo suyo se descarta: no habría cómo guardarlo. Que otra
 * persona la CONFIRME ya no lo descarta: una confirmada se sigue editando.
 */
export function fusionar(baseVieja: Forma, forma: Forma, baseNueva: Forma, p: Pick<Permisos, "editar">): Forma {
  if (!p.editar) return baseNueva;
  const f: Forma = { ...baseNueva };
  for (const k of [...CAMPOS_TEXTO, ...CAMPOS_MP, ...CAMPOS_FECHA, "total", "comision"] as const) {
    if (forma[k] !== baseVieja[k]) f[k] = forma[k];
  }
  if (forma.precio_origen !== baseVieja.precio_origen) f.precio_origen = forma.precio_origen;
  if (!mismasLineas(baseVieja.lineas, forma.lineas)) {
    f.lineas = fusionarLineas(baseVieja.lineas, forma.lineas, baseNueva.lineas);
  }
  return f;
}

const CAMBIO = "La orden cambió mientras tanto; se recargó.";

/**
 * Lo que se le dice a quien estaba escribiendo cuando la orden cambió por
 * debajo. `baseVieja`/`formaVieja` son la foto de ANTES de releer; `baseNueva`
 * y `p`, lo que quedó. Si sus renglones se mezclaron con los de otra persona
 * se dice con todas sus letras: es el momento de revisar la tabla, no después
 * de guardar. `tras409` = venía de un Guardar que rebotó (hay que repetirlo).
 *
 * Y si, corrigiendo una confirmada, Bodega entregó algo mientras tanto: lo que
 * salió quedó congelado como está en el servidor, lo hubiera cambiado yo o no
 * (ver `fusionarLineas`). Ahí no se promete que «se conservó» lo escrito.
 */
export function avisoDeCambio(baseVieja: Forma, formaVieja: Forma, baseNueva: Forma,
                              p: Pick<Permisos, "editar"> | null, tras409: boolean): string {
  const habia = Object.keys(cambiosDe(baseVieja, formaVieja)).length > 0;
  if (!habia) return CAMBIO;
  if (!p?.editar) return `${CAMBIO} Lo que estabas escribiendo ya no se puede guardar.`;
  const tocoRenglones = !mismasLineas(baseVieja.lineas, formaVieja.lineas);
  const salidos = (lineas: Renglon[]) => lineas.filter(renglonSalio).length;
  if (tocoRenglones && salidos(baseNueva.lineas) > salidos(baseVieja.lineas)) {
    return `${CAMBIO} Ya salió parte de la orden: los renglones que salieron no se tocan y quedaron como están. `
      + (tras409 ? "Revisa los demás y vuelve a guardar." : "Revisa los demás antes de guardar.");
  }
  const fusionados = tocoRenglones && !mismasLineas(baseVieja.lineas, baseNueva.lineas);
  if (fusionados) {
    return `${CAMBIO} Tus renglones se fusionaron con los de la otra persona: `
      + (tras409 ? "revísalos y vuelve a guardar." : "revísalos antes de guardar.");
  }
  return `${CAMBIO} Lo que estabas escribiendo se conservó.${tras409 ? " Revisa y vuelve a intentarlo." : ""}`;
}

/**
 * Por qué NO se puede guardar así, o `null`. La bodega NO se exige aquí: un
 * borrador puede guardarse sin ella (la venta llegó y todavía no se decide de
 * dónde sale). Se exige al confirmar (ver `faltaParaConfirmar`) y, en una
 * confirmada, al guardar (ver `faltaParaGuardarConfirmada`).
 */
export function validar(f: Forma): string | null {
  const llaveDe = (l: Renglon) => `${skuDe(l)}|${l.almacen}`;
  const salidos = new Set(f.lineas.filter(renglonSalio).map(llaveDe));
  const vistos = new Set<string>();
  for (const l of f.lineas) {
    const c = numero(l.cantidad);
    if (!l.sku.trim()) return "Hay un renglón sin SKU.";
    if (c === null || !Number.isInteger(c) || c < 1) {
      return `Renglón ${l.sku}: la cantidad tiene que ser un entero mayor que cero.`;
    }
    if ((numero(l.precio) ?? 0) < 0) return `Renglón ${l.sku}: el precio no puede ser negativo.`;
    // La llave del renglón en la base es SKU + bodega (`ov_lineas_sku_alm_uq`):
    // dos iguales truenan al guardar, y aquí se dice cuál y qué hacer.
    const llave = llaveDe(l);
    if (vistos.has(llave)) {
      // Su gemelo ya salió: ése no se junta con nada ni se cambia (está congelado).
      if (salidos.has(llave)) {
        return `El renglón de ${l.sku} ya salió de ${l.almacen}: no se cambia. Si hace falta más, va en otra bodega o en otra orden.`;
      }
      return l.almacen
        ? `El SKU ${l.sku} está dos veces en la bodega ${l.almacen}: junta los dos renglones o cambia la bodega de uno.`
        : `El SKU ${l.sku} está dos veces sin bodega: junta los dos renglones o elige la bodega de cada uno.`;
    }
    vistos.add(llave);
  }
  if ((numero(f.total) ?? 0) < 0) return "El total no puede ser negativo.";
  if ((numero(f.comision) ?? 0) < 0) return "La comisión no puede ser negativa.";
  for (const [k, rotulo] of [["fecha_venta", "la fecha de la venta"],
                             ["entrega_limite", "la fecha de entrega a la paquetería"]] as const) {
    if (f[k] && !deInputLocal(f[k])) return `No se entiende ${rotulo}.`;
  }
  if (f.mp_orden.trim()) {
    // Con id de venta van los tres. Sin canal el barrido no puede cachar la
    // cancelación; sin cuenta la base ya no acepta la orden (antes sí).
    if (!f.mp_canal.trim()) return "Elige el canal de la orden de marketplace: canal, cuenta e id van juntos.";
    if (!f.mp_cuenta.trim()) return "Elige la cuenta de la orden de marketplace: canal, cuenta e id van juntos.";
  }
  return null;
}

/**
 * Por qué todavía NO se puede confirmar lo que está guardado, o `null`.
 * Confirmar aparta el stock de cada renglón EN SU BODEGA: sin bodega no hay
 * dónde apartar, y el servidor contestaría un rechazo genérico después de
 * hacer esperar. Aquí se dice qué renglón y qué hacer. Que el stock ALCANCE no
 * se revisa aquí: eso sólo lo sabe la base en el momento de apartar.
 */
export function faltaParaConfirmar(lineas: Pick<LineaOrden, "sku" | "almacen">[], elegibles: Bodega[]): string | null {
  if (!lineas.length) return "La orden no tiene renglones: agrega al menos un producto antes de confirmarla.";
  const sinBodega = lineas.filter((l) => !l.almacen);
  if (sinBodega.length) {
    const skus = sinBodega.slice(0, 4).map((l) => l.sku).join(", ") + (sinBodega.length > 4 ? "…" : "");
    return `${sinBodega.length === 1 ? "Falta elegir la bodega de un renglón" : `Falta elegir la bodega de ${sinBodega.length} renglones`} (${skus}). `
      + "Confirmar aparta el stock de cada renglón en su bodega: elígela, guarda y vuelve a confirmar.";
  }
  if (elegibles.length) {
    const fuera = lineas.find((l) => !elegibles.some((b) => b.codigo === l.almacen));
    if (fuera) {
      return `La bodega ${fuera.almacen} del renglón ${fuera.sku} no admite órdenes de venta: cámbiala por una que sí, guarda y vuelve a confirmar.`;
    }
  }
  return null;
}

/**
 * Por qué todavía NO se puede guardar el cambio de una CONFIRMADA, o `null`.
 *
 * A un borrador lo que le falte se le pide al confirmar. Una confirmada no
 * tiene ese segundo paso: ya aparta, y la base exige AL GUARDAR que le quede al
 * menos un renglón por entregar y que cada uno aparte su cantidad completa en
 * su bodega (`ov_coherente`). Aquí se dice antes de mandar, con el renglón y
 * qué hacer; el servidor lo revisa de todos modos. Que el stock ALCANCE no se
 * decide aquí: eso sólo lo sabe la base al mover el apartado (y lo dice el 409).
 *
 * Los renglones que ya salieron ni cuentan ni se revisan: están congelados. Y
 * que la bodega admita órdenes sólo se exige cuando los renglones CAMBIAN, que
 * es cuando el servidor los vuelve a apartar: corregir la guía de una orden
 * cuya bodega se apagó después no tiene por qué rebotar.
 */
export function faltaParaGuardarConfirmada(base: Forma, f: Forma, elegibles: Bodega[]): string | null {
  const pendientes = f.lineas.filter((l) => !renglonSalio(l));
  if (!pendientes.length) {
    // Las mismas palabras del servidor: sin renglones por entregar no hay nada
    // que apartar, y eso ya no es corregir la orden: es cerrarla.
    return "Una orden confirmada necesita al menos un renglón por entregar. "
      + (f.lineas.some(renglonSalio) ? "Si ya salió todo, márcala DELIVERED." : "Si ya no va, cancélala.");
  }
  const sinBodega = pendientes.filter((l) => !l.almacen);
  if (sinBodega.length) {
    const skus = sinBodega.slice(0, 4).map((l) => l.sku).join(", ") + (sinBodega.length > 4 ? "…" : "");
    return `${sinBodega.length === 1 ? "Falta elegir la bodega de un renglón" : `Falta elegir la bodega de ${sinBodega.length} renglones`} (${skus}). `
      + "En una orden confirmada cada renglón por entregar aparta su stock en su bodega: elígela y vuelve a guardar.";
  }
  if (elegibles.length && !mismasLineas(base.lineas, f.lineas)) {
    const fuera = pendientes.find((l) => !elegibles.some((b) => b.codigo === l.almacen));
    if (fuera) {
      return `La bodega ${fuera.almacen} del renglón ${fuera.sku} no admite órdenes de venta: cámbiala por una que sí y vuelve a guardar.`;
    }
  }
  return null;
}

/** Lo que contestó una relectura: nada nuevo, la orden cambió, o no se pudo leer. */
export type Relectura = "igual" | "cambio" | "fallo";

const PAUSA_TRAS_FALLO_MS = 1_500;

/**
 * Relee la orden HASTA ALCANZAR la `rev` que se avisó.
 *
 * Por qué no basta una relectura: `releer` devuelve la lectura que ya iba en
 * vuelo, y ésa pudo salir ANTES del cambio que ahora se avisa. Dos cambios
 * seguidos de otra persona («Guardar y continuar» son un PUT y un confirmar
 * separados por un viaje de red) dejaban el documento en la rev intermedia
 * —BORRADOR en pantalla, CONFIRMADA en la base— sin límite de tiempo. Lo mismo
 * si la única relectura fallaba (un 502 en pleno deploy): nadie reintentaba.
 *
 * Aquí se comprueba contra `objetivo` y se insiste: enseguida si la lectura se
 * quedó corta, tras una pausa si falló, y hasta `intentos` veces MÁS. No lleva
 * nada de React (todo entra por parámetro) para poder probarla sin navegador.
 */
export async function releerHasta(e: {
  /** La `rev` a la que hay que llegar (0 = con una relectura basta). */
  objetivo: number;
  /** Cuántas veces MÁS se relee si no se alcanzó. */
  intentos: number;
  releer: () => Promise<Relectura>;
  /** La `rev` que el documento tiene en pantalla. `null` = ya no está (se cerró o es otra orden). */
  rev: () => number | null;
  /** ¿Hay una acción mía en vuelo? Entonces no se relee: se apunta para cuando termine. */
  ocupado: () => boolean;
  apuntar: (rev: number) => void;
  /** La relectura trajo un cambio: avisarle a quien mira. */
  alCambio: () => void;
  esperar: (ms: number) => Promise<void>;
}): Promise<void> {
  const falta = () => {
    const rev = e.rev();
    return rev !== null && rev < e.objetivo;
  };
  for (let quedan = e.intentos; ; quedan -= 1) {
    const r = await e.releer();
    if (e.rev() === null) return;
    if (r === "cambio") e.alCambio();
    if (quedan <= 0 || !falta()) return;
    await e.esperar(r === "fallo" ? PAUSA_TRAS_FALLO_MS : 0);
    if (!falta()) return;
    if (e.ocupado()) { e.apuntar(e.objetivo); return; }
  }
}

/**
 * Una fecha a medias, o `null`. Un `datetime-local` con el día puesto y la
 * hora en «--:--» reporta `value = ""` y NO dispara `input`: React nunca se
 * entera, el formulario cree que el campo está vacío y la orden se guardaría
 * SIN esa fecha aunque la persona vio un día escrito. Lo único que lo sabe es
 * el propio control (`validity.badInput`), así que antes de guardar se le
 * pregunta a él.
 */
function fechaIncompleta(): string | null {
  if (typeof document === "undefined") return null;
  const campos = Array.from(document.querySelectorAll<HTMLInputElement>("input[data-ov-fecha]"));
  const mala = campos.find((el) => el.validity.badInput);
  return mala
    ? `«${mala.dataset.ovFecha}» está incompleta: le falta la hora. Complétala (o bórrala) y vuelve a intentarlo: así como está, la fecha no se guardaría.`
    : null;
}

/** Lo que queda ESCRITO en la descripción cuando la venta trae renglones sin SKU. */
export const notaSinSku = (n: number): string =>
  `OJO: la venta trae ${n} renglón(es) SIN SKU que no entraron a esta orden. Revísala contra el marketplace.`;

/**
 * Prellena con una venta de marketplace: lo que la venta SÍ trae pisa lo
 * capturado; lo que no trae (`null`) deja lo que había. El total queda
 * marcado como precio del marketplace.
 *
 * La venta NO dice de qué bodega sale (eso se decide aquí, renglón por
 * renglón): sus renglones entran SIN bodega, salvo que sólo haya una donde
 * elegir (`elegibles`), que entonces no hay nada que decidir.
 *
 * Si de la orden YA SALIÓ algún renglón (una confirmada con entrega parcial),
 * la venta NO cambia los renglones: cambiarlos por los de la venta borraría del
 * formulario lo que ya salió —que está congelado— y no hay cómo saber qué parte
 * de la venta es lo entregado. Trae lo demás (precio, guía, fechas) y lo que
 * falta por entregar se corrige a mano.
 */
export function aplicarVenta(f: Forma, v: VentaMarketplace, elegibles: Bodega[] = []): Forma {
  // El cliente sólo se cambia si nadie lo escribió (vacío o el rótulo del canal anterior).
  const clienteAuto = !f.cliente.trim() || f.cliente.trim() === rotuloCanal(f.canal);
  const unica = elegibles.length === 1 ? elegibles[0].codigo : "";
  let lineas = f.lineas;
  if (v.lineas.length && !f.lineas.some(renglonSalio)) {
    const previos = new Map(f.lineas.map((l) => [skuDe(l), l]));
    const juntos = new Map<string, Renglon>();
    for (const l of v.lineas) {
      const k = skuDe(l);
      const ya = juntos.get(k);
      // Un SKU dos veces en la venta = un renglón con la suma.
      if (ya) { ya.cantidad = String((numero(ya.cantidad) ?? 0) + l.cantidad); continue; }
      const p = previos.get(k);
      // Si algún día la venta trae bodega, sólo vale una donde se pueda hacer la orden.
      const deLaVenta = l.almacen && elegibles.some((b) => b.codigo === l.almacen) ? l.almacen : "";
      juntos.set(k, renglonNuevo({
        sku: l.sku.trim(), titulo: l.titulo ?? p?.titulo ?? null, imagen: l.imagen ?? p?.imagen ?? null,
        cantidad: String(l.cantidad), precio: Number(l.precio_unitario).toFixed(2),
        almacen: deLaVenta || p?.almacen || unica,
        saldos: p?.saldos ?? {}, conocido: p?.conocido ?? null,
      }));
    }
    lineas = [...juntos.values()];
  }
  // Renglones de la venta SIN SKU: no entran a `lineas` (no hay qué surtir por
  // SKU), así que la orden nace con MENOS piezas que la venta. El aviso ámbar
  // de pantalla se va al guardar; la descripción se queda, y es lo que lee
  // almacén. Sólo si nadie escribió una: lo capturado no se pisa.
  const sinSku = v.renglones_sin_sku ?? 0;
  return {
    ...f,
    descripcion: sinSku > 0 && !f.descripcion.trim() ? notaSinSku(sinSku) : f.descripcion,
    canal: v.canal || f.canal,
    cliente: clienteAuto && v.canal ? rotuloCanal(v.canal) : f.cliente,
    // La cuenta es obligatoria con el id: si la venta no la dijera y el canal sólo tiene una, es ésa.
    mp_canal: v.canal, mp_cuenta: v.cuenta || cuentaUnica(v.canal), mp_orden: v.orden,
    guia: v.guia ?? f.guia, paqueteria: v.paqueteria ?? f.paqueteria,
    fecha_venta: v.fecha ? aInputLocal(v.fecha) : f.fecha_venta,
    entrega_limite: v.entrega_limite ? aInputLocal(v.entrega_limite) : f.entrega_limite,
    total: v.total !== null ? Number(v.total).toFixed(2) : f.total,
    comision: v.comision !== null ? Number(v.comision).toFixed(2) : f.comision,
    precio_origen: v.total !== null ? "marketplace" : f.precio_origen,
    lineas,
  };
}

/** «02 oct 2026, 12:09» en hora de CDMX, a partir del valor de un datetime-local. */
function fechaLegible(valorInput: string): string {
  const iso = deInputLocal(valorInput);
  return iso ? `${fechaLarga(iso)}, ${fechaHora(iso).slice(-5)}` : "—";
}

// ── La entrega (DELIVERED), por renglón ───────────────────────────────────────

/** Los renglones que TODAVÍA no salen: los únicos que se pueden entregar (cada uno, una sola vez). */
export function renglonesPendientes(lineas: LineaOrden[]): LineaOrden[] {
  return lineas.filter((l) => (l.entregado ?? null) === null && !l.entregado_at)
    .sort((a, b) => a.linea - b.linea);
}

/**
 * Las piezas que «Salió tarde» va a dar por salidas: sólo las de los renglones
 * que seguían pendientes. Un renglón que ya se cerró en una entrega anterior
 * (aunque fuera con 0) no se vuelve a tocar, así que `orden.piezas` diría de más.
 */
export function piezasPorSalir(lineas: LineaOrden[]): number {
  return renglonesPendientes(lineas).reduce((s, l) => s + l.cantidad, 0);
}

/** Un renglón pendiente en el diálogo de la entrega. `n` es texto: lo que se está tecleando. */
export interface FilaEntrega {
  id: number;
  sku: string;
  cantidad: number;
  /** ¿Sale AHORA? Sin marcar, el renglón sigue apartado para una entrega posterior. */
  marcado: boolean;
  /** Piezas que salieron: 0..cantidad. */
  n: string;
}

export type PlanEntrega =
  | { ok: false; error: string }
  | {
    ok: true;
    /** Lo que se manda. `undefined` = «todo lo pendiente, completo» (el servidor no necesita la lista). */
    lineas: EntregaLinea[] | undefined;
    /** Piezas que salen en esta entrega. */
    salen: number;
    /** Piezas de los renglones marcados que NO salen: se sueltan y vuelven a quedar libres. */
    sueltan: number;
    /** Renglones que siguen apartados (no se marcaron). */
    quedan: number;
    /** ¿La orden pasa a DELIVERED? Sí cuando no queda ningún renglón por salir. */
    cierra: boolean;
  };

/**
 * Qué se manda al marcar DELIVERED, y qué va a pasar.
 *
 * Dos cosas que parecen la misma y no lo son:
 *   · un renglón SIN MARCAR no sale ahora: sigue apartado y la orden se queda
 *     CONFIRMADA (entrega parcial); se entrega después;
 *   · un renglón MARCADO con menos piezas sale con ésas y lo demás se SUELTA
 *     para siempre: ese renglón ya no se vuelve a tocar.
 *
 * Si todo lo pendiente sale completo no se manda la lista: el servidor entrega
 * «todo lo que falta», y así no hay cómo mandarle una cuenta distinta de la suya.
 * `yaSalieron` = piezas entregadas en entregas anteriores de la misma orden.
 */
export function planDeEntrega(filas: FilaEntrega[], yaSalieron: number): PlanEntrega {
  if (!filas.length) return { ok: false, error: "No queda ningún renglón por salir." };
  const marcadas = filas.filter((f) => f.marcado);
  if (!marcadas.length) return { ok: false, error: "Marca al menos un renglón: ¿cuál salió?" };
  const lineas: EntregaLinea[] = [];
  let salen = 0;
  let sueltan = 0;
  for (const f of marcadas) {
    const n = numero(f.n);
    if (n === null || !Number.isInteger(n) || n < 0 || n > f.cantidad) {
      return { ok: false,
               error: `Renglón ${f.sku}: las piezas que salieron van de 0 a ${f.cantidad} (enteras).` };
    }
    lineas.push({ id: f.id, n });
    salen += n;
    sueltan += f.cantidad - n;
  }
  const quedan = filas.length - marcadas.length;
  const cierra = quedan === 0;
  // Una orden «entregada» de la que no salió nada no es una entrega: es una cancelación.
  if (cierra && salen + yaSalieron === 0) {
    return { ok: false,
             error: "No salió ninguna pieza: eso no es una entrega. Si la orden ya no va a salir, cancélala." };
  }
  return { ok: true, lineas: cierra && sueltan === 0 ? undefined : lineas, salen, sueltan, quedan, cierra };
}

// ── Acciones ──────────────────────────────────────────────────────────────────

type AccionSalio = "salio_si" | "salio_no";
type Accion = "crear" | "guardar" | "confirmar" | "entregar" | "cancelar" | "borrar" | AccionSalio | "salio_tarde";
type AccionOrden = Exclude<Accion, "crear" | "guardar">;

/** Qué significa cada estado de la devolución (en entregada y en DELIVERED but CANCELLED). */
export const AYUDA_DEVOLUCION: Record<DevolucionEstado, string> = {
  pendiente: "El producto ya salió y tiene que regresar al almacén.",
  recibida: "El producto ya regresó al almacén.",
  cerrada: "La devolución se cerró: no queda nada pendiente.",
};

/** La nota fija que acompaña a la devolución: aquí sólo se MUESTRA su estado. */
const NOTA_DEVOLUCIONES = "El proceso de devoluciones se define aparte.";

// Una CONFIRMADA sí se corrige (0071). Cuando ÉSTA no se puede, el porqué es el
// del backend (`permisos.porque.editar`): el «¿salió?» pendiente, el modo
// prueba, el rol, la migración que falta. Esto es sólo por si llegara sin él.
const NOTA_CONFIRMADA_FIJA = "Esta orden confirmada no se puede editar ahora.";

// Lo que se le avisa a quien SÍ puede corregir una confirmada: que no es un
// borrador. Guardar aquí mueve stock apartado y deja rastro de quién lo hizo.
const NOTA_EDITAR_CONFIRMADA = "Esta orden ya está confirmada y se puede corregir. Al guardar se vuelve a apartar el stock, "
  + "todo o nada: si un renglón no alcanza, no se guarda ningún cambio. Cada cambio queda en la bitácora con quién lo hizo.";

// Lo que YA SALIÓ no se arregla con «borrar y capturar de nuevo»: borrar no
// regresa las piezas al saldo (la salida se queda en el libro) y la recaptura
// apartaría y sacaría OTRA vez el mismo paquete. Por eso tiene su propia nota.
const NOTA_YA_SALIO = "Esta orden ya salió de la bodega: no se modifica. Borrarla no regresa las piezas al saldo ni es para capturarla de nuevo.";

/** ¿De esta orden ya salió algo de la bodega? (entregada, DELIVERED but CANCELLED o una entrega parcial). */
export function yaSalioAlgo(o: Pick<Orden, "estado" | "piezas_entregadas">): boolean {
  return o.estado === "entregada" || o.estado === "entregada_cancelada" || (o.piezas_entregadas ?? 0) > 0;
}

/**
 * Por qué este documento es de sólo lectura, o `null` si no aplica (se edita, o
 * es un borrador: de ése lo dice la tarjeta de datos). En una CONFIRMADA sólo
 * hay nota cuando de verdad no se puede editar, y es el porqué del backend: ya
 * no existe el «una confirmada no se modifica» de antes.
 */
export function notaDeLectura(o: Pick<Orden, "estado" | "tipo" | "borrada_at" | "permisos">): string | null {
  if (o.borrada_at) return null;   // el aviso de «orden borrada» ya lo dice
  if (o.tipo === "full") return "Es un envío a FULL: se captura en Crear FULL y aquí sólo se consulta.";
  if (o.estado === "borrador") return null;
  if (o.estado === "cancelada") return "Una orden cancelada ya no se modifica: si hace falta, se captura de nuevo.";
  if (o.estado === "entregada" || o.estado === "entregada_cancelada") return NOTA_YA_SALIO;
  if (seEdita(o)) return null;
  return o.permisos.porque.editar?.trim() || NOTA_CONFIRMADA_FIJA;
}

/**
 * Lo que se le avisa, discreto, a quien SÍ puede corregir una CONFIRMADA, o
 * `null` (un borrador no lo necesita: guardarlo no mueve nada). Con una entrega
 * parcial dice además que lo que ya salió no se toca.
 */
export function notaDeEdicion(o: Pick<Orden, "estado" | "tipo" | "borrada_at" | "permisos" | "lineas">): string | null {
  if (o.estado !== "confirmada" || !seEdita(o)) return null;
  const salieron = renglonesPendientes(o.lineas).length < o.lineas.length;
  return NOTA_EDITAR_CONFIRMADA + (salieron ? " Los renglones que ya salieron no se tocan." : "");
}

/** Cuántos caracteres exige la base de motivo para cancelar o borrar ESTA orden. */
export function minimoDeMotivo(accion: "cancelar" | "borrar", o: Pick<Orden, "confirmada_at">): number {
  if (accion === "borrar") return MOTIVO_BORRAR;
  return o.confirmada_at ? MOTIVO_CANCELAR_CONFIRMADA : MOTIVO_MINIMO;
}

/** A dónde quería ir el usuario cuando se le preguntó por sus cambios sin guardar. */
type Destino =
  | { tipo: "accion"; accion: AccionOrden }
  | { tipo: "salir" }
  | { tipo: "abrir"; folio: string };

/**
 * El diálogo abierto. El de una ACCIÓN guarda la `rev` de la orden que la
 * persona tenía delante cuando lo abrió: con ésa viaja su acción, no con la de
 * ahora (ver `revDeDialogo`).
 */
type Dialogo =
  | { tipo: AccionOrden; rev: number }
  | { tipo: "sucio"; destino: Destino };

/**
 * Con qué `rev` viaja la acción de un diálogo: con la de la orden que la
 * persona tenía DELANTE al abrirlo (`d.rev`), no con la de ahora (`actual`).
 *
 * Por qué: un diálogo abierto no detiene al documento. Si otra persona corrige
 * la orden mientras tanto —desde la 0071 también una confirmada—, el chat
 * avisa, el documento relee y el diálogo SE REPINTA solo con lo nuevo: renglones
 * y piezas que quien va a dar clic no vio al abrirlo. Con la `rev` de ahora el
 * candado dejaba pasar justo lo que tenía que atajar: «Marcar DELIVERED» abierto
 * sobre 2 piezas registraba la salida de 8, y el físico bajaba 8 habiendo salido
 * 2, en una orden que ya entregada no se corrige. Con la `rev` de al abrir, el
 * servidor contesta su 409 «la orden cambió», no pasa nada, y el diálogo se
 * vuelve a abrir ya sobre lo que hay. Vale igual para confirmar, cancelar,
 * borrar, «¿salió?» y «salió tarde»: quien da clic confirma lo que VIO.
 *
 * Si no hay un diálogo de ESA acción abierto (no debería: sus botones viven
 * dentro de él) queda la `rev` de ahora, que es lo que el servidor va a revisar.
 */
export function revDeDialogo(d: Dialogo | null, accion: AccionOrden, actual: number): number {
  return d && d.tipo === accion && Number.isInteger(d.rev) ? d.rev : actual;
}

type Carga =
  | { estado: "lista" }
  | { estado: "cargando" }
  | { estado: "no_existe" }
  | { estado: "error"; mensaje: string };

/** Lo que contestó «Traer venta». */
type Traida =
  | { estado: "buscando" }
  | { estado: "falla"; mensaje: string }
  | { estado: "elegir"; ventas: VentaMarketplace[] }
  | { estado: "aplicada"; venta: VentaMarketplace };

const NO_SE_PUDO: Record<Accion, string> = {
  crear: "No se pudo crear la orden (el servidor no contestó).",
  guardar: "No se pudieron guardar los cambios (el servidor no contestó).",
  confirmar: "No se pudo confirmar la orden (el servidor no contestó).",
  entregar: "No se pudo marcar DELIVERED (el servidor no contestó).",
  cancelar: "No se pudo cancelar la orden (el servidor no contestó).",
  borrar: "No se pudo borrar la orden (el servidor no contestó).",
  salio_si: "No se pudo registrar que el paquete salió (el servidor no contestó).",
  salio_no: "No se pudo registrar que el paquete no salió (el servidor no contestó).",
  salio_tarde: "No se pudo registrar la salida tardía (el servidor no contestó).",
};

const ESPERA_SALIO = "El canal canceló esta venta: primero hay que contestar si el paquete salió.";

export interface BotonAccion {
  accion: AccionOrden;
  rotulo: string;
  icono: LucideIcon;
  tono: "primario" | "secundario" | "peligro" | "exito";
  puede: boolean;
  porque?: string;
  /** Una línea que explica la acción en el menú «…» (las de la barra se explican solas). */
  nota?: string;
}

/**
 * Qué va en la barra y qué en el menú «…», según el estado y lo que el backend
 * permite. Lo que ya NO existe no aparece ni apagado: regresar a borrador,
 * reintentar la reserva y mover la devolución se fueron con el modelo viejo.
 */
export function accionesDe(o: Orden): { barra: BotonAccion[]; menu: BotonAccion[] } {
  const barra: BotonAccion[] = [];
  const menu: BotonAccion[] = [];
  if (o.borrada_at) return { barra, menu };
  const p = o.permisos;
  const cancelar: BotonAccion = { accion: "cancelar", rotulo: "Cancelar orden", icono: Ban, tono: "peligro",
                                  puede: p.cancelar, porque: p.porque.cancelar };
  if (o.estado === "borrador") {
    barra.push({ accion: "confirmar", rotulo: "Confirmar y apartar", icono: PackageCheck, tono: "primario",
                 puede: p.confirmar, porque: p.porque.confirmar });
    barra.push(cancelar);
  } else if (o.estado === "confirmada") {
    // Con la marca del canal la base ya no deja pasar a DELIVERED
    // (`ov_ordenes_canal_cancelo_chk`): el botón se apaga aquí aunque el
    // permiso llegara encendido, y dice que primero va el «¿salió?».
    const espera = !!o.canal_cancelo_at;
    barra.push({ accion: "entregar", rotulo: "Marcar DELIVERED", icono: Truck, tono: "exito",
                 puede: p.entregar && !espera,
                 porque: espera ? (p.porque.entregar || ESPERA_SALIO) : p.porque.entregar });
    // Cancelar una confirmada ya no es sólo de admin: lo decide el permiso
    // (hoy, cualquiera que escribe). Va a la barra si ESTE usuario puede; si
    // no, al menú, apagado y con el porqué del backend (no estorba, pero
    // tampoco se esconde).
    // Con la marca tampoco se cancela a mano: sería una tercera salida que se
    // salta la pregunta (quedaría como cancelación «manual», sin la referencia
    // del canal). «No salió» hace lo mismo y lo deja bien anotado. Va al menú,
    // apagado y con su porqué, tenga el permiso que tenga quien mira.
    if (espera) menu.push({ ...cancelar, puede: false, porque: ESPERA_SALIO });
    else (p.cancelar ? barra : menu).push(cancelar);
  } else if (o.estado === "cancelada" && o.confirmada_at) {
    // Sólo donde tiene sentido: una cancelada que SÍ estuvo confirmada (la que
    // se canceló en borrador nunca apartó nada, no pudo salir).
    menu.push({ accion: "salio_tarde", rotulo: "Salió tarde", icono: PackageX, tono: "secundario",
                puede: p.salio_tarde, porque: p.porque.salio_tarde,
                nota: "El paquete sí había salido: pasa a DELIVERED but CANCELLED." });
  }
  menu.push({ accion: "borrar", rotulo: "Borrar la orden", icono: Trash2, tono: "peligro",
              puede: p.borrar, porque: p.porque.borrar });
  return { barra, menu };
}

/** Cuántos SKUs se le preguntan al catálogo de una sentada (una venta trae 1–3 renglones). */
const SALDOS_POR_TANDA = 8;

// ── El documento ──────────────────────────────────────────────────────────────

export function OrdenDocumento({ refOrden, prefill, modulo, sucioRef, onCerrar, onCreada, onCambio }: {
  /** Folio o id de la orden. `null` = orden nueva. */
  refOrden: string | null;
  /** Una venta de marketplace con la que nace prellenada la orden nueva. */
  prefill: VentaMarketplace | null;
  modulo: EstadoModulo;
  /**
   * ¿Hay cambios sin guardar? Lo ESCRIBE el documento y lo LEE la página, que
   * es quien ve «Atrás» del navegador y los clics en las pestañas y el navbar:
   * nada de eso pasa por los botones de aquí. Es un ref y no un estado porque
   * se consulta dentro de un evento, no para pintar.
   */
  sucioRef?: MutableRefObject<boolean>;
  onCerrar: () => void;
  onCreada: (o: Orden) => void;
  onCambio: (o: Orden) => void;
}): JSX.Element {
  // El catálogo de bodegas y las que se pueden elegir en un renglón. Entran
  // también por ref: las usan funciones que terminan después de un `await`.
  const bodegas = useMemo(() => modulo.bodegas ?? [], [modulo.bodegas]);
  const elegibles = useMemo(() => bodegasDeOrdenes(bodegas), [bodegas]);
  const bodegasRef = useRef(bodegas);
  bodegasRef.current = bodegas;
  const elegiblesRef = useRef(elegibles);
  elegiblesRef.current = elegibles;

  const [orden, setOrden] = useState<Orden | null>(null);
  const [carga, setCarga] = useState<Carga>(refOrden === null ? { estado: "lista" } : { estado: "cargando" });
  // La orden nueva arranca YA prellenada con su venta: si se esperara al efecto,
  // el formulario se pintaría vacío un cuadro y luego brincaría.
  const [arranque] = useState<Forma>(() => (
    refOrden === null && prefill ? aplicarVenta(formaVacia(), prefill, elegibles) : formaVacia()));
  const [base, setBase] = useState<Forma>(arranque);
  const [forma, setForma] = useState<Forma>(arranque);
  const [ocupado, setOcupado] = useState<Accion | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dialogo, setDialogo] = useState<Dialogo | null>(null);
  const [menu, setMenu] = useState(false);
  const [traida, setTraida] = useState<Traida | null>(() => (
    refOrden === null && prefill ? { estado: "aplicada", venta: prefill } : null));
  const [intento, setIntento] = useState(0);
  const [altoBarra, setAltoBarra] = useState(62);

  // Espejos para lo que termina DESPUÉS de un `await`: el estado de React de
  // ese render ya es viejo, y decidir con él confirmaría con la `rev` anterior.
  const ordenRef = useRef<Orden | null>(null);
  const baseRef = useRef<Forma>(base);
  const formaRef = useRef<Forma>(forma);
  const ocupadoRef = useRef<Accion | null>(null);
  /** La clave del alta, atada a lo que se mandó con ella (ver `claveDeIntento`). */
  const claveRef = useRef<ClaveIntento | null>(null);
  const revPendiente = useRef(0);
  const releyendo = useRef<Promise<Relectura> | null>(null);
  const busqueda = useRef<AbortController | null>(null);
  /** Lo que el catálogo ya contestó del saldo de cada SKU, y a cuáles ya se les preguntó (por documento). */
  const cacheSaldos = useRef(new Map<string, Saldos>());
  const preguntados = useRef(new Set<string>());
  /** Cambia con cada documento: una respuesta del catálogo que llega tarde no se pinta en otra orden. */
  const documento = useRef(0);
  const vivo = useRef(true);
  const barra = useRef<HTMLDivElement>(null);
  const cajaMenu = useRef<HTMLDivElement>(null);
  const alCambiar = useRef(onCambio);
  alCambiar.current = onCambio;

  useEffect(() => {
    vivo.current = true;
    return () => {
      vivo.current = false;
      busqueda.current?.abort();
    };
  }, []);

  const cambiar = useCallback((fn: (f: Forma) => Forma) => {
    const s = fn(formaRef.current);
    formaRef.current = s;
    setForma(s);
  }, []);

  /**
   * Le pone a cada renglón lo que el catálogo ya dijo de su SKU. El servidor
   * sólo manda el saldo de la bodega DEL renglón: sin esto, cada guardado
   * olvidaría lo que ya se sabía de las demás bodegas. Lo del servidor es más
   * fresco y gana.
   */
  const conCache = useCallback((f: Forma): Forma => ({
    ...f,
    lineas: f.lineas.map((l) => {
      const saldos = { ...cacheSaldos.current.get(skuDe(l)), ...l.saldos };
      cacheSaldos.current.set(skuDe(l), saldos);
      return { ...l, saldos };
    }),
  }), []);

  /** Pone en pantalla lo que contestó el servidor. `conservar` = no pisar lo que se está escribiendo. */
  const aplicar = useCallback((o: Orden, conservar: boolean) => {
    const nuevaBase = conCache(formaDe(o));
    const siguiente = conservar && ordenRef.current?.id === o.id
      ? fusionar(baseRef.current, formaRef.current, nuevaBase, { editar: seEdita(o) })
      : nuevaBase;
    ordenRef.current = o;
    baseRef.current = nuevaBase;
    formaRef.current = siguiente;
    setOrden(o);
    setBase(nuevaBase);
    setForma(siguiente);
  }, [conCache]);

  // ── Carga (o arranque en blanco) ────────────────────────────────────────────
  useEffect(() => {
    setError(null);
    setDialogo(null);
    setMenu(false);
    busqueda.current?.abort();
    const otroDocumento = () => {
      documento.current += 1;
      cacheSaldos.current = new Map();
      preguntados.current = new Set();
      revPendiente.current = 0;
    };
    if (refOrden === null) {
      // Orden nueva: en blanco, o con la venta que se eligió en «Ventas de marketplace».
      otroDocumento();
      const inicial = prefill ? aplicarVenta(formaVacia(), prefill, elegiblesRef.current) : formaVacia();
      ordenRef.current = null;
      baseRef.current = inicial;
      formaRef.current = inicial;
      claveRef.current = null;   // otro formulario = otra clave de alta
      setOrden(null);
      setBase(inicial);
      setForma(inicial);
      setTraida(prefill ? { estado: "aplicada", venta: prefill } : null);
      setCarga({ estado: "lista" });
      return;
    }
    // La que se acaba de crear aquí mismo ya está en pantalla: no se relee.
    const o = ordenRef.current;
    if (o && (o.folio === refOrden || String(o.id) === refOrden)) {
      setCarga({ estado: "lista" });
      return;
    }
    otroDocumento();
    ordenRef.current = null;
    setOrden(null);
    setTraida(null);
    setCarga({ estado: "cargando" });
    const ctrl = new AbortController();
    leerOrden(refOrden, ctrl.signal)
      .then((leida) => {
        aplicar(leida, false);
        setCarga({ estado: "lista" });
      })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        if (e instanceof ApiError && e.status === 404) setCarga({ estado: "no_existe" });
        else setCarga({ estado: "error", mensaje: mensajeDeError(e, "El servidor no contestó (puede estar reiniciando tras un deploy).") });
      });
    return () => ctrl.abort();
  }, [refOrden, prefill, intento, aplicar]);

  // El aviso de abajo se va solo.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  // La barra puede partirse en dos renglones: el chat pegajoso se acomoda a su alto real.
  useEffect(() => {
    const el = barra.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const medir = () => setAltoBarra(el.offsetHeight);
    const ro = new ResizeObserver(medir);
    ro.observe(el);
    medir();
    return () => ro.disconnect();
  }, []);

  // El menú «…» se cierra con clic fuera o Esc.
  useEffect(() => {
    if (!menu) return;
    const fuera = (ev: MouseEvent) => {
      if (cajaMenu.current && !cajaMenu.current.contains(ev.target as Node)) setMenu(false);
    };
    const tecla = (ev: KeyboardEvent) => { if (ev.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", fuera);
    document.addEventListener("keydown", tecla);
    return () => {
      document.removeEventListener("mousedown", fuera);
      document.removeEventListener("keydown", tecla);
    };
  }, [menu]);

  // ── Qué se puede ────────────────────────────────────────────────────────────
  const nueva = orden === null && refOrden === null;
  const borrada = !!orden?.borrada_at;
  const esFull = orden?.tipo === "full";
  /** Se editan el borrador y la confirmada (encabezado y renglones a la vez), si el permiso lo dice: ver `seEdita`. */
  const puedeEditar = orden ? seEdita(orden) : nueva && modulo.yo.escribe && !modulo.falta_migracion;
  const bloqueado = ocupado !== null;
  /** Sin permiso: el campo se pinta como dato («—» si está vacío). */
  const lectura = !puedeEditar;
  const notaLectura = orden ? notaDeLectura(orden) : null;
  /** Se está corrigiendo una CONFIRMADA: guardar mueve el apartado (no es un borrador). */
  const notaEdicion = orden ? notaDeEdicion(orden) : null;
  const porqueLectura = orden
    ? (notaLectura ?? orden.permisos.porque.editar ?? "")
    : modulo.falta_migracion ? (modulo.motivo || "Faltan las migraciones 0064/0065 en la base.")
      : "Tu usuario es de sólo lectura: no puede crear órdenes.";

  const cambios = useMemo(() => cambiosDe(base, forma), [base, forma]);
  const sucio = puedeEditar && Object.keys(cambios).length > 0;

  // Cerrar la pestaña con cambios sin guardar: que el navegador pregunte.
  useEffect(() => {
    if (!sucio) return;
    const antes = (ev: BeforeUnloadEvent) => { ev.preventDefault(); ev.returnValue = ""; };
    window.addEventListener("beforeunload", antes);
    return () => window.removeEventListener("beforeunload", antes);
  }, [sucio]);

  // `beforeunload` sólo cubre cerrar o recargar. «Atrás», las pestañas de
  // Inventario y el navbar son navegación de cliente: las ataja la página, que
  // lee este ref. Al desmontarse el documento ya no hay nada que perder.
  useEffect(() => {
    if (!sucioRef) return;
    sucioRef.current = sucio;
    return () => { sucioRef.current = false; };
  }, [sucio, sucioRef]);

  const moneda = orden?.moneda || "MXN";
  const suma = useMemo(() => sumaDe(forma.lineas), [forma.lineas]);
  const totalCapturado = numero(forma.total);
  const total = totalCapturado ?? suma;
  const comision = numero(forma.comision) ?? 0;
  const piezas = forma.lineas.reduce((a, l) => a + (numero(l.cantidad) ?? 0), 0);

  // ── Saldos que todavía no se saben ──────────────────────────────────────────
  // Un renglón que vino de una venta (o al que se le va a elegir bodega) no
  // trae su saldo: se le pregunta al catálogo UNA vez por SKU, para que «libre
  // N» se vea ANTES de guardar. Si la pregunta falla se queda en «sin dato»; el
  // servidor lo dice de todos modos al guardar.
  const ponerSaldos = useCallback((sku: string, saldos: Saldos) => {
    cacheSaldos.current.set(sku, { ...cacheSaldos.current.get(sku), ...saldos });
    const poner = (f: Forma): Forma => ({
      ...f,
      lineas: f.lineas.map((l) => (
        skuDe(l) === sku ? { ...l, saldos: { ...l.saldos, ...saldos }, conocido: l.conocido ?? true } : l)),
    });
    // También en `base`: es informativo (no cuenta como cambio) y «Deshacer» vuelve a ella.
    baseRef.current = poner(baseRef.current);
    setBase(baseRef.current);
    cambiar(poner);
  }, [cambiar]);

  const sinSaldo = useMemo(() => (puedeEditar ? skusSinSaldo(forma.lineas, elegibles) : []),
                           [puedeEditar, forma.lineas, elegibles]);
  const claveSinSaldo = sinSaldo.join("\n");
  useEffect(() => {
    if (!claveSinSaldo) return;
    const doc = documento.current;
    const tanda = claveSinSaldo.split("\n").filter((s) => !preguntados.current.has(s)).slice(0, SALDOS_POR_TANDA);
    for (const sku of tanda) {
      preguntados.current.add(sku);
      buscarSkus(sku)
        .then((r) => {
          if (!vivo.current || documento.current !== doc) return;
          const o = (r.opciones ?? []).find((x) => skuDe(x) === sku);
          if (o) ponerSaldos(sku, saldosDe(o.existencias, bodegasRef.current));
        })
        .catch(() => { /* se queda en «sin dato» */ });
    }
  }, [claveSinSaldo, ponerSaldos]);

  /**
   * Vuelve a preguntarle al catálogo el saldo de los renglones que todavía no
   * están guardados donde están (ver `skusSinGuardar`). Es para después de un
   * guardado que el servidor rechazó sin que la orden cambiara —«no alcanzó el
   * stock»—: la relectura trae fresco el saldo de lo GUARDADO, y a éstos, que ya
   * se habían preguntado una vez, nadie les volvía a preguntar. Van a lo más
   * `SALDOS_POR_TANDA`, como la primera vez; y si la pregunta falla se quedan
   * con lo que tenían: el texto del servidor sigue a la vista.
   */
  const repreguntarSaldos = useCallback(() => {
    const doc = documento.current;
    const sinGuardar = () => skusSinGuardar(baseRef.current.lineas, formaRef.current.lineas);
    for (const sku of sinGuardar().slice(0, SALDOS_POR_TANDA)) {
      buscarSkus(sku)
        .then((r) => {
          if (!vivo.current || documento.current !== doc) return;
          // Si mientras llegaba la respuesta el renglón ya se guardó (o se quitó),
          // su saldo fresco es el que trajo el servidor con el guardado: una
          // respuesta del catálogo de ANTES de eso lo pisaría con un dato viejo.
          if (!sinGuardar().includes(sku)) return;
          const o = (r.opciones ?? []).find((x) => skuDe(x) === sku);
          if (o) ponerSaldos(sku, saldosDe(o.existencias, bodegasRef.current));
        })
        .catch(() => { /* se queda con lo que había */ });
    }
  }, [ponerSaldos]);

  // ── Releer (409 y avisos del chat) ──────────────────────────────────────────
  const releer = useCallback((): Promise<Relectura> => {
    if (releyendo.current) return releyendo.current;
    const o = ordenRef.current;
    if (!o) return Promise.resolve("fallo");
    const p = leerOrden(o.id)
      .then((n): Relectura => {
        const actual = ordenRef.current;
        if (!vivo.current || !actual || actual.id !== n.id) return "fallo";
        // Una lectura más vieja que lo que ya hay en pantalla no se pinta.
        if (n.rev < actual.rev) return "igual";
        const cambio = n.rev !== actual.rev || n.estado !== actual.estado;
        aplicar(n, true);
        if (cambio) alCambiar.current(n);
        return cambio ? "cambio" : "igual";
      })
      .catch((): "fallo" => "fallo")
      .finally(() => { releyendo.current = null; });
    releyendo.current = p;
    return p;
  }, [aplicar]);

  /**
   * Alguien movió la orden: se relee hasta alcanzar la `rev` avisada (ver
   * `releerHasta`). Si los intentos se agotan, el siguiente ciclo del chat
   * vuelve a avisar: ya no se calla tras el primer aviso.
   */
  const avisarCambio = useCallback((objetivo = 0, intentos = 3) => {
    const id = ordenRef.current?.id;
    if (id === undefined) return;
    // La foto de lo que se estaba escribiendo, tomada justo antes de CADA relectura.
    let antes = { base: baseRef.current, forma: formaRef.current };
    void releerHasta({
      objetivo, intentos,
      releer: () => {
        antes = { base: baseRef.current, forma: formaRef.current };
        return releer();
      },
      rev: () => (vivo.current && ordenRef.current?.id === id ? ordenRef.current.rev : null),
      ocupado: () => ocupadoRef.current !== null,
      apuntar: (rev) => { revPendiente.current = Math.max(revPendiente.current, rev); },
      alCambio: () => {
        const o = ordenRef.current;
        setToast(avisoDeCambio(antes.base, antes.forma, baseRef.current,
                               o ? { editar: seEdita(o) } : null, false));
      },
      esperar: (ms) => new Promise((seguir) => { setTimeout(seguir, ms); }),
    });
  }, [releer]);

  /** El chat vio que la orden cambió en el servidor (otra persona, o el barrido). */
  const alCambiarChat = useCallback((c: { rev: number; estado: EstadoOrden }) => {
    const o = ordenRef.current;
    if (!o || c.rev <= o.rev) return;   // ya lo sé: fui yo
    // Con una acción mía en vuelo, lo más probable es que el aviso sea de ella
    // misma: se apunta y se revisa cuando termine.
    if (ocupadoRef.current) { revPendiente.current = Math.max(revPendiente.current, c.rev); return; }
    avisarCambio(c.rev);
  }, [avisarCambio]);

  // ── Escrituras ──────────────────────────────────────────────────────────────
  const terminar = useCallback(() => {
    ocupadoRef.current = null;
    if (!vivo.current) return;
    setOcupado(null);
    const o = ordenRef.current;
    const pendiente = revPendiente.current;
    revPendiente.current = 0;
    if (o && pendiente > o.rev) avisarCambio(pendiente);
  }, [avisarCambio]);

  const ejecutar = useCallback(async (
    accion: Accion, llamar: (o: Orden) => Promise<RespOrden>, respaldo: string,
  ): Promise<boolean> => {
    const o = ordenRef.current;
    if (!o || ocupadoRef.current) return false;
    ocupadoRef.current = accion;
    setOcupado(accion);
    setError(null);
    try {
      const r = await llamar(o);
      if (!vivo.current) return true;
      aplicar(r.orden, false);
      setToast(r.mensaje || respaldo);
      alCambiar.current(r.orden);
      return true;
    } catch (e: unknown) {
      if (!vivo.current) return false;
      if (esConflicto(e)) {
        // Un 409 SIEMPRE relee (trae el saldo de ahora, que es lo que hace falta
        // ver si no alcanzó). Y sólo si la `rev` cambió es «la orden cambió»: se
        // avisa suave. Si no cambió, el 409 es de negocio —no alcanzó el stock,
        // esa venta ya tiene orden, modo prueba— y se enseña el `detail` del
        // servidor tal cual: él dice qué SKU y en qué bodega. (También cae aquí
        // el diálogo que se abrió ANTES de un cambio que el documento ya releyó
        // —ver `revDeDialogo`—: el `detail` es el «la orden cambió» del servidor,
        // y queda arriba, en rojo, con el diálogo ya cerrado.)
        const antes = { base: baseRef.current, forma: formaRef.current };
        const r = await releer();
        if (!vivo.current) return false;
        if (r === "cambio") {
          const actual = ordenRef.current;
          setToast(avisoDeCambio(antes.base, antes.forma, baseRef.current,
                                 actual ? { editar: seEdita(actual) } : null, true));
        } else {
          setError(mensajeDeError(e, r === "fallo"
            ? "El servidor rechazó la acción y la orden no se pudo recargar. Vuelve a intentarlo."
            : "El servidor rechazó la acción sin decir por qué. Revisa la orden y vuelve a intentarlo."));
          // La relectura refrescó el saldo de los renglones GUARDADOS. Si lo que
          // rebotó fue un guardado, faltan los que todavía no lo están: sin esto
          // se quedaban con el «libre» viejo junto al aviso que dice otra cosa.
          if (accion === "guardar") repreguntarSaldos();
        }
      } else {
        setError(mensajeDeError(e, NO_SE_PUDO[accion]));
      }
      return false;
    } finally {
      terminar();
    }
  }, [aplicar, releer, repreguntarSaldos, terminar]);

  const guardar = useCallback(async (): Promise<boolean> => {
    const o = ordenRef.current;
    if (!o) return false;
    const f = formaRef.current;
    // Una confirmada ya aparta: lo que a un borrador se le pide hasta confirmar
    // (bodega en cada renglón, al menos uno por entregar) a ella se le pide
    // aquí, antes de mandar. Si el stock alcanza lo dice el servidor.
    const falla = validar(f)
      ?? (o.estado === "confirmada" ? faltaParaGuardarConfirmada(baseRef.current, f, elegiblesRef.current) : null)
      ?? fechaIncompleta();
    if (falla) { setError(falla); return false; }
    const datos = cambiosDe(baseRef.current, f);
    if (!Object.keys(datos).length) return true;
    const guardado = await ejecutar("guardar", (x) => guardarOrden(x.id, x.rev, datos), "Cambios guardados.");
    // «Se trajo la venta… revisa y guarda» ya se cumplió.
    if (guardado && vivo.current) setTraida(null);
    return guardado;
  }, [ejecutar]);

  const crear = async () => {
    if (ocupadoRef.current) return;
    const falla = validar(formaRef.current) ?? fechaIncompleta();
    if (falla) { setError(falla); return; }
    const datos = datosCompletos(formaRef.current);
    // La clave va atada a lo que se manda con ella. Reintentar LO MISMO (se
    // perdió la respuesta, doble clic) reusa la clave y el servidor contesta la
    // orden que ya creó. Si la persona CORRIGIÓ algo tras el fallo, es otro
    // envío: con la clave vieja el servidor devolvería la orden del primer
    // intento y la corrección se perdería sin que nadie lo dijera.
    claveRef.current = claveDeIntento(claveRef.current, JSON.stringify(datos));
    ocupadoRef.current = "crear";
    setOcupado("crear");
    setError(null);
    try {
      const r = await crearOrden(datos, claveRef.current.clave);
      if (!vivo.current) return;
      aplicar(r.orden, false);
      setTraida(null);
      setToast(r.mensaje || `Borrador ${r.orden.folio} creado.`);
      onCreada(r.orden);
    } catch (e: unknown) {
      if (vivo.current) setError(mensajeDeError(e, NO_SE_PUDO.crear));
    } finally {
      terminar();
    }
  };

  /** Va a donde el usuario pidió (ya sin cambios pendientes). */
  const ir = (d: Destino) => {
    setDialogo(null);
    setMenu(false);
    // Aquí ya se preguntó (o no había nada que perder): que la página no vuelva
    // a preguntar al ver cambiar la dirección.
    if (d.tipo !== "accion" && sucioRef) sucioRef.current = false;
    if (d.tipo === "salir") { onCerrar(); return; }
    // La página abre la orden que diga la dirección (`#OV-00012`, ver docs/ORDENES_VENTA.md §8).
    if (d.tipo === "abrir") { window.location.hash = d.folio; return; }
    // Sin orden no hay acción que preguntar (sus botones ni se pintan).
    const o = ordenRef.current;
    if (!o) return;
    // El diálogo se queda con la `rev` de la orden que la persona tiene DELANTE
    // al abrirlo: con ésa viaja su acción, cambie lo que cambie mientras lo lee
    // (ver `revDeDialogo`).
    if (d.accion !== "confirmar") { setDialogo({ tipo: d.accion, rev: o.rev }); return; }
    // Confirmar aparta TODO O NADA, cada renglón en su bodega. Lo que se
    // confirma es lo GUARDADO (por eso se lee de la orden y no del formulario).
    const falta = faltaParaConfirmar(o.lineas, elegiblesRef.current);
    if (falta) { setError(falta); return; }
    // Confirmar aparta stock de verdad (deja de estar libre para las demás
    // órdenes): se pregunta, como en todas las demás. Ya no es un paso sin
    // vuelta —una confirmada se corrige o se cancela—, y el diálogo lo dice.
    // Lo que falta se dijo arriba, sin abrir nada.
    setDialogo({ tipo: "confirmar", rev: o.rev });
  };

  /**
   * La `rev` con la que viaja la acción del diálogo abierto: la de cuando se
   * ABRIÓ (ver `revDeDialogo`), no la de `o`, que es la orden de ahora.
   */
  const revVista = (accion: AccionOrden, o: Orden): number => revDeDialogo(dialogo, accion, o.rev);

  const confirmar = () => {
    void ejecutar("confirmar", (x) => confirmarOrden(x.id, revVista("confirmar", x)),
                  "Orden confirmada: el stock quedó apartado.")
      .then(() => { if (vivo.current) setDialogo(null); });
  };

  /** Antes de moverse: si hay cambios sin guardar, se pregunta. */
  const pedir = (d: Destino) => {
    setMenu(false);
    if (sucio) setDialogo({ tipo: "sucio", destino: d });
    else ir(d);
  };

  const guardarYSeguir = async (d: Destino) => {
    setDialogo(null);
    if (await guardar()) ir(d);
  };

  /**
   * Vuelve el formulario a lo guardado. Con los cambios se van los avisos que
   * hablaban de ellos (el porqué de un guardado que no pasó, la venta traída).
   */
  const deshacer = () => {
    cambiar(() => baseRef.current);
    setError(null);
    setTraida(null);
  };

  /**
   * La otra salida de «hay cambios sin guardar» ante una ACCIÓN: soltarlos y
   * seguir. Hace falta porque en una confirmada guardar es volver a apartar, y
   * puede no poderse: quien quitó todos los renglones («Si ya no va, cancélala»)
   * o pidió más de lo que hay (409 «no alcanzó») era mandado a cancelar o a
   * entregar, y «Guardar y continuar» lo regresaba al mismo rechazo sin llegar
   * nunca a la acción. Ninguna necesita esos cambios: todas se aplican sobre lo
   * GUARDADO.
   */
  const descartarYSeguir = (d: Destino) => {
    deshacer();
    ir(d);
  };

  /** Toda acción con diálogo cierra el suyo al terminar, salga bien o mal (el porqué queda arriba). */
  const yCerrar = (p: Promise<boolean>) => {
    void p.then(() => { if (vivo.current) setDialogo(null); });
  };

  const conMotivo = (accion: "cancelar" | "borrar", motivo: string) => {
    const llamar = accion === "cancelar" ? cancelarOrden : borrarOrden;
    yCerrar(ejecutar(accion, (o) => llamar(o.id, revVista(accion, o), motivo),
                     accion === "cancelar" ? "Orden cancelada." : "Orden borrada."));
  };

  const entregar = (plan: Extract<PlanEntrega, { ok: true }>) => {
    // El plan salió de los renglones que el diálogo pinta AHORA; la `rev`, de
    // los que tenía al abrirse. Si entre una cosa y otra la orden cambió, el
    // servidor rechaza la entrega entera (409) y no sale nada.
    yCerrar(ejecutar("entregar", (o) => entregarOrden(o.id, revVista("entregar", o), plan.lineas),
                     plan.cierra ? "Orden entregada a la paquetería."
                       : "Entrega parcial registrada: lo que falta sigue apartado."));
  };

  /** Bodega contesta el «¿salió?» del canal. */
  const contestarSalio = (salio: boolean) => {
    const accion: AccionSalio = salio ? "salio_si" : "salio_no";
    // «No salió» con piezas de una entrega anterior NO queda cancelada: lo que
    // ya salió tiene que regresar (el servidor la deja DELIVERED but CANCELLED).
    const yaSalieron = (ordenRef.current?.piezas_entregadas ?? 0) > 0;
    yCerrar(ejecutar(accion, (o) => responderSalio(o.id, revVista(accion, o), salio),
                     salio || yaSalieron ? "Quedó como DELIVERED but CANCELLED: se espera la devolución."
                       : "Orden cancelada: el apartado se soltó."));
  };

  const registrarSalidaTardia = () => {
    yCerrar(ejecutar("salio_tarde", (o) => salioTarde(o.id, revVista("salio_tarde", o)),
                     "Salida registrada: quedó como DELIVERED but CANCELLED."));
  };

  // ── Captura ─────────────────────────────────────────────────────────────────
  const fijar = <K extends keyof Forma>(k: K, v: Forma[K]) => cambiar((f) => ({ ...f, [k]: v }));

  const fijarCanal = (canal: string) => cambiar((f) => {
    const c = CANALES.find((x) => x.id === canal);
    const s: Forma = { ...f, canal };
    // El cliente por omisión es el canal («Temu», «Amazon»), mientras nadie escriba otro.
    if (c?.mp && (!f.cliente.trim() || f.cliente.trim() === rotuloCanal(f.canal))) s.cliente = c.rotulo;
    // La orden de marketplace sigue al canal mientras no haya un id capturado.
    if (!f.mp_orden.trim() && (!f.mp_canal || f.mp_canal === f.canal)) {
      s.mp_canal = c?.mp ? canal : "";
      s.mp_cuenta = c?.mp ? cuentaUnica(canal) : "";
    }
    return s;
  });

  // La cuenta es parte de la llave (canal + cuenta + id). Donde el canal sólo
  // tiene una no hay nada que adivinar y queda puesta; donde hay dos (Mercado
  // Libre) la elige la persona, o la pone «Traer venta».
  const fijarMpCanal = (canal: string) => cambiar((f) => ({ ...f, mp_canal: canal, mp_cuenta: cuentaUnica(canal) }));

  const fijarRenglon = (uid: string, parche: Partial<Renglon>) => cambiar((f) => ({
    ...f, lineas: f.lineas.map((l) => (l.uid === uid ? { ...l, ...parche } : l)),
  }));

  const quitarRenglon = (uid: string) => cambiar((f) => ({ ...f, lineas: f.lineas.filter((l) => l.uid !== uid) }));

  const agregar = useCallback((o: SkuOpcion, fueraDeCatalogo: boolean) => {
    const sku = o.sku.trim();
    if (!sku) return;
    const k = sku.toLowerCase();
    // De dónde conviene sacarlo: la bodega elegible con más libre, o la única que hay.
    const sugerida = bodegaSugerida(o.existencias, elegiblesRef.current);
    const saldos = fueraDeCatalogo ? {} : saldosDe(o.existencias, bodegasRef.current);
    if (!fueraDeCatalogo) {
      cacheSaldos.current.set(k, { ...cacheSaldos.current.get(k), ...saldos });
      preguntados.current.add(k);
    }
    const donde = dondeAgregar(formaRef.current.lineas, sku, sugerida, elegiblesRef.current);
    if (donde.tipo === "ya_salio") {
      // Sólo en una confirmada con entrega parcial: el renglón de ese SKU ya
      // salió (no se le suma) y no queda otra bodega donde abrirle uno nuevo.
      setToast(`${sku} ya salió de ${donde.bodegas.join(", ")} en esta orden y ese renglón no se cambia. Si hace falta más, va en otra orden.`);
      return;
    }
    if (donde.tipo === "sumar") {
      const ya = donde.renglon;
      // Repetir un SKU suma una pieza al renglón que ya está (y refresca su saldo).
      cambiar((f) => ({ ...f, lineas: f.lineas.map((l) => (
        l.uid === ya.uid ? { ...l, cantidad: String((numero(l.cantidad) ?? 0) + 1), saldos: { ...l.saldos, ...saldos } }
          : l)) }));
      setToast(`${ya.sku} ya estaba: se sumó una pieza.`);
      return;
    }
    cambiar((f) => ({ ...f, lineas: [...f.lineas, renglonNuevo({
      sku, titulo: o.nombre, almacen: donde.almacen, saldos, conocido: !fueraDeCatalogo,
    })] }));
  }, [cambiar]);

  // ── Traer la venta del marketplace ──────────────────────────────────────────
  const usarVenta = (v: VentaMarketplace) => {
    cambiar((f) => aplicarVenta(f, v, elegiblesRef.current));
    setTraida({ estado: "aplicada", venta: v });
  };

  const traer = async () => {
    const id = formaRef.current.mp_orden.trim();
    const canal = formaRef.current.mp_canal;
    if (!id) return;
    busqueda.current?.abort();
    const ctrl = new AbortController();
    busqueda.current = ctrl;
    setTraida({ estado: "buscando" });
    try {
      const r = await buscarVenta(id, canal || undefined, ctrl.signal);
      if (ctrl.signal.aborted) return;
      const ventas = r.ventas ?? [];
      if (!r.ok) {
        setTraida({ estado: "falla", mensaje: r.motivo || "El servidor no pudo leer las ventas." });
      } else if (!ventas.length) {
        setTraida({ estado: "falla", mensaje: `No se encontró la venta «${id}»${canal ? ` en ${rotuloCanal(canal)}` : ""}. Revisa el id o cambia el canal.` });
      } else if (ventas.length === 1 && !ventas[0].cancelada && !ventas[0].es_fulfillment
                 && (!ventas[0].ov || ventas[0].ov.id === ordenRef.current?.id)) {
        usarVenta(ventas[0]);
      } else {
        setTraida({ estado: "elegir", ventas });
      }
    } catch (e: unknown) {
      if (ctrl.signal.aborted || (e as { name?: string })?.name === "AbortError") return;
      setTraida({ estado: "falla", mensaje: mensajeDeError(e, "No se pudo buscar la venta (el servidor no contestó).") });
    }
  };

  // ── Lo que se pinta ─────────────────────────────────────────────────────────
  const acciones = orden ? accionesDe(orden) : { barra: [], menu: [] };
  const reserva = orden ? reservaDe(orden) : null;
  const folioEnError = error ? /OV-\d{5,}/.exec(error)?.[0] : undefined;
  const cuentasMp = cuentasDe(forma.mp_canal);
  /**
   * Todavía no aparta nada: no hay columnas de apartado ni de salida, y el
   * «libre» de cada renglón se compara contra TODO lo que pide. (En una
   * confirmada que se está corrigiendo se compara contra lo que le FALTA por
   * apartar: ver `apartadoEn`.)
   */
  const enBorrador = !orden || orden.estado === "borrador";
  /** El canal canceló con el paquete en camino y nadie ha contestado si salió. */
  const esperaSalio = !!orden && orden.estado === "confirmada" && !!orden.canal_cancelo_at && !borrada;
  // La devolución sólo existe donde algo ya salió. En DELIVERED but CANCELLED,
  // `null` es «no quedó registrada», no «pendiente»: se dice tal cual.
  const devolucion = orden && (orden.estado === "entregada" || orden.estado === "entregada_cancelada")
    ? orden.devolucion_estado : null;
  const muestraDevolucion = !!orden && (orden.estado === "entregada_cancelada" || devolucion !== null);
  /**
   * Corrigiendo una confirmada la tabla lleva campos Y las columnas de apartado:
   * no cabe en la tarjeta con el relleno normal (precio e importe quedaban tras
   * el scroll). En ese caso va compacta, y «Salieron» sólo si algo ya salió
   * (si no, sería una columna entera de «—»).
   */
  const compacta = puedeEditar && !enBorrador;
  const verSalieron = !enBorrador && (!compacta || forma.lineas.some(renglonSalio));
  const PX = compacta ? "px-2" : "px-3";
  const TH_ = compacta ? TH.replace("px-3", "px-2") : TH;
  const columnas = 5 + (enBorrador ? 0 : 1) + (verSalieron ? 1 : 0) + (puedeEditar ? 1 : 0);
  const variables = {
    "--ov-chat-top": `${68 + altoBarra + 16}px`,
    "--ov-chat-alto": `calc(100vh - ${68 + altoBarra + 32}px)`,
  } as CSSProperties;

  const opcionesCon = (lista: { id: string; rotulo: string }[], valor: string) =>
    (valor && !lista.some((x) => x.id === valor) ? [...lista, { id: valor, rotulo: valor }] : lista);

  return (
    <div className="space-y-4" style={variables}>
      {/* ── Barra: a dónde regreso, qué orden es y qué puedo hacerle ── */}
      <div ref={barra}
           className="sticky top-[4.25rem] z-30 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-slate-200 bg-white/95 px-4 py-3 shadow-card backdrop-blur">
        <button type="button" onClick={() => pedir({ tipo: "salir" })}
                className="-ml-1 inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm font-semibold text-slate-500 hover:bg-slate-100 hover:text-slate-800">
          <ArrowLeft className="h-4 w-4" /> Órdenes
        </button>
        <span className="hidden h-5 w-px bg-slate-200 sm:block" />
        <h1 className={`text-xl font-extrabold tracking-tight text-slate-900 ${orden || !nueva ? "font-mono" : ""}`}>
          {orden ? orden.folio : nueva ? "Nueva orden" : refOrden}
        </h1>
        {orden ? (
          <>
            <ChipEstado estado={orden.estado} />
            {reserva && <ChipReserva reserva={reserva}
                                     detalle={borrada ? "La orden se borró: lo que tenía apartado se soltó."
                                       : `${num(orden.piezas_apartadas)} de ${num(orden.piezas)} piezas apartadas`
                                         + (orden.piezas_entregadas > 0 ? ` · ${num(orden.piezas_entregadas)} ya salieron` : "")} />}
            {esFull && (
              <span className="inline-flex items-center rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-bold text-indigo-700 ring-1 ring-indigo-200"
                    title="Envío al almacén del marketplace. Se captura en Crear FULL: aquí sólo se consulta.">
                Envío a FULL
              </span>
            )}
            {esperaSalio && (
              <span className="inline-flex items-center rounded bg-amber-50 px-2 py-0.5 text-[11px] font-bold text-amber-800 ring-1 ring-amber-300"
                    title="El canal canceló con el paquete en camino: falta contestar si salió de la bodega.">
                ¿SALIÓ?
              </span>
            )}
            {borrada && (
              <span className="inline-flex items-center rounded bg-rose-50 px-2 py-0.5 text-[11px] font-bold text-rose-700 ring-1 ring-rose-200">
                BORRADA
              </span>
            )}
          </>
        ) : nueva ? (
          <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-[11.5px] font-bold text-slate-500 ring-1 ring-slate-200"
                title="Todavía no existe: nace como BORRADOR al crearla.">
            SIN GUARDAR
          </span>
        ) : null}

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {nueva && (
            <>
              <Boton tono="fantasma" onClick={() => pedir({ tipo: "salir" })} deshabilitado={bloqueado}>Descartar</Boton>
              <Boton tono="primario" icono={Save} onClick={() => void crear()} ocupado={ocupado === "crear"}
                     deshabilitado={!puedeEditar} porque={porqueLectura}>
                Crear borrador
              </Boton>
            </>
          )}
          {orden && sucio && (
            <>
              <Boton tono="fantasma" icono={Undo2} deshabilitado={bloqueado} onClick={deshacer}>
                Deshacer
              </Boton>
              <Boton tono="primario" icono={Save} onClick={() => void guardar()} ocupado={ocupado === "guardar"}
                     deshabilitado={bloqueado && ocupado !== "guardar"}>
                Guardar cambios
              </Boton>
            </>
          )}
          {acciones.barra.map((a) => (
            <Boton key={a.accion} tono={a.tono} icono={a.icono} ocupado={ocupado === a.accion}
                   deshabilitado={!a.puede || (bloqueado && ocupado !== a.accion)}
                   porque={a.puede ? undefined : a.porque}
                   onClick={() => pedir({ tipo: "accion", accion: a.accion })}>
              {a.rotulo}
            </Boton>
          ))}
          {acciones.menu.length > 0 && (
            <div ref={cajaMenu} className="relative">
              <button type="button" onClick={() => setMenu((m) => !m)} aria-haspopup="menu" aria-expanded={menu}
                      title="Más acciones" aria-label="Más acciones"
                      className="rounded-lg border border-slate-200 bg-white p-2 text-slate-500 hover:bg-slate-50 hover:text-slate-800">
                <MoreHorizontal className="h-4 w-4" />
              </button>
              {menu && (
                <div role="menu"
                     className="absolute right-0 top-full z-10 mt-1 w-72 rounded-xl border border-slate-200 bg-white py-1 shadow-lg">
                  {acciones.menu.map((a) => (
                    <button key={a.accion} type="button" role="menuitem" disabled={!a.puede || bloqueado}
                            onClick={() => pedir({ tipo: "accion", accion: a.accion })}
                            className={`flex w-full items-start gap-2.5 px-3 py-2 text-left text-sm disabled:cursor-not-allowed ${
                              a.puede ? (a.tono === "peligro" ? "text-rose-600 hover:bg-rose-50" : "text-slate-700 hover:bg-slate-50")
                                : "text-slate-400"}`}>
                      <a.icono className="mt-0.5 h-4 w-4 shrink-0" />
                      <span className="min-w-0">
                        <span className="block font-semibold">{a.rotulo}</span>
                        {a.puede && a.nota && (
                          <span className="mt-0.5 block text-[11.5px] leading-snug text-slate-400">{a.nota}</span>
                        )}
                        {!a.puede && (a.porque || a.nota) && (
                          <span className="mt-0.5 block text-[11.5px] leading-snug text-slate-400">{a.porque || a.nota}</span>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {carga.estado === "cargando" && <Esqueleto />}

      {(carga.estado === "no_existe" || carga.estado === "error") && (
        <section className="rounded-2xl border border-slate-200 bg-white px-6 py-14 text-center">
          <AlertTriangle className={`mx-auto h-8 w-8 ${carga.estado === "error" ? "text-rose-300" : "text-slate-300"}`} />
          <p className="mt-2 font-semibold text-slate-700">
            {carga.estado === "no_existe" ? <>No existe la orden <span className="font-mono">{refOrden}</span></> : "No se pudo leer la orden"}
          </p>
          <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">
            {carga.estado === "no_existe"
              ? "Revisa el folio. Las órdenes no se eliminan: si alguien la borró, sigue apareciendo en el filtro «Borradas» de la lista."
              : carga.mensaje}
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <Boton icono={ArrowLeft} onClick={onCerrar}>Volver a Órdenes</Boton>
            {carga.estado === "error" && (
              <Boton tono="primario" icono={RefreshCw} onClick={() => setIntento((n) => n + 1)}>Reintentar</Boton>
            )}
          </div>
        </section>
      )}

      {carga.estado === "lista" && (orden || nueva) && (
        <>
          {/* ── Avisos: de lo más grave a lo informativo ── */}
          {error && (
            <Aviso tono="error" icono={AlertTriangle}>
              <div className="flex items-start gap-3">
                <p className="min-w-0 flex-1">{error}</p>
                {folioEnError && folioEnError !== orden?.folio && (
                  <button type="button" onClick={() => pedir({ tipo: "abrir", folio: folioEnError })}
                          className="shrink-0 font-semibold underline underline-offset-2 hover:no-underline">
                    Abrir {folioEnError}
                  </button>
                )}
                <button type="button" onClick={() => setError(null)} title="Cerrar el aviso" aria-label="Cerrar el aviso"
                        className="shrink-0 rounded p-0.5 hover:bg-rose-100">
                  <X className="h-4 w-4" />
                </button>
              </div>
            </Aviso>
          )}
          {orden && borrada && (
            <Aviso tono="error" icono={Trash2}>
              <b>Orden borrada</b> por {quien(orden.borrada_nombre, orden.borrada_por)} el {fechaHora(orden.borrada_at)}.
              {orden.borrada_motivo ? <> Motivo: «{orden.borrada_motivo}».</> : null}{" "}
              No se elimina: queda aquí como registro, y su folio no se recicla.
            </Aviso>
          )}
          {orden && esperaSalio && (
            // El anillo grueso es de lo que PIDE algo. Mientras nadie conteste,
            // la orden no avanza: ni DELIVERED ni libera su apartado.
            <div className="flex items-start gap-3 rounded-xl bg-amber-50 p-4 text-amber-900 ring-2 ring-amber-300">
              <CircleHelp className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
              <div className="min-w-0 flex-1 text-sm">
                <p className="font-extrabold tracking-tight">¿Salió?</p>
                <p className="mt-0.5 leading-relaxed">
                  El canal canceló esta venta cuando el paquete ya iba en camino (estado del canal:{" "}
                  <b className="font-mono text-[12.5px]">{orden.canal_cancelo_ref || "sin dato"}</b>).{" "}
                  <b>¿El paquete salió de la bodega?</b>
                </p>
                <p className="mt-0.5 text-[12.5px] leading-snug opacity-80">
                  Avisó el {fechaHora(orden.canal_cancelo_at)}. Mientras nadie conteste, la orden no se puede
                  marcar DELIVERED y su stock sigue apartado.
                </p>
                {orden.permisos.responder_salio ? (
                  <div className="mt-2.5 flex flex-wrap gap-2">
                    <Boton chico tono="primario" icono={Truck} ocupado={ocupado === "salio_si"}
                           deshabilitado={bloqueado && ocupado !== "salio_si"}
                           onClick={() => pedir({ tipo: "accion", accion: "salio_si" })}>
                      Sí salió
                    </Boton>
                    <Boton chico icono={PackageX} ocupado={ocupado === "salio_no"}
                           deshabilitado={bloqueado && ocupado !== "salio_no"}
                           onClick={() => pedir({ tipo: "accion", accion: "salio_no" })}>
                      No salió
                    </Boton>
                  </div>
                ) : (
                  <p className="mt-1.5 inline-flex items-center gap-1 text-[11.5px] opacity-80">
                    <Lock className="h-3 w-3 shrink-0" />{" "}
                    {orden.permisos.porque.responder_salio || "Tu usuario no puede contestar esta pregunta."}
                  </p>
                )}
              </div>
            </div>
          )}
          {orden && muestraDevolucion && (
            // Sólo se MUESTRA: recibir, dictaminar y cerrar una devolución es
            // otro proceso. Grita mientras el producto no regresa; después no.
            <div className={`flex items-start gap-3 rounded-xl p-4 ${
              devolucion === "cerrada" ? "bg-slate-50 text-slate-700 ring-1 ring-slate-200"
                : devolucion === "recibida" ? "bg-amber-50 text-amber-900 ring-1 ring-amber-200"
                  : "bg-amber-50 text-amber-900 ring-2 ring-amber-300"}`}>
              {devolucion === "cerrada"
                ? <CircleCheck className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
                : <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />}
              <div className="min-w-0 flex-1 text-sm">
                <p className="font-extrabold tracking-tight">
                  {orden.estado === "entregada_cancelada" ? "DELIVERED but CANCELLED" : "Devolución"}
                </p>
                <p className="mt-0.5 leading-relaxed">
                  {devolucion ? AYUDA_DEVOLUCION[devolucion]
                    : "El producto ya salió y la venta se canceló: tiene que regresar al almacén."}{" "}
                  <b>Devolución: {devolucion ?? "sin registrar"}.</b>
                </p>
                <p className="mt-1 text-[12.5px] leading-snug opacity-80">{NOTA_DEVOLUCIONES}</p>
              </div>
            </div>
          )}
          {/* Sólo donde aplica: una orden ya cancelada o entregada no tiene nada que confirmar. */}
          {/* `ok: false` = el backend no pudo leer kubera: sus banderas vienen
              apagadas y sus bodegas vacías porque NO se leyeron. Ni «modo prueba»
              ni «no hay bodega» se afirman sin haberlo medido (la página ya no
              monta el documento así; esto es por si algún día llega). */}
          {modulo.ok !== false && !modulo.habilitado && !borrada && (!orden || orden.estado === "borrador" || orden.estado === "confirmada") && (
            <Aviso tono="ambar" icono={Info}>
              <b>Modo prueba:</b> puedes crear y editar borradores, pero no confirmar, entregar ni corregir una
              confirmada hasta que se encienda la bandera <span className="font-mono text-[12.5px]">ordenes_venta</span>.
            </Aviso>
          )}
          {modulo.ok !== false && puedeEditar && !elegibles.length && (
            <Aviso tono="ambar" icono={AlertTriangle}>
              <b>No hay bodega para órdenes.</b> Ninguna bodega de kubera admite órdenes de venta ahora mismo:{" "}
              {enBorrador
                ? "puedes capturar el borrador, pero no se podrá confirmar mientras no haya una."
                // La confirmada ya aparta: sin bodega que admita órdenes no hay dónde volver a apartar.
                : "puedes corregir los datos de la orden, pero no sus renglones mientras no haya una."}
            </Aviso>
          )}
          {nueva && lectura && (
            <Aviso tono="info" icono={Lock}>
              <b>Sólo lectura.</b> {porqueLectura}
            </Aviso>
          )}

          {/* ── La traza ── */}
          <section className="rounded-2xl border border-slate-200 bg-white p-4">
            <Traza orden={orden} modo="completa" />
          </section>

          <div className="grid gap-4 lg:grid-cols-3 lg:items-start">
            <div className="min-w-0 space-y-4 lg:col-span-2">
              {/* Discreta: explica por qué no hay nada que editar, sin estorbar a quien sólo consulta. */}
              {notaLectura && (
                <p className="flex items-start gap-1.5 px-1 text-xs leading-snug text-slate-400">
                  <Lock className="mt-0.5 h-3 w-3 shrink-0" /> {notaLectura}
                </p>
              )}
              {/* Igual de discreta, para quien SÍ puede corregir una confirmada: aquí
                  guardar no es lo de un borrador (mueve el apartado y deja rastro). */}
              {notaEdicion && (
                <p className="flex items-start gap-1.5 px-1 text-xs leading-snug text-slate-500">
                  <PencilLine className="mt-0.5 h-3 w-3 shrink-0" /> {notaEdicion}
                </p>
              )}

              {/* ── Datos de la orden ── */}
              <Tarjeta titulo="Datos de la orden"
                       nota={orden && lectura && !borrada && !notaLectura ? (
                         <span className="inline-flex items-center gap-1" title={porqueLectura}>
                           <Lock className="h-3 w-3 shrink-0" /> {porqueLectura || "Sólo lectura"}
                         </span>
                       ) : undefined}>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Campo rotulo="Cliente" ayuda={lectura ? undefined : "El canal o la razón social. Nunca el comprador."}>
                    <Entrada valor={forma.cliente} alCambiar={(v) => fijar("cliente", v)} lectura={lectura}
                             apagado={bloqueado} max={TOPE.cliente} placeholder="Temu, Amazon, razón social…" />
                  </Campo>
                  <Campo rotulo="Canal">
                    <Selector valor={forma.canal} alCambiar={fijarCanal} lectura={lectura} apagado={bloqueado}
                              opciones={opcionesCon(CANALES, forma.canal)} />
                  </Campo>
                  {orden && esFull && (
                    <>
                      <Campo rotulo="Tienda de FULL">
                        <Entrada valor={orden.full_tienda ?? ""} alCambiar={() => undefined} lectura apagado />
                      </Campo>
                      <Campo rotulo="Número de envío">
                        <Entrada valor={orden.envio_ref ?? ""} alCambiar={() => undefined} lectura apagado mono />
                      </Campo>
                    </>
                  )}
                </div>

                {/* Un envío a FULL no lleva orden de marketplace (`ov_ordenes_full_mp_chk`): el bloque sobra. */}
                <div className={`mt-4 rounded-xl bg-slate-50/70 p-4 ring-1 ring-slate-100 ${esFull ? "hidden" : ""}`}>
                  <p className="text-[11px] font-extrabold uppercase tracking-[0.06em] text-slate-600">
                    Orden de marketplace
                  </p>
                  <div className="mt-2 flex flex-wrap items-end gap-3">
                    <Campo rotulo="Canal de la venta" ancho="w-full sm:w-44">
                      <Selector valor={forma.mp_canal} alCambiar={fijarMpCanal} lectura={lectura} apagado={bloqueado}
                                opciones={opcionesCon(CANALES.filter((c) => c.mp), forma.mp_canal)} />
                    </Campo>
                    {/* La cuenta es parte de la llave: se ve SIEMPRE que haya canal, no sólo donde hay dos. */}
                    {(forma.mp_canal || forma.mp_cuenta) && (
                      <Campo rotulo="Cuenta" ancho="w-full sm:w-40">
                        <Selector valor={forma.mp_cuenta} alCambiar={(v) => fijar("mp_cuenta", v)} lectura={lectura}
                                  apagado={bloqueado}
                                  opciones={opcionesCon(cuentasMp.map((c) => ({ id: c, rotulo: rotuloCuenta(c) })),
                                                        forma.mp_cuenta)} />
                      </Campo>
                    )}
                    <Campo rotulo="Id de la venta" ancho="min-w-0 flex-1 basis-56">
                      <Entrada valor={forma.mp_orden} alCambiar={(v) => fijar("mp_orden", v)} lectura={lectura}
                               apagado={bloqueado} mono max={TOPE.mp_orden} placeholder="PO-211-…, 2000012345678901…"
                               alEnter={() => void traer()} />
                    </Campo>
                    {!lectura && (
                      <Boton icono={Download} onClick={() => void traer()} ocupado={traida?.estado === "buscando"}
                             deshabilitado={!forma.mp_orden.trim() || bloqueado}
                             porque={forma.mp_orden.trim() ? undefined : "Escribe el id de la venta"}>
                        Traer venta
                      </Boton>
                    )}
                  </div>
                  {!lectura && !traida && (
                    <p className="mt-2 text-[11.5px] leading-snug text-slate-400">
                      «Traer venta» prellena renglones, precio, guía, paquetería y fecha con lo que el panel ya
                      registró de esa venta; la bodega de cada renglón la eliges tú. Canal, cuenta e id van
                      juntos: con ellos se cacha la cancelación del marketplace.
                    </p>
                  )}
                  {traida && traida.estado !== "buscando" && (
                    <div className="mt-3">
                      <VentaTraida traida={traida} ordenId={orden?.id ?? null} elegibles={elegibles}
                                   renglonesFijos={forma.lineas.some(renglonSalio)}
                                   onUsar={usarVenta} onAbrir={(folio) => pedir({ tipo: "abrir", folio })}
                                   onCerrar={() => setTraida(null)} />
                    </div>
                  )}
                </div>

                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <Campo rotulo="Fecha de la venta" ayuda={lectura ? undefined : "Hora de CDMX."}>
                    <Fecha valor={forma.fecha_venta} alCambiar={(v) => fijar("fecha_venta", v)} lectura={lectura}
                           apagado={bloqueado} rotulo="Fecha de la venta" />
                  </Campo>
                  <Campo rotulo="Entregar a la paquetería" ayuda={lectura ? undefined : "Límite para entregarla, hora de CDMX."}>
                    <Fecha valor={forma.entrega_limite} alCambiar={(v) => fijar("entrega_limite", v)} lectura={lectura}
                           apagado={bloqueado} rotulo="Entregar a la paquetería" />
                  </Campo>
                  <Campo rotulo="Guía"
                         ayuda={lectura || !enBorrador ? undefined
                           : "Si todavía no la tienes, se puede anotar después de confirmar."}>
                    <Entrada valor={forma.guia} alCambiar={(v) => fijar("guia", v)} lectura={lectura}
                             apagado={bloqueado} mono max={TOPE.guia} placeholder="Número de guía" />
                  </Campo>
                  <Campo rotulo="Paquetería">
                    <Entrada valor={forma.paqueteria} alCambiar={(v) => fijar("paqueteria", v)} lectura={lectura}
                             apagado={bloqueado} max={TOPE.paqueteria} placeholder="J&T, Estafeta, DHL…" />
                  </Campo>
                </div>

                <div className="mt-4">
                  <Campo rotulo="Descripción">
                    {lectura ? (
                      <p className="whitespace-pre-wrap rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
                        {forma.descripcion || "—"}
                      </p>
                    ) : (
                      <textarea value={forma.descripcion} onChange={(ev) => fijar("descripcion", ev.target.value)}
                                rows={2} maxLength={MAX_DESCRIPCION} disabled={bloqueado} className={CLASE_CAMPO}
                                placeholder="Venta de Temu, orden PO-…, tienda Kubera…" />
                    )}
                  </Campo>
                  {!lectura && forma.descripcion.length > MAX_DESCRIPCION - 80 && (
                    <p className="mt-1 text-right text-[11px] tabular-nums text-slate-400">
                      {forma.descripcion.length} / {MAX_DESCRIPCION}
                    </p>
                  )}
                </div>
              </Tarjeta>

              {/* ── Renglones y totales ── */}
              <Tarjeta titulo="Renglones" sinRelleno
                       nota={forma.lineas.length
                         ? `${num(forma.lineas.length)} ${forma.lineas.length === 1 ? "renglón" : "renglones"} · ${num(piezas)} ${piezas === 1 ? "pieza" : "piezas"}`
                         : undefined}>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[760px] text-sm">
                    <thead className="border-b border-slate-100 bg-slate-50/60">
                      <tr>
                        <th className={TH_}>Producto</th>
                        <th className={TH_}
                            title="De qué bodega de kubera sale el renglón, y cuánto hay libre de ese SKU ahí (físico menos apartado)">
                          Bodega
                        </th>
                        <th className={`${TH_} text-right`}>Cantidad</th>
                        {!enBorrador && (
                          <th className={`${TH_} text-right`}
                              title="Piezas apartadas en su bodega: la cantidad completa o cero (se aparta todo o nada)">
                            Apartado
                          </th>
                        )}
                        {verSalieron && (
                          <th className={`${TH_} text-right`}
                              title="Piezas que salieron al entregar el renglón. «—» = todavía no sale">
                            Salieron
                          </th>
                        )}
                        <th className={`${TH_} text-right`}>Precio unit.</th>
                        <th className={`${TH_} text-right`}>Importe</th>
                        {puedeEditar && <th className="w-10" />}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {!forma.lineas.length && (
                        <tr>
                          <td colSpan={columnas} className="px-5 py-10 text-center text-sm text-slate-400">
                            {puedeEditar
                              ? "Sin renglones todavía. Agrega un producto con el buscador de abajo o trae la venta del marketplace."
                              : "Esta orden no tiene renglones."}
                          </td>
                        </tr>
                      )}
                      {forma.lineas.map((l) => {
                        const cant = numero(l.cantidad) ?? 0;
                        const precio = numero(l.precio);
                        // El renglón que ya salió está congelado: se ve, pero no se toca ni se quita.
                        const fijo = renglonSalio(l);
                        const editable = puedeEditar && !fijo;
                        // Corrigiendo una confirmada, lo que cuenta es lo que el renglón ya
                        // tiene apartado DONDE ESTÁ AHORA (nada, si es nuevo o cambió de
                        // bodega): lo demás es lo que le toca apartar al guardar. Fuera de
                        // eso se enseña lo que dice el servidor.
                        const corrige = editable && !enBorrador;
                        const apartado = corrige ? apartadoEn(l, base.lineas) : l.reservado;
                        return (
                          <tr key={l.uid} className="align-middle">
                            <td className="px-5 py-2.5">
                              <div className="flex items-center gap-3">
                                <Miniatura src={l.imagen} />
                                <div className="min-w-0">
                                  <div className="truncate font-mono text-[13px] font-bold text-slate-800">{l.sku}</div>
                                  <div className={`${compacta ? "max-w-[160px]" : "max-w-[280px]"} truncate text-xs text-slate-500`}
                                       title={l.titulo ?? undefined}>
                                    {l.titulo || <span className="text-slate-400">sin título</span>}
                                  </div>
                                  {l.conocido === false && (
                                    <div className="mt-0.5 text-[11px] font-semibold text-amber-700"
                                         title={`El catálogo (core.products) no conoce este SKU: no tiene saldo en ninguna bodega, y sin saldo la orden no se puede ${corrige ? "guardar" : "confirmar"}.`}>
                                      SKU fuera del catálogo
                                    </div>
                                  )}
                                  {/* Sólo donde los demás renglones sí se editan: dice por qué éste no. */}
                                  {puedeEditar && fijo && (
                                    <div className="mt-0.5 inline-flex items-center gap-1 text-[11px] font-semibold text-slate-500"
                                         title="Este renglón ya salió de la bodega: no se cambia ni se quita. Si hace falta más de este producto, va en otra bodega o en otra orden.">
                                      <Lock className="h-3 w-3 shrink-0" /> ya salió
                                    </div>
                                  )}
                                </div>
                              </div>
                            </td>
                            <td className={`${PX} py-2.5`}>
                              <CeldaBodega renglon={l} porApartar={cant - apartado} editable={editable} apagado={bloqueado}
                                           avisa={enBorrador || editable} alGuardar={corrige} angosta={compacta}
                                           elegibles={elegibles} bodegas={bodegas}
                                           ocupadas={forma.lineas.filter((x) => x.uid !== l.uid && skuDe(x) === skuDe(l))
                                             .map((x) => x.almacen)}
                                           alCambiar={(v) => fijarRenglon(l.uid, { almacen: v })} />
                            </td>
                            <td className={`${PX} py-2.5 text-right`}>
                              {editable ? (
                                <input type="number" min="1" step="1" inputMode="numeric" value={l.cantidad}
                                       disabled={bloqueado} aria-label={`Cantidad de ${l.sku}`}
                                       onChange={(ev) => fijarRenglon(l.uid, { cantidad: ev.target.value })}
                                       onWheel={(ev) => ev.currentTarget.blur()}
                                       className={`${CLASE_CAMPO} ${compacta ? "!w-16" : "!w-20"} text-right tabular-nums`} />
                              ) : <span className="font-semibold tabular-nums text-slate-800">{num(cant)}</span>}
                            </td>
                            {!enBorrador && (
                              <td className={`${PX} py-2.5 text-right`}>
                                <Apartado renglon={l} apartado={apartado} porGuardar={corrige} />
                              </td>
                            )}
                            {verSalieron && (
                              <td className={`${PX} py-2.5 text-right`}><Salieron renglon={l} cantidad={cant} /></td>
                            )}
                            <td className={`${PX} py-2.5 text-right`}>
                              {editable ? (
                                <input type="number" min="0" step="0.01" inputMode="decimal" value={l.precio}
                                       disabled={bloqueado} placeholder="0.00" aria-label={`Precio unitario de ${l.sku}`}
                                       onChange={(ev) => fijarRenglon(l.uid, { precio: ev.target.value })}
                                       onWheel={(ev) => ev.currentTarget.blur()}
                                       className={`${CLASE_CAMPO} ${compacta ? "!w-24" : "!w-28"} text-right tabular-nums`} />
                              ) : <span className="tabular-nums text-slate-700">{dinero(precio, moneda)}</span>}
                            </td>
                            <td className="px-5 py-2.5 text-right font-semibold tabular-nums text-slate-900">
                              {precio === null ? <span className="font-normal text-slate-400">—</span>
                                : dinero(centavos(cant * precio), moneda)}
                            </td>
                            {puedeEditar && (
                              <td className="py-2.5 pr-3 text-right">
                                {editable && (
                                  <button type="button" onClick={() => quitarRenglon(l.uid)} disabled={bloqueado}
                                          title="Quitar el renglón" aria-label={`Quitar ${l.sku}`}
                                          className="rounded-lg p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50">
                                    <X className="h-4 w-4" />
                                  </button>
                                )}
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {puedeEditar && (
                  <div className="border-t border-slate-100 px-5 py-3">
                    <BuscadorSku onElegir={agregar} deshabilitado={bloqueado} bodegas={elegibles} />
                  </div>
                )}

                {/* Totales */}
                <div className="border-t border-slate-100 px-5 py-4">
                  <dl className="ml-auto w-full max-w-sm space-y-2.5 text-sm">
                    <FilaTotal rotulo="Suma de renglones">
                      <span className="tabular-nums text-slate-700">{dinero(suma, moneda)}</span>
                    </FilaTotal>
                    <FilaTotal rotulo="Total"
                               nota={forma.precio_origen === "marketplace" ? "precio de la venta en el marketplace"
                                 : totalCapturado === null ? "igual a la suma de renglones"
                                   : Math.abs(totalCapturado - suma) >= 0.005 ? "capturado a mano · difiere de la suma" : "capturado a mano"}>
                      {lectura ? (
                        <span className="font-semibold tabular-nums text-slate-900">{dinero(total, moneda)}</span>
                      ) : (
                        <span className="flex items-center justify-end gap-2">
                          {totalCapturado !== null && (
                            <button type="button" disabled={bloqueado}
                                    onClick={() => cambiar((f) => ({ ...f, total: "", precio_origen: "manual" }))}
                                    className="text-[11.5px] font-semibold text-indigo-600 hover:underline disabled:opacity-50">
                              usar la suma
                            </button>
                          )}
                          <input type="number" min="0" step="0.01" inputMode="decimal" value={forma.total}
                                 disabled={bloqueado} placeholder={suma.toFixed(2)} aria-label="Total de la orden"
                                 onChange={(ev) => cambiar((f) => ({ ...f, total: ev.target.value, precio_origen: "manual" }))}
                                 onWheel={(ev) => ev.currentTarget.blur()}
                                 className={`${CLASE_CAMPO} !w-32 text-right font-semibold tabular-nums`} />
                        </span>
                      )}
                    </FilaTotal>
                    <FilaTotal rotulo="Comisión" nota="lo que cobra el canal por la venta">
                      {lectura ? (
                        <span className="tabular-nums text-slate-700">{dinero(comision, moneda)}</span>
                      ) : (
                        <input type="number" min="0" step="0.01" inputMode="decimal" value={forma.comision}
                               disabled={bloqueado} placeholder="0.00" aria-label="Comisión"
                               onChange={(ev) => fijar("comision", ev.target.value)}
                               onWheel={(ev) => ev.currentTarget.blur()}
                               className={`${CLASE_CAMPO} !w-32 text-right tabular-nums`} />
                      )}
                    </FilaTotal>
                    <div className="flex items-baseline justify-between gap-4 border-t border-slate-200 pt-2.5">
                      <dt className="text-[11px] font-extrabold uppercase tracking-[0.06em] text-slate-600">Neto</dt>
                      <dd className={`text-xl font-extrabold tracking-tight tabular-nums ${total - comision < 0 ? "text-rose-600" : "text-slate-900"}`}>
                        {dinero(centavos(total - comision), moneda)}
                      </dd>
                    </div>
                  </dl>
                </div>
              </Tarjeta>

              {/* ── PDF ── */}
              <Tarjeta titulo="PDF"
                       nota={orden && orden.archivos.length
                         ? `${num(orden.archivos.length)} ${orden.archivos.length === 1 ? "archivo" : "archivos"}`
                         : undefined}>
                <ArchivosOrden orden={orden} modulo={modulo}
                               onCambio={(o) => { aplicar(o, true); alCambiar.current(o); }} />
              </Tarjeta>

              {orden && <Pie orden={orden} />}
            </div>

            {/* ── El chat de movimientos: pegajoso, con su propio scroll ── */}
            <aside className="min-w-0 lg:sticky lg:top-[var(--ov-chat-top)]">
              {/* ChatOrden trae su propia tarjeta (h-full): aquí sólo se le da el alto.
                  Sin orden no hay nada que desplazar, así que no se estira. */}
              <div className={orden ? "h-[520px] lg:h-[var(--ov-chat-alto)] lg:min-h-[420px]" : ""}>
                <ChatOrden ordenId={orden?.id ?? null} rev={orden?.rev ?? 0}
                           puedeEscribir={!!orden?.permisos.mensajes} porque={orden?.permisos.porque.mensajes}
                           yo={modulo.yo.actor} onCambio={alCambiarChat} />
              </div>
            </aside>
          </div>
        </>
      )}

      {/* ── Preguntas ── */}
      {dialogo?.tipo === "sucio" && (
        <Confirmacion
          titulo="Hay cambios sin guardar"
          texto={dialogo.destino.tipo === "accion"
            ? "Antes de mover la orden hay que guardar lo que cambiaste, o descartarlo: la acción se aplica sobre lo que está guardado."
            : orden ? "Si sales ahora, lo que cambiaste en esta orden se pierde."
              : "Si sales ahora, la orden nueva no se crea y lo capturado se pierde."}
          volver="Seguir editando"
          accion={dialogo.destino.tipo === "accion" ? "Guardar y continuar" : "Salir sin guardar"}
          tono={dialogo.destino.tipo === "accion" ? "primario" : "peligro"}
          // El botón de en medio es la OTRA salida. Para irse: guardar antes.
          // Ante una acción: soltar los cambios y seguir (ver `descartarYSeguir`);
          // va en rosa, como «Salir sin guardar», porque también pierde lo escrito,
          // y el foco sigue naciendo en «Guardar y continuar» (el último).
          extra={!orden ? undefined
            : dialogo.destino.tipo === "accion"
              ? <Boton tono="peligro" onClick={() => descartarYSeguir(dialogo.destino)}>Descartar cambios y continuar</Boton>
              : <Boton onClick={() => void guardarYSeguir(dialogo.destino)}>Guardar y salir</Boton>}
          onConfirmar={() => (dialogo.destino.tipo === "accion" ? void guardarYSeguir(dialogo.destino) : ir(dialogo.destino))}
          onCerrar={() => setDialogo(null)}
        />
      )}
      {dialogo?.tipo === "confirmar" && orden && (
        <Confirmacion
          titulo={`Confirmar ${orden.folio}`}
          texto={<ResumenConfirmar orden={orden} />}
          // El foco nace en la acción: es el paso normal del flujo, un Enter más.
          accion="Confirmar y apartar" tono="primario" ocupado={ocupado === "confirmar"}
          onConfirmar={confirmar}
          onCerrar={() => { if (!bloqueado) setDialogo(null); }}
        />
      )}
      {dialogo?.tipo === "entregar" && orden && (
        <DialogoEntrega orden={orden} ocupado={ocupado === "entregar"} onConfirmar={entregar}
                        onCerrar={() => { if (!bloqueado) setDialogo(null); }} />
      )}
      {dialogo?.tipo === "cancelar" && orden && (
        <DialogoMotivo
          titulo={`Cancelar ${orden.folio}`}
          texto={orden.estado !== "confirmada"
            ? <>Se cancela el borrador. No tenía stock apartado. No se puede deshacer: habría que capturar otra orden.</>
            : orden.piezas_entregadas > 0
              ? <>De esta orden <b>ya {orden.piezas_entregadas === 1 ? "salió 1 pieza" : `salieron ${num(orden.piezas_entregadas)} piezas`}</b>.
                  Si la cancelas queda como <b>DELIVERED but CANCELLED</b>: lo que seguía apartado
                  {orden.piezas_apartadas > 0 ? <> ({num(orden.piezas_apartadas)} {orden.piezas_apartadas === 1 ? "pieza" : "piezas"})</> : null}{" "}
                  se suelta y lo que salió tiene que regresar (se abre su devolución como pendiente). No se puede deshacer.</>
              : <>Se cancela la orden y <b>se suelta su apartado</b>
                  {orden.piezas_apartadas > 0 ? <> ({num(orden.piezas_apartadas)} {orden.piezas_apartadas === 1 ? "pieza vuelve" : "piezas vuelven"} a quedar libres en su bodega)</> : null}.
                  No se puede deshacer: habría que capturar otra orden.</>}
          accion={orden.estado === "confirmada" && orden.piezas_entregadas > 0 ? "Cancelar y pedir devolución" : "Cancelar la orden"}
          minimo={minimoDeMotivo("cancelar", orden)}
          ocupado={ocupado === "cancelar"}
          onConfirmar={(m) => conMotivo("cancelar", m)}
          onCerrar={() => { if (!bloqueado) setDialogo(null); }}
        />
      )}
      {dialogo?.tipo === "borrar" && orden && (
        <DialogoMotivo
          titulo={`Borrar ${orden.folio}`}
          texto={<>La orden sale de la lista, pero <b>no se elimina</b>: queda quién la borró y por qué, y su folio
            no se recicla.{orden.piezas_apartadas > 0 ? <> Se suelta su apartado ({num(orden.piezas_apartadas)} {orden.piezas_apartadas === 1 ? "pieza" : "piezas"}).</> : null}{" "}
            {yaSalioAlgo(orden)
              // Lo que ya salió no vuelve por borrar la orden: la salida se queda
              // en el libro. Recapturarla sacaría dos veces el mismo paquete.
              ? <><b>Lo que ya salió de la bodega NO regresa al saldo</b>
                  {orden.piezas_entregadas > 0 ? <> ({num(orden.piezas_entregadas)} {orden.piezas_entregadas === 1 ? "pieza" : "piezas"})</> : null}:
                  borrarla no deshace la salida, y <b>no hay que capturarla de nuevo</b> (saldría dos veces el mismo paquete).
                  {orden.devolucion_estado === "pendiente"
                    ? <> Su devolución está <b>pendiente</b>: al borrarla deja de contarse en «Por devolver», aunque el producto todavía tenga que regresar.</>
                    : null}</>
              // Borrar ya no es LA salida para una confirmada con un error (ésa se
              // corrige): es para la orden que no debió existir.
              : <>Es para la orden que no debió existir (duplicada, o capturada por error).
                  {seEdita(orden) ? <> Si sólo hay que corregirle algo, no hace falta borrarla: se puede editar.</> : null}</>}</>}
          accion="Borrar la orden" minimo={minimoDeMotivo("borrar", orden)} ocupado={ocupado === "borrar"}
          onConfirmar={(m) => conMotivo("borrar", m)}
          onCerrar={() => { if (!bloqueado) setDialogo(null); }}
        />
      )}
      {dialogo?.tipo === "salio_si" && orden && (
        <Confirmacion
          titulo={`El paquete de ${orden.folio} SÍ salió`}
          texto={<>La orden pasa a <b>DELIVERED but CANCELLED</b>: sus piezas apartadas
            {orden.piezas_apartadas > 0 ? <> ({num(orden.piezas_apartadas)})</> : null} se dan por salidas —bajan del
            físico de la bodega— y se queda esperando la devolución. <b>No se puede deshacer.</b></>}
          accion="Sí salió" tono="primario" seguro ocupado={ocupado === "salio_si"}
          onConfirmar={() => contestarSalio(true)}
          onCerrar={() => { if (!bloqueado) setDialogo(null); }}
        />
      )}
      {dialogo?.tipo === "salio_no" && orden && (
        <Confirmacion
          titulo={`El paquete de ${orden.folio} NO salió`}
          texto={orden.piezas_entregadas > 0
            // Con una entrega anterior no queda «cancelada»: lo que ya salió
            // tiene que regresar (igual que al cancelarla a mano).
            ? <>De esta orden <b>ya {orden.piezas_entregadas === 1 ? "salió 1 pieza" : `salieron ${num(orden.piezas_entregadas)} piezas`}</b> en
                una entrega anterior: queda como <b>DELIVERED but CANCELLED</b> (la canceló el marketplace). Lo que seguía apartado
                {orden.piezas_apartadas > 0 ? <> ({num(orden.piezas_apartadas)} {orden.piezas_apartadas === 1 ? "pieza" : "piezas"})</> : null}{" "}
                se suelta y lo que salió tiene que regresar (se abre su devolución como pendiente).
                Confírmalo sólo si lo que faltaba sigue físicamente en la bodega. <b>No se puede deshacer.</b></>
            : <>La orden <b>se cancela</b> (la canceló el marketplace) y su apartado se suelta
                {orden.piezas_apartadas > 0 ? <>: {num(orden.piezas_apartadas)} {orden.piezas_apartadas === 1 ? "pieza vuelve" : "piezas vuelven"} a quedar libres en su bodega</> : null}.
                Confírmalo sólo si el paquete sigue físicamente en la bodega. <b>No se puede deshacer.</b></>}
          accion="No salió" tono="peligro" ocupado={ocupado === "salio_no"}
          onConfirmar={() => contestarSalio(false)}
          onCerrar={() => { if (!bloqueado) setDialogo(null); }}
        />
      )}
      {dialogo?.tipo === "salio_tarde" && orden && (
        <Confirmacion
          titulo={`${orden.folio}: el paquete sí había salido`}
          texto={<>Esta orden se canceló, pero su paquete ya había salido de la bodega. Al registrarlo pasa a{" "}
            <b>DELIVERED but CANCELLED</b>: se anota la salida de {piezasPorSalir(orden.lineas) === 1 ? "su pieza" : `sus ${num(piezasPorSalir(orden.lineas))} piezas`}{" "}
            —<b>baja el físico de la bodega</b>— y se queda esperando la devolución. <b>No se puede deshacer.</b></>}
          accion="Sí, registrar la salida" tono="primario" seguro ocupado={ocupado === "salio_tarde"}
          onConfirmar={registrarSalidaTardia}
          onCerrar={() => { if (!bloqueado) setDialogo(null); }}
        />
      )}

      {toast && (
        <div role="status"
             className="fixed bottom-5 left-1/2 z-50 max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}

// ── Piezas ────────────────────────────────────────────────────────────────────

const TH = "px-3 py-2.5 text-left text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400 first:pl-5 last:pr-5";

function Tarjeta({ titulo, nota, sinRelleno, children }: {
  titulo: string; nota?: ReactNode; sinRelleno?: boolean; children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white">
      <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
        <h2 className="text-sm font-extrabold tracking-tight text-slate-900">{titulo}</h2>
        {nota ? <span className="min-w-0 text-right text-xs text-slate-400">{nota}</span> : null}
      </header>
      <div className={sinRelleno ? "" : "p-5"}>{children}</div>
    </section>
  );
}

/** Texto de una línea. En lectura un campo vacío dice «—», no se queda en blanco. */
function Entrada({ valor, alCambiar, lectura, apagado, placeholder, mono, max, alEnter }: {
  valor: string; alCambiar: (v: string) => void; lectura: boolean; apagado: boolean;
  placeholder?: string; mono?: boolean; max?: number; alEnter?: () => void;
}) {
  return (
    <input type="text" value={lectura ? (valor || "—") : valor} disabled={lectura || apagado}
           onChange={(ev) => alCambiar(ev.target.value)} maxLength={max} spellCheck={false}
           onKeyDown={alEnter ? (ev) => { if (ev.key === "Enter") { ev.preventDefault(); alEnter(); } } : undefined}
           placeholder={lectura ? undefined : placeholder}
           className={`${CLASE_CAMPO} ${mono ? "font-mono" : ""}`} />
  );
}

function Selector({ valor, alCambiar, lectura, apagado, opciones }: {
  valor: string; alCambiar: (v: string) => void; lectura: boolean; apagado: boolean;
  opciones: { id: string; rotulo: string }[];
}) {
  return (
    <select value={valor} onChange={(ev) => alCambiar(ev.target.value)} disabled={lectura || apagado}
            className={CLASE_CAMPO}>
      <option value="">—</option>
      {opciones.map((o) => <option key={o.id} value={o.id}>{o.rotulo}</option>)}
    </select>
  );
}

/**
 * Fecha y hora en CDMX. En lectura se escribe con letra: un datetime-local
 * apagado y vacío parece un error.
 *
 * `incompleta`: el control tiene el día pero no la hora (o al revés). En ese
 * estado el navegador reporta `value = ""` y no dispara `input`, así que el
 * formulario lo cree vacío. Se le pregunta al control al salir de él y se dice
 * junto al campo; `fechaIncompleta()` lo vuelve a revisar al guardar, por si
 * nunca se salió del campo. `rotulo` es como se nombra el campo en ese aviso.
 */
function Fecha({ valor, alCambiar, lectura, apagado, rotulo }: {
  valor: string; alCambiar: (v: string) => void; lectura: boolean; apagado: boolean; rotulo: string;
}) {
  const [incompleta, setIncompleta] = useState(false);
  if (lectura) return <input type="text" value={fechaLegible(valor)} disabled className={CLASE_CAMPO} />;
  return (
    <>
      <input type="datetime-local" value={valor} disabled={apagado} data-ov-fecha={rotulo}
             aria-invalid={incompleta || undefined}
             onChange={(ev) => { setIncompleta(false); alCambiar(ev.target.value); }}
             onBlur={(ev) => setIncompleta(ev.currentTarget.validity.badInput)}
             className={`${CLASE_CAMPO} ${incompleta ? "!border-amber-400 !ring-2 !ring-amber-100" : ""}`} />
      {incompleta && (
        <span role="alert" className="mt-1 block text-[11.5px] font-semibold leading-snug text-amber-700">
          Falta la hora: así como está, la fecha no se guarda. Complétala o borra el campo.
        </span>
      )}
    </>
  );
}

function Miniatura({ src }: { src: string | null }) {
  const [rota, setRota] = useState(false);
  useEffect(() => setRota(false), [src]);
  // Sólo imágenes públicas del catálogo: una del backend no cargaría (un <img> no manda el token).
  const sirve = !!src && /^https?:\/\//i.test(src) && !(API_BASE && src.startsWith(API_BASE)) && !rota;
  if (!sirve) {
    return (
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50">
        <Package className="h-4 w-4 text-slate-300" />
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt="" loading="lazy" onError={() => setRota(true)}
         className="h-10 w-10 shrink-0 rounded-lg border border-slate-200 object-cover" />
  );
}

/**
 * De qué bodega sale el renglón y cuánto hay libre de su SKU AHÍ.
 *
 * El saldo tiene tres caras y cada una dice lo suyo: «libre N» (se sabe), «sin
 * dato» porque el SKU no tiene fila de saldo en esa bodega (no es un cero), y
 * «sin dato» porque todavía no se pregunta. `avisa` = el renglón todavía tiene
 * algo que apartar (un borrador, o una confirmada que se está corrigiendo): ahí
 * «libre» se compara contra lo que le falta (`porApartar`) y, si no alcanza, va
 * en ámbar —se aparta todo o nada, mejor verlo antes—. En la que sólo se
 * consulta, «libre» es lo que queda para LOS DEMÁS y no se compara.
 */
function CeldaBodega({
  renglon: l, porApartar, editable, apagado, avisa, alGuardar, angosta, elegibles, bodegas, ocupadas, alCambiar,
}: {
  renglon: Renglon;
  /**
   * Piezas que al renglón le falta apartar EN ESA BODEGA: todas las que pide en
   * un borrador; en una confirmada, las de más (lo que ya aparta ahí no cuenta:
   * «libre» ya lo trae descontado). Cero o menos = no necesita nada.
   */
  porApartar: number;
  editable: boolean;
  apagado: boolean;
  avisa: boolean;
  /** Se está corrigiendo una confirmada: el apartado se mueve al GUARDAR, no al confirmar. */
  alGuardar: boolean;
  /** La tabla va compacta (corrigiendo una confirmada): el selector ocupa menos. */
  angosta?: boolean;
  elegibles: Bodega[];
  bodegas: Bodega[];
  /** Bodegas que ya usa OTRO renglón del mismo SKU: SKU + bodega no se repite. */
  ocupadas: string[];
  alCambiar: (codigo: string) => void;
}) {
  const saldo = saldoDe(l);
  const fuera = !!l.almacen && elegibles.length > 0 && !elegibles.some((b) => b.codigo === l.almacen);
  const corto = avisa && !!saldo && saldo.libre < porApartar;
  // Por qué va en ámbar (sólo se lee si `corto`).
  const noAlcanza = alGuardar
    ? `no alcanza para ${porApartar === 1 ? "la pieza que le falta" : `las ${num(porApartar)} piezas que le faltan`} por apartar: así no se podrá guardar`
    : `no alcanza para ${porApartar === 1 ? "la pieza" : `las ${num(porApartar)} piezas`} del renglón: así no se podrá confirmar`;
  const nota = !l.almacen
    ? (editable
      ? <span className="font-semibold text-amber-700"
              title={`Sin bodega no se puede ${alGuardar ? "guardar" : "confirmar"}: el stock se aparta en la bodega de cada renglón.`}>
          elige la bodega
        </span>
      : <span className="text-slate-400">sin bodega</span>)
    : fuera && editable
      ? <span className="font-semibold text-amber-700"
              title={`${rotuloBodega(l.almacen, bodegas)} no admite órdenes de venta: con ella no se ${
                alGuardar ? "podrán guardar cambios en los renglones" : "podrá confirmar"}.`}>
          no admite órdenes
        </span>
      : saldo
        ? <span className={`tabular-nums ${corto ? "font-semibold text-amber-700" : "text-slate-500"}`}
                title={`${l.almacen}: ${num(saldo.fisico)} físicas − ${num(saldo.apartado)} apartadas = ${num(saldo.libre)} libres`
                  + (corto ? ` · ${noAlcanza} (se aparta todo o nada)` : "")}>
            libre {num(saldo.libre)}
          </span>
        : <span className="text-slate-400"
                title={saldo === null
                  ? `Este SKU no tiene saldo registrado en ${l.almacen}. No es un cero: no se sabe. Sin saldo no se puede apartar.`
                  : `Todavía no se sabe cuánto hay de este SKU en ${l.almacen}: se lee al guardar.`}>
            sin dato
          </span>;

  if (!editable) {
    return (
      <div title={l.almacen ? rotuloBodega(l.almacen, bodegas) : undefined}>
        <div className="font-mono text-[12.5px] font-semibold text-slate-700">{l.almacen || "—"}</div>
        <div className="mt-0.5 text-[11.5px] leading-snug">{nota}</div>
      </div>
    );
  }
  return (
    <div>
      <select value={l.almacen} disabled={apagado} aria-label={`Bodega de ${l.sku}`}
              title={l.almacen ? rotuloBodega(l.almacen, bodegas) : "De qué bodega sale este renglón"}
              onChange={(ev) => alCambiar(ev.target.value)}
              className={`${CLASE_CAMPO} ${angosta ? "!w-32" : "!w-40"} !px-2 !py-1.5 font-mono text-[12.5px] ${
                !l.almacen || fuera ? "!border-amber-300" : ""}`}>
        <option value="">—</option>
        {fuera && <option value={l.almacen}>{l.almacen} (no admite órdenes)</option>}
        {/* Sin catálogo de dónde elegir, la bodega GUARDADA se sigue viendo: sin
            esta opción el select enseñaría «—», como si el renglón no tuviera. */}
        {!!l.almacen && !elegibles.length && <option value={l.almacen}>{l.almacen}</option>}
        {elegibles.map((b) => {
          const elegida = b.codigo === l.almacen;
          const ocupada = !elegida && ocupadas.includes(b.codigo);
          const s = l.saldos[b.codigo];
          // El saldo de la elegida ya se lee debajo: en la lista sólo se dice el de las OTRAS.
          const extra = elegida ? "" : ocupada ? " · ya en otro renglón"
            : s ? ` · libre ${num(s.libre)}` : s === null ? " · sin dato" : "";
          return <option key={b.codigo} value={b.codigo} disabled={ocupada}>{b.codigo}{extra}</option>;
        })}
      </select>
      <div className="mt-1 text-[11.5px] leading-snug">{nota}</div>
    </div>
  );
}

/**
 * Piezas apartadas del renglón: la cantidad completa o cero (se aparta todo o
 * nada). `apartado` es lo que tiene HOY en la bodega que enseña; `porGuardar` =
 * se está corrigiendo una confirmada, y entonces ese número es el de ANTES de
 * guardar (un renglón nuevo, o movido de bodega, todavía no aparta nada ahí).
 */
function Apartado({ renglon: l, apartado, porGuardar }: { renglon: Renglon; apartado: number; porGuardar: boolean }) {
  if (apartado > 0) {
    return (
      <span className="font-semibold tabular-nums text-emerald-700"
            title={`Apartadas en ${l.almacen || "su bodega"}: nadie más las puede tomar.`}>
        {num(apartado)}
      </span>
    );
  }
  return (
    <span className="tabular-nums text-slate-400"
          title={l.entregado !== null ? "Ya salió: el apartado se convirtió en salida y lo que no salió se soltó."
            : !porGuardar ? "Sin apartado: se soltó, o la orden nunca lo apartó."
              : l.almacen ? "Todavía no aparta nada en esta bodega: se aparta al guardar (todo o nada)."
                : "Todavía no aparta nada: elige su bodega y se aparta al guardar (todo o nada)."}>
      0
    </span>
  );
}

/** Piezas que salieron al entregar el renglón. «—» = todavía no sale. Un renglón que ya salió no se vuelve a tocar. */
function Salieron({ renglon: l, cantidad }: { renglon: Renglon; cantidad: number }) {
  if (l.entregado === null) {
    return <span className="text-slate-300" title="Todavía no sale de la bodega.">—</span>;
  }
  const cuando = [fechaHora(l.entregado_at, ""), l.entregado_por ? `marcó ${quien(null, l.entregado_por)}` : ""]
    .filter(Boolean).join(" · ");
  const que = l.entregado === 0 ? "No salió ninguna pieza: el renglón se soltó"
    : l.entregado < cantidad ? `Salieron ${num(l.entregado)} de ${num(cantidad)}: lo demás se soltó`
      : "Salió completo";
  return (
    <span title={cuando ? `${que} · ${cuando}` : que}
          className={`font-semibold tabular-nums ${
            l.entregado >= cantidad ? "text-emerald-700" : l.entregado > 0 ? "text-amber-700" : "text-slate-400"}`}>
      {num(l.entregado)}
      {l.entregado < cantidad && <span className="font-normal text-slate-400"> de {num(cantidad)}</span>}
    </span>
  );
}

function FilaTotal({ rotulo, nota, children }: { rotulo: string; nota?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="min-w-0">
        <span className={CLASE_ROTULO}>{rotulo}</span>
        {nota ? <span className="block text-[11.5px] leading-snug text-slate-400">{nota}</span> : null}
      </dt>
      <dd className="shrink-0 text-right">{children}</dd>
    </div>
  );
}

/**
 * «Marcar DELIVERED»: qué salió de cada renglón pendiente.
 *
 * Por omisión sale todo (es lo normal: un clic y Enter). Se puede bajar el
 * número de un renglón —lo que no sale se suelta— o desmarcarlo —sigue
 * apartado para una entrega posterior—. Abajo se dice, antes de confirmar, en
 * qué queda la orden: nadie debería enterarse por el chip de que su entrega
 * fue «parcial».
 */
function DialogoEntrega({ orden, ocupado, onConfirmar, onCerrar }: {
  orden: Orden;
  ocupado: boolean;
  onConfirmar: (plan: Extract<PlanEntrega, { ok: true }>) => void;
  onCerrar: () => void;
}) {
  const pendientes = useMemo(() => renglonesPendientes(orden.lineas), [orden.lineas]);
  // Sólo lo que la persona tocó; lo demás vale lo de omisión (marcado y completo).
  const [captura, setCaptura] = useState<Record<number, { marcado?: boolean; n?: string }>>({});
  const filas: FilaEntrega[] = pendientes.map((l) => ({
    id: l.id, sku: l.sku, cantidad: l.cantidad,
    marcado: captura[l.id]?.marcado ?? true, n: captura[l.id]?.n ?? String(l.cantidad),
  }));
  const plan = planDeEntrega(filas, orden.piezas_entregadas);
  const fijar = (id: number, parche: { marcado?: boolean; n?: string }) =>
    setCaptura((c) => ({ ...c, [id]: { ...c[id], ...parche } }));
  const titulo = `Marcar ${orden.folio} como DELIVERED`;
  const pz = (n: number) => `${num(n)} ${n === 1 ? "pieza" : "piezas"}`;

  return (
    <Ventana onCerrar={onCerrar} etiqueta={titulo} ancho="max-w-2xl">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <h2 className="text-base font-extrabold tracking-tight text-slate-900">{titulo}</h2>
        <BotonCerrar onClick={onCerrar} />
      </div>
      <div className="space-y-3 px-5 py-4">
        <p className="text-sm leading-relaxed text-slate-600">
          ¿Qué se entregó a la paquetería? Por omisión salen todas las piezas de cada renglón. Baja el número
          si salieron menos, o desmarca el renglón que todavía no sale. Queda registrado que tú lo marcaste.
        </p>
        <Aviso tono="ambar" icono={Info}>
          <b>Lo que no salga se suelta y vuelve a quedar libre en la bodega.</b> Un renglón que ya salió no se
          vuelve a tocar.
        </Aviso>
        {orden.piezas_entregadas > 0 && (
          <p className="text-xs text-slate-500">
            De esta orden ya {orden.piezas_entregadas === 1 ? "salió 1 pieza" : `salieron ${num(orden.piezas_entregadas)} piezas`} en
            una entrega anterior: aquí sólo están los renglones que faltan.
          </p>
        )}
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200">
          {filas.map((f, i) => {
            const l = pendientes[i];
            const n = numero(f.n);
            const menos = f.marcado && n !== null && Number.isInteger(n) && n >= 0 && n < f.cantidad;
            return (
              <li key={f.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
                <input type="checkbox" checked={f.marcado} disabled={ocupado}
                       onChange={(ev) => fijar(f.id, { marcado: ev.target.checked })}
                       aria-label={`${f.sku} sale ahora`}
                       className="h-4 w-4 shrink-0 rounded border-slate-300 text-indigo-600 focus:ring-indigo-200" />
                <div className="min-w-0 flex-1 basis-44">
                  <div className="truncate font-mono text-[13px] font-bold text-slate-800">{f.sku}</div>
                  <div className="truncate text-xs text-slate-500" title={l.titulo ?? undefined}>
                    {l.titulo || <span className="text-slate-400">sin título</span>}
                  </div>
                  <div className="mt-0.5 text-[11.5px] text-slate-400">
                    {l.almacen ? <span className="font-mono">{l.almacen}</span> : "sin bodega"} · {pz(f.cantidad)}{" "}
                    {f.cantidad === 1 ? "apartada" : "apartadas"}
                  </div>
                </div>
                <div className="ml-auto flex shrink-0 flex-col items-end gap-0.5">
                  {f.marcado ? (
                    <label className="flex items-center gap-2 text-xs text-slate-500">
                      Salieron
                      <input type="number" min="0" max={f.cantidad} step="1" inputMode="numeric" value={f.n}
                             disabled={ocupado} aria-label={`Piezas de ${f.sku} que salieron`}
                             onChange={(ev) => fijar(f.id, { n: ev.target.value })}
                             onWheel={(ev) => ev.currentTarget.blur()}
                             className={`${CLASE_CAMPO} !w-20 !py-1.5 text-right font-semibold tabular-nums`} />
                      <span className="tabular-nums">de {num(f.cantidad)}</span>
                    </label>
                  ) : (
                    <span className="text-xs font-semibold text-slate-500">Todavía no sale: sigue apartado</span>
                  )}
                  {menos && (
                    <span className="text-[11.5px] font-semibold text-amber-700">
                      {f.cantidad - (n ?? 0) === 1 ? "se suelta 1 pieza" : `se sueltan ${num(f.cantidad - (n ?? 0))} piezas`}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
          {!filas.length && (
            <li className="px-3 py-6 text-center text-sm text-slate-400">No queda ningún renglón por salir.</li>
          )}
        </ul>
        <p role="status" className={`text-sm leading-snug ${plan.ok ? "text-slate-700" : "font-semibold text-rose-600"}`}>
          {!plan.ok ? plan.error
            : plan.cierra
              ? <>{plan.salen === 1 ? "Sale 1 pieza" : `Salen ${num(plan.salen)} piezas`}
                  {plan.sueltan > 0 ? <> y {plan.sueltan === 1 ? "se suelta 1" : `se sueltan ${num(plan.sueltan)}`}</> : null}:
                  la orden queda <b>DELIVERED</b>.</>
              : <>{plan.salen === 1 ? "Sale 1 pieza" : `Salen ${num(plan.salen)} piezas`}
                  {plan.sueltan > 0 ? <> y {plan.sueltan === 1 ? "se suelta 1" : `se sueltan ${num(plan.sueltan)}`}</> : null}.{" "}
                  {plan.quedan === 1 ? "1 renglón sigue apartado" : `${num(plan.quedan)} renglones siguen apartados`}:
                  la orden queda <b>CONFIRMADA</b>, con <b>entrega parcial</b>.</>}
        </p>
      </div>
      <PieConfirmacion seguro={false}>
        <Boton onClick={onCerrar} tono="fantasma">No, regresar</Boton>
        <Boton tono="exito" icono={Truck} ocupado={ocupado} deshabilitado={!plan.ok}
               porque={plan.ok ? undefined : plan.error}
               onClick={() => { if (plan.ok) onConfirmar(plan); }}>
          {plan.ok && !plan.cierra ? "Registrar entrega parcial" : "Sí, marcar DELIVERED"}
        </Boton>
      </PieConfirmacion>
    </Ventana>
  );
}

/**
 * Lo que se le dice a quien va a confirmar: qué se aparta y dónde, con qué
 * guía, y que después todavía se puede corregir (cada cambio vuelve a apartar
 * y queda registrado). Se lee de la orden GUARDADA, que es lo que el servidor
 * va a confirmar.
 */
function ResumenConfirmar({ orden }: { orden: Orden }) {
  const renglones = orden.lineas.length;
  const piezas = orden.lineas.reduce((s, l) => s + l.cantidad, 0);
  const bodegas = Array.from(new Set(orden.lineas.map((l) => l.almacen).filter((b): b is string => !!b)));
  return (
    <>
      Se {piezas === 1 ? "aparta" : "apartan"} <b>{num(piezas)} {piezas === 1 ? "pieza" : "piezas"}</b> en{" "}
      {num(renglones)} {renglones === 1 ? "renglón" : "renglones"}
      {bodegas.length ? <> ({bodegas.join(" · ")})</> : null}, todo o nada.{" "}
      {orden.guia ? <>Guía: <b className="font-mono">{orden.guia}</b>.</> : <b>No tiene guía capturada.</b>}{" "}
      Después de confirmar <b>la orden todavía se puede corregir</b> (también la guía): cada cambio vuelve a
      apartar el stock, todo o nada, y queda registrado en la bitácora con quién lo hizo.
    </>
  );
}

/** Lo que contestó «Traer venta»: el aviso de lo aplicado, o la lista para elegir. */
function VentaTraida({ traida, ordenId, elegibles, renglonesFijos, onUsar, onAbrir, onCerrar }: {
  traida: Exclude<Traida, { estado: "buscando" }>;
  ordenId: number | null;
  /** Las bodegas donde se puede hacer la orden: de ahí sale qué decirle de la bodega de los renglones. */
  elegibles: Bodega[];
  /** De la orden ya salieron renglones: la venta NO cambió los renglones (ver `aplicarVenta`). */
  renglonesFijos: boolean;
  onUsar: (v: VentaMarketplace) => void;
  onAbrir: (folio: string) => void;
  onCerrar: () => void;
}) {
  if (traida.estado === "falla") {
    return <Aviso tono="ambar" icono={AlertTriangle}>{traida.mensaje}</Aviso>;
  }
  if (traida.estado === "aplicada") {
    const v = traida.venta;
    const sinSku = v.renglones_sin_sku ?? 0;
    const conSku = v.lineas.reduce((a, l) => a + l.cantidad, 0);
    return (
      <div className="space-y-2">
        <Aviso tono="ok" icono={PackageCheck}>
          Se trajo la venta <span className="font-mono text-[12.5px] font-semibold">{v.orden}</span> de{" "}
          {origenDeVenta(v)}: {num(v.piezas)} {v.piezas === 1 ? "pieza" : "piezas"}
          {v.total !== null ? <> · total {dinero(v.total)}</> : <> · sin precio registrado</>}
          {v.comision !== null ? <> · comisión {dinero(v.comision)}</> : null}.{" "}
          {renglonesFijos
            ? "Los renglones no se cambiaron: de esta orden ya salieron piezas, y lo que falta por entregar se corrige a mano."
            : !v.lineas.length ? "La venta no trae renglones con SKU: los de la orden no se tocaron."
              : sinSku > 0 ? "Los renglones traídos son SÓLO los que tienen SKU."
                : "Los renglones son los de la venta."}{" "}
          {/* La venta no dice de qué bodega sale: eso se decide aquí. */}
          {renglonesFijos || !v.lineas.length ? null
            : elegibles.length === 1 ? <>Salen de <span className="font-mono text-[12.5px]">{elegibles[0].codigo}</span>, la única bodega con órdenes. </>
              : elegibles.length > 1 ? <><b>Falta elegir la bodega de cada renglón.</b> </> : null}
          Revisa y guarda.
        </Aviso>
        {/* `piezas` cuenta TODA la venta, pero un renglón sin SKU no entra a la
            orden: sin este aviso, almacén surtiría menos de lo que se vendió.
            (Si los renglones no se cambiaron, no «entró» ninguno: ya se dijo arriba.) */}
        {sinSku > 0 && !renglonesFijos && (
          <Aviso tono="ambar" icono={AlertTriangle}>
            La venta trae <b>{num(sinSku)} {sinSku === 1 ? "renglón" : "renglones"} sin SKU</b> que no se{" "}
            {sinSku === 1 ? "pudo" : "pudieron"} traer: {sinSku === 1 ? "agrégalo" : "agrégalos"} a mano.{" "}
            La venta es de {num(v.piezas)} {v.piezas === 1 ? "pieza" : "piezas"} y{" "}
            {conSku === 0 ? "no entró ninguna" : conSku === 1 ? "sólo entró 1" : `sólo entraron ${num(conSku)}`} a
            los renglones. Revísala contra el marketplace antes de confirmar.
          </Aviso>
        )}
        {v.cancelada && (
          <Aviso tono="ambar" icono={AlertTriangle}>
            Esta venta está <b>CANCELADA</b> en el marketplace: con el módulo encendido, el barrido cancelará la orden.
          </Aviso>
        )}
        {v.es_fulfillment && (
          <Aviso tono="ambar" icono={AlertTriangle}>
            Esta venta es <b>FULL</b>: sale del almacén del marketplace y no lleva orden propia.
          </Aviso>
        )}
        {v.ov && v.ov.id !== ordenId && (
          <Aviso tono="ambar" icono={AlertTriangle}>
            Esta venta ya tiene la orden <b>{v.ov.folio}</b> ({ROTULO_ESTADO[v.ov.estado]}): no se podrá guardar otra.{" "}
            <button type="button" onClick={() => onAbrir(v.ov!.folio)}
                    className="font-semibold underline underline-offset-2 hover:no-underline">
              Abrir {v.ov.folio}
            </button>
          </Aviso>
        )}
      </div>
    );
  }
  const unica = traida.ventas.length === 1 ? traida.ventas[0] : null;
  return (
    <div className="space-y-2">
      <Aviso tono="ambar" icono={Info}>
        <div className="flex items-start gap-3">
          <p className="min-w-0 flex-1">
            {!unica ? <>Hay {traida.ventas.length} ventas con ese id. Elige cuál traer.</>
              : unica.ov && unica.ov.id !== ordenId
                ? <>Esa venta ya tiene la orden <b>{unica.ov.folio}</b> ({ROTULO_ESTADO[unica.ov.estado]}). Una venta lleva una sola orden viva.</>
                : unica.es_fulfillment
                  ? <>Esa venta es <b>FULL</b>: sale del almacén del marketplace y <b>no lleva orden propia</b>.</>
                  : <>Esa venta está <b>CANCELADA</b> en el marketplace. Puedes traerla para documentarla, pero el barrido cancelará la orden.</>}
          </p>
          <button type="button" onClick={onCerrar} title="Cerrar" aria-label="Cerrar"
                  className="shrink-0 rounded p-0.5 hover:bg-amber-100">
            <X className="h-4 w-4" />
          </button>
        </div>
      </Aviso>
      <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
        {traida.ventas.map((v) => {
          const otra = v.ov && v.ov.id !== ordenId ? v.ov.folio : null;
          return (
            <FilaVenta key={`${v.canal}|${v.cuenta}|${v.orden}`} venta={v} onAbrirOrden={otra ? onAbrir : undefined}>
              {otra ? (
                <Boton chico onClick={() => onAbrir(otra)}>Abrir {otra}</Boton>
              ) : v.es_fulfillment ? (
                <span className="text-right text-[11.5px] leading-snug text-slate-400">no lleva<br />orden propia</span>
              ) : (
                <Boton chico tono={v.cancelada ? "secundario" : "primario"} onClick={() => onUsar(v)}>
                  {v.cancelada ? "Traer de todos modos" : "Usar esta venta"}
                </Boton>
              )}
            </FilaVenta>
          );
        })}
      </div>
    </div>
  );
}

/** Quién y cuándo: discreto, al pie. La historia completa está en el chat. */
function Pie({ orden }: { orden: Orden }) {
  const linea = "flex flex-wrap gap-x-1.5";
  return (
    <ul className="space-y-1 px-1 text-xs leading-relaxed text-slate-400">
      <li className={linea}>
        <span>Creada por <b className="font-semibold text-slate-500">{quien(orden.creado_nombre, orden.creado_por)}</b> {ROTULO_VIA[orden.creado_via]}</span>
        <span>· {fechaHora(orden.creado_at)}</span>
      </li>
      {orden.confirmada_at && (
        <li className={linea}>
          <span>Confirmada por <b className="font-semibold text-slate-500">{quien(orden.confirmada_nombre, orden.confirmada_por)}</b></span>
          <span>· {fechaHora(orden.confirmada_at)}</span>
        </li>
      )}
      {orden.canal_cancelo_at && (
        <li className={linea}>
          <span>El canal avisó la cancelación con el paquete en camino</span>
          {orden.canal_cancelo_ref ? <span>(estado del canal: <span className="font-mono">{orden.canal_cancelo_ref}</span>)</span> : null}
          <span>· {fechaHora(orden.canal_cancelo_at)}</span>
        </li>
      )}
      {orden.entregada_at && (
        <li className={linea}>
          <span>Entregada a la paquetería por <b className="font-semibold text-slate-500">{quien(orden.entregada_nombre, orden.entregada_por)}</b></span>
          <span>· {fechaHora(orden.entregada_at)}</span>
        </li>
      )}
      {orden.cancelada_at && (
        <li className={linea}>
          <span>
            Cancelada por <b className="font-semibold text-slate-500">{quien(orden.cancelada_nombre, orden.cancelada_por)}</b>
            {orden.cancelada_origen === "marketplace" ? " (la canceló el marketplace)"
              : orden.cancelada_origen === "sistema" ? " (la canceló el sistema)"
                : orden.cancelada_origen === "manual" ? " (a mano)" : ""}
          </span>
          <span>· {fechaHora(orden.cancelada_at)}</span>
          {orden.cancelada_motivo ? <span>· motivo: «{orden.cancelada_motivo}»</span> : null}
        </li>
      )}
      {orden.borrada_at && (
        <li className={`${linea} text-rose-500`}>
          <span>Borrada por <b className="font-semibold">{quien(orden.borrada_nombre, orden.borrada_por)}</b></span>
          <span>· {fechaHora(orden.borrada_at)}</span>
          {orden.borrada_motivo ? <span>· motivo: «{orden.borrada_motivo}»</span> : null}
        </li>
      )}
      <li>Última actualización: {fechaHora(orden.actualizado_at)} · hora de CDMX</li>
    </ul>
  );
}

function Esqueleto() {
  const barra = "animate-pulse rounded bg-slate-100";
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Leyendo la orden">
      <div className="rounded-2xl border border-slate-200 bg-white p-4"><div className={`${barra} h-12`} /></div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="rounded-2xl border border-slate-200 bg-white p-5">
            <div className={`${barra} h-4 w-40`} />
            <div className="mt-4 grid gap-4 sm:grid-cols-3">
              {[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className={`${barra} h-9`} />)}
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-5">
            <div className={`${barra} h-4 w-28`} />
            <div className="mt-4 space-y-3">
              {[0, 1, 2].map((i) => <div key={i} className={`${barra} h-10`} />)}
            </div>
          </div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className={`${barra} h-4 w-32`} />
          <div className="mt-4 space-y-3">
            {[0, 1, 2, 3].map((i) => <div key={i} className={`${barra} h-12`} />)}
          </div>
        </div>
      </div>
    </div>
  );
}
