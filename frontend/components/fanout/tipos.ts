/**
 * Tipos y estilos compartidos de la página del fan-out en vivo
 * (/dashboard, /dashboard/matriz y el panel del rastro).
 * La forma de los datos la define `backend/services/fanout_vivo.py`.
 */

export type Tono = "mal" | "ok" | "full" | "omit";
export type Kind = "ok" | "mal" | "full" | "omit" | "nopub" | "igual" | "sim";

export interface Celda {
  k: Kind;
  texto: string;
  detalle?: string;
}

export interface Columna {
  id: string;
  canal: string;
  cuenta: string;
  nombre: string;
}

export interface Evento {
  id: number;
  sku: string;
  fin: string;
  hora: string;
  origen: "odoo" | "woo" | "venta" | "otro";
  cambio: string;
  woo_antes: number | null;
  woo_despues: number | null;
  woo_hora: string | null;
  ms: number;
  total_s: number | null;
  espera_s: number | null;
  celdas: Record<string, Celda>;
  tono: Tono;
  toca: boolean;
}

export interface Coincidencia {
  canal: string;
  nombre: string;
  vivas: number;
  iguales: number;
  de_mas: number;
  de_menos: number;
  causas?: { c: string; t: string; n: number }[]; // por qué no coinciden las que están a la venta
}

/** Por qué una publicación no coincide con Woo (lo arma `fanout_vivo._causas`). */
export interface Causa {
  c: "403" | "error" | "perdido" | "camino" | "canal" | "tarde" | "omitida" | "fuera";
  t: string; // etiqueta corta
  d: string; // el detalle, en una oración
}

export interface CanalEstado {
  canal: string;
  nombre: string;
  estado: "al_dia" | "rechaza" | "con_errores" | "sin_cambios";
  escritos_24h: number;
  ok_24h: number;
  rech_24h: number;
  racha: number;
  racha_dias: number | null;
  racha_desde: string | null;
  ultimo_ok: string;
  ultimo_ok_reciente: string;
  error_codigo: string | null;
  error_permiso: boolean;
  ultimo_rechazo: { sku: string; fin: string } | null;
  coincidencia?: Coincidencia;
}

export interface Atender {
  nivel: "urgente" | "hoy" | "semana" | "despues";
  titulo: string;
  texto: string;
  rastro?: { sku: string; fin: string } | null;
  matriz?: boolean;
  full?: boolean;
}

export interface Vivo {
  ahora: string;
  reparto: string[];
  fuera_reparto: string[];
  veredicto: { llegan: number; total: number; frase: string; detalle: string; grave: boolean };
  canales: CanalEstado[];
  foto: {
    skus_odoo: number;
    skus_woo: number;
    ambos: number;
    distintos: number;
    distintos_por_ventas?: number; // de los distintos, los que son exactamente ventas sin orden
    sin_odoo_con_piezas: number;
    piezas_sin_odoo: number;
    ultima: string | null;
    peor: { sku: string; stock_odoo: number; stock_woo: number } | null;
  };
  stock_watch: {
    habilitado: boolean;
    modo: string | null;
    en_memoria: boolean;
    segundos: number | null;
    cambios: number;
    ultima: string;
    pendientes_ventas: number | null;
    mas_vieja_h: number | null;
  };
  fanout: { habilitado: boolean; dry_run: boolean; cola: number; debounce_s: number; reserva: number };
  atender: Atender[];
  bien: string[];
  serie: { dias: string[]; canales: { canal: string; nombre: string; ok: number[]; mal: number[] }[] };
  /** Escrituras por hora de cada canal, últimas 24 h (la más vieja primero). */
  pulso: { canal: string; nombre: string; ok: number[]; mal: number[] }[];
  full_sin_regla: { tipo: string; n: number }[];
  columnas: Columna[];
  eventos: Evento[];
  sin_reparto_1h: number;
  ultimo_id: number;
}

export interface Destino extends Celda {
  canal: string;
  nombre: string;
  fuera: boolean;
}

export interface Rastro extends Evento {
  ok: boolean;
  motivo: string | null;
  destinos: Destino[];
  fila: { sku: string; hora: string; dt: number; fin: string; tono: Tono; este: boolean }[];
}

export interface CeldaMatriz {
  k: "igual" | "mas" | "menos" | "rech" | "full" | "nopub";
  v: string;
  d: string;
  s: string;
  p: boolean; // no está a la venta (borrador, pausada, incompleta…)
  causa?: Causa | null;
}

/** Piezas ya vendidas cuya orden todavía no nace en Odoo: stock_watch se las resta a Woo. */
export interface SinOrden {
  piezas: number;
  ventas: number;
  canales: string;
  edad: string;
  explica: boolean; // ¿son exactamente la diferencia Odoo − Woo?
}

export interface FilaMatriz {
  sku: string;
  que: string;
  odoo: string;
  woo: string;
  dif: boolean; // Odoo≠Woo que las ventas sin orden NO explican
  woo_de?: string; // de dónde sale el Woo: «foto hoy 21:53» (stock_watch) o «leído hoy 21:57» (fan-out)
  sin_orden?: SinOrden | null;
  celdas: Record<string, CeldaMatriz>;
  tags: string[];
  peso: number;
}

export interface Matriz {
  ahora: string;
  columnas: Columna[];
  barras: Coincidencia[];
  filas: FilaMatriz[];
}

/** Color de cada resultado en el horario y el rastro (contraste ≥ 4.5:1). */
export const CELDA_CLS: Record<Kind, string> = {
  ok: "bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-200",
  mal: "bg-rose-600 text-white",
  full: "bg-sky-50 text-sky-800 ring-1 ring-inset ring-sky-200",
  omit: "bg-slate-100 text-slate-700",
  igual: "bg-white text-slate-700 ring-1 ring-inset ring-slate-200",
  sim: "bg-violet-50 text-violet-800 ring-1 ring-inset ring-violet-200",
  nopub: "text-slate-500",
};

export const TONO_COLOR: Record<Tono, string> = {
  mal: "#e11d48",
  ok: "#059669",
  full: "#0284c7",
  omit: "#94a3b8",
};

export const ESTADO_CANAL: Record<CanalEstado["estado"], { texto: string; chip: string; linea: string }> = {
  al_dia: { texto: "Al día", chip: "bg-emerald-50 text-emerald-800", linea: "#10b981" },
  sin_cambios: { texto: "Sin cambios hoy", chip: "bg-slate-100 text-slate-700", linea: "#94a3b8" },
  con_errores: { texto: "Con rechazos", chip: "bg-amber-50 text-amber-800", linea: "#f59e0b" },
  rechaza: { texto: "Rechaza", chip: "bg-rose-600 text-white", linea: "#e11d48" },
};

/** Etiqueta de causa (contraste ≥ 4.5:1 sobre su fondo). */
export const CAUSA_CLS: Record<Causa["c"], string> = {
  "403": "bg-rose-600 text-white",
  error: "bg-rose-100 text-rose-900",
  perdido: "bg-violet-600 text-white",
  camino: "bg-indigo-100 text-indigo-900",
  canal: "bg-sky-100 text-sky-900",
  tarde: "bg-amber-100 text-amber-900",
  omitida: "bg-slate-200 text-slate-800",
  fuera: "bg-slate-100 text-slate-700",
};

export const CAUSA_NOMBRE: Record<Causa["c"], string> = {
  "403": "Rechazado (403)",
  error: "Error al escribir",
  perdido: "Cambio perdido",
  camino: "En camino",
  canal: "Cambió el canal",
  tarde: "Sin alinear",
  omitida: "Omitida a propósito",
  fuera: "No recibe stock",
};

/** Celda de la matriz contra Woo: sólida si está a la venta, punteada si no. */
export const CELDA_MATRIZ_SOLIDO: Record<CeldaMatriz["k"], string> = {
  igual: "border border-emerald-300 bg-emerald-50 text-emerald-800",
  mas: "border border-rose-400 bg-rose-50 text-rose-800",
  menos: "border border-amber-400 bg-amber-50 text-amber-800",
  rech: "border border-rose-600 bg-rose-600 text-white",
  full: "border border-sky-300 bg-sky-50 text-sky-800",
  nopub: "border border-transparent text-slate-500",
};
export const CELDA_MATRIZ_PUNTEADO: Partial<Record<CeldaMatriz["k"], string>> = {
  igual: "border border-dashed border-emerald-600 bg-white text-emerald-800",
  mas: "border border-dashed border-rose-600 bg-white text-rose-800",
  menos: "border border-dashed border-amber-600 bg-white text-amber-800",
};

/** Un renglón de la línea de trazabilidad (lo arma `fanout_vivo.historia`). */
export type ItemTraza =
  | { tipo: "woo"; ts: string; hora: string; origen: "odoo" | "woo"; de: number | null; a: number | null;
      fallo: boolean; motivo: string }
  | { tipo: "reparto"; ts: string; fin: string; hora: string; motivo: string;
      origen: "venta" | "recuperado" | "excedente" | "reenvio" | "cambio"; tono: Tono; destinos: Destino[]; sin_destinos: boolean }
  | { tipo: "canal"; ts: string; hora: string; canal: string; nombre: string; campo: string; via: string;
      relacion: "coincide" | "su_cuenta" | "sin_escritura" | "estado";
      de?: number | null; a?: number | null; ref?: { valor: number | null; hora: string } | null;
      de_txt?: string; a_txt?: string }
  | { tipo: "full"; ts: string; hora: string; cuenta: string; nombre: string; ml_tipo: string; texto: string;
      grupo: GrupoFull | "foto"; sig: string; x?: number; de?: number | null; a?: number | null };

/** El carril FULL de un SKU en su trazabilidad. */
export interface FullSku {
  cuentas: { cuenta: string; nombre: string; stock: number; situacion: string | null; cambio: string | null;
             vendidas_14d: number; cobertura: number | null;
             /** La publicación FULL de esta fila la declara OTRO SKU: el número de aquí es viejo. */
             de_otro?: { sku: string; stock: number; listing: string } | null }[];
  grupos: Partial<Record<GrupoFull, { avisos: number; piezas: number }>>;
}

export interface Historia {
  ok: boolean;
  sku: string;
  dias: number;
  hoy: string;
  ahora: string;
  existe: boolean;
  odoo: string;
  woo: string;
  woo_de?: string;
  sin_orden?: SinOrden | null;
  columnas: Columna[];
  celdas: Record<string, CeldaMatriz>;
  resumen: { cambios_woo: number; repartos: number; con_rechazo: number; su_cuenta: number; full?: number };
  full?: FullSku | null;
  items: ItemTraza[];
  total: number;
  truncado: boolean;
}

// ── La pestaña FULL (`backend/services/fanout_full.py`) ─────────────────────

export type GrupoFull = "llego" | "vendido" | "cancelado" | "traslado" | "cuarentena" | "ajuste" | "retiro" | "otro";
export type EstadoCuadre = "cuadra" | "revisar" | "no_cuadra";
export type CuentaSel = "ambas" | "BEKURA" | "SANCORFASHION";

export interface AvisoFull {
  hora: string;
  dia: string;
  cuenta: string;
  nombre: string;
  sku: string | null;
  tipo: string;
  texto: string;
  grupo: GrupoFull;
  x: number;
  sig: string;
}

export interface CuentaFull {
  cuenta: string;
  nombre: string;
  piezas: number;
  publicaciones: number;
  skus: number;
  piezas_no_activas: number;
  hoy: Record<GrupoFull, { avisos: number; piezas: number }>;
  avisos_hoy: number;
}

export interface DiaLibro {
  dia: string;
  cuenta: string;
  nombre: string;
  parcial: boolean;
  grupos: Record<GrupoFull, number>;
  avisos: number;
  vendible: number;
  todo: number;
  foto: number;
  cambios_foto: number;
  dif_vendible: number;
  dif_todo: number;
  estado_vendible: EstadoCuadre;
  estado_todo: EstadoCuadre;
}

export interface ResumenFull {
  ok: boolean;
  hoy: string;
  hora: string;
  cuentas: CuentaFull[];
  avisos_hoy: number;
  por_hora: { h: string; n: number }[];
  avisos: AvisoFull[];
  avisos_24h: number;
  libro: DiaLibro[];
  umbral: { cuadra: number; revisar: number };
  salud: {
    foto_al: string | null;
    ultimo_aviso: string | null;
    sin_sku_hoy: number;
    sin_sku_7d: number;
    ayer: { cuenta: string; nombre: string; dif: number; estado: EstadoCuadre }[];
    /** Publicaciones guardadas en dos filas y las piezas de la fila vieja que no cuenta. */
    dobles: { publicaciones: number; piezas: number };
    tipos_nuevos: { tipo: string; n: number }[];
  };
}

export interface CaminoCuenta {
  abiertas: number;
  pzs_abiertas: number;
  en_proceso: number;
  enviadas: number;
  llegadas: number;
  en_camino: number;
  sin_numero: number;
}

export interface CaminoFull {
  ok: boolean;
  motivo?: string;
  cuentas?: Record<string, CaminoCuenta>;
  faltan?: { salida: string; cuenta: string; nombre: string; enviadas: number; llegadas: number; falta: number;
             dias: number | null }[];
  generado?: string;
  edad_s?: number;
}

/** Cada grupo de aviso: cómo se dice en plural y su color (el punto del horario y del carril). */
export const GRUPO_FULL: Record<GrupoFull | "foto", { texto: string; color: string }> = {
  llego: { texto: "Llegadas", color: "#0284c7" },
  vendido: { texto: "Ventas", color: "#4f46e5" },
  cancelado: { texto: "Cancelaciones", color: "#38bdf8" },
  traslado: { texto: "Traslados", color: "#94a3b8" },
  cuarentena: { texto: "Cuarentena", color: "#94a3b8" },
  ajuste: { texto: "Ajustes de ML", color: "#d97706" },
  retiro: { texto: "Retiros", color: "#c2410c" },
  otro: { texto: "Tipo nuevo", color: "#e11d48" },
  foto: { texto: "Lectura del sync", color: "#64748b" },
};

export const CUADRE_CLS: Record<EstadoCuadre, { texto: string; chip: string; dif: string; fila: string }> = {
  cuadra: { texto: "Cuadra", chip: "bg-indigo-100 text-indigo-800", dif: "text-indigo-800", fila: "" },
  revisar: { texto: "Revisar", chip: "bg-amber-100 text-amber-900", dif: "text-amber-800", fila: "bg-amber-50/60" },
  no_cuadra: { texto: "No cuadra", chip: "bg-orange-100 text-orange-900", dif: "text-orange-800", fila: "bg-orange-50/70" },
};

export const CUENTA_CHIP: Record<string, string> = {
  BEKURA: "bg-indigo-50 text-indigo-800",
  SANCORFASHION: "bg-orange-50 text-orange-800",
};

/** +5 · −61 · 0, con separador de miles y el signo menos tipográfico. */
export const conSigno = (n: number) =>
  n > 0 ? `+${n.toLocaleString("es-MX")}` : n < 0 ? `−${Math.abs(n).toLocaleString("es-MX")}` : "0";
