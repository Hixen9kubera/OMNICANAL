/**
 * Tipos y estilos compartidos de la página del fan-out en vivo
 * (/dashboard, /dashboard/matriz, /dashboard/full, /dashboard/bodegas y los cajones).
 * La forma de los datos la define `backend/services/fanout_vivo.py` (FULL y Bodegas, la suya).
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

// ── La pestaña Bodegas (`backend/services/fanout_bodegas.py`) ────────────────

export interface CeldaOdoo {
  fisico: number;
  reservado: number;
  libre: number;
}

export interface CeldaKubera {
  fisico: number;
  apartado: number;
  libre: number;
}

/**
 * Woo de la foto contra el «Woo esperado»: `mas` es Woo ofreciendo de más (sobreventa).
 * `por_copiar`: algo se movió desde la foto (Odoo hoy no es el de la foto, kubera cambió,
 * o hubo una venta u orden después de la pasada); lo resuelve la próxima pasada, y si
 * sigue igual después de ella es una escritura fallida, un freno o el modo solo registro.
 */
export type CoincideBodega = "igual" | "mas" | "menos" | "por_copiar" | "no_toca";
export type EstadoPuerta = "sin_formato" | "por_confirmar" | "esperando" | "abierta";

export interface PuertaBodega {
  estado: EstadoPuerta;
  texto: string;
  renglones?: number;
  piezas?: number;
  folios?: string;
  via?: string;
  abierta?: string | null;
}

export interface FilaBodega {
  sku: string;
  nombre: string;
  /** null = Odoo no contestó; {} = contestó y el código no existe allá. */
  odoo: Record<string, CeldaOdoo> | null;
  odoo_existe: boolean;
  /** El `free_qty` total que leyó (y absorbió) la última pasada de stock_watch. */
  odoo_total: number | null;
  /** max(0, `free_qty`) de HOY del producto que copia stock_watch, en la misma lectura que las tres bodegas. */
  odoo_hoy: number | null;
  /** `free_qty` de hoy − Σ libre de las tres bodegas (misma lectura): lo que Odoo tiene en otras bodegas. */
  otras: number | null;
  avisos: string[];
  kubera: Record<string, CeldaKubera>;
  /** Σ libre ≥ 0 de las bodegas kubera con cuenta_para_woo. */
  libre_kubera: number;
  /** Vendidas sin orden en Odoo (lo que resta stock_watch). */
  pend: number;
  esperado: number | null;
  esperado_motivo: "calculado" | "delta" | "ciega" | "sin_odoo";
  esperado_d: string;
  /** El Odoo y el kubera que entran en «Woo esperado» (kubera null = no suma). */
  odoo_base: number | null;
  kubera_base: number | null;
  woo: number | null;
  /** Hora local de la foto de stock_watch («YYYY-MM-DD HH:MM:SS»). */
  woo_de: string | null;
  stock_kubera_foto: number | null;
  coincide: CoincideBodega;
  dif: number | null;
  coincide_t: string;
  puerta: PuertaBodega;
  tags: string[];
}

export interface AlmacenBodega {
  codigo: string;
  nombre: string;
  fuente: "odoo" | "kubera";
  odoo_warehouse_id: number | null;
  preferencia: number | null;
  surte_ventas: boolean;
  admite_ov: boolean;
  cuenta_para_woo: boolean;
  motivo: string | null;
  actualizado: string | null;
}

export interface BanderaBodega {
  flag: string;
  que: string;
  encendida: boolean;
  /** fila en ops.automatizacion_flags · sin fila (manda la variable, que vale false) · lectura fallida (apagada). */
  fuente: "fila" | "variable" | "error";
  variable: string | null;
  motivo: string | null;
  por: string | null;
  actualizado: string | null;
}

export interface ResumenBodegas {
  ok: boolean;
  motivo?: string;
  ahora: string;
  hoy: string;
  tablas: { ok: boolean; faltan: string[]; vigia: boolean; stock_kubera: boolean };
  almacenes: AlmacenBodega[];
  banderas: BanderaBodega[];
  stock_watch: {
    habilitado: boolean;
    solo_registro: boolean;
    tope: number | null;
    modo: string | null;
    absoluto: boolean;
    resta: boolean;
    dias: number | null;
    estado_memoria: string | null;
    ultima: string | null;
    edad_s: number | null;
    filas_foto: number;
    /** null = stock_watch de esta versión no sabe sumar kubera. */
    suma_kubera: boolean | null;
    pendientes: {
      aplica: boolean; ciega: boolean; ventas: number | null; mas_vieja_h?: number | null; motivo?: string;
      /** SKUs con una venta u orden posterior a la pasada (quedan «por copiar», no «de más»). */
      recientes?: number; recientes_error?: string | null;
    };
  };
  formula: { texto: string; suma_kubera: boolean; absoluto: boolean; resta: boolean; solo_registro: boolean; habilitado: boolean };
  formatos: Partial<Record<"por_confirmar" | "confirmados" | "esperando" | "abiertas_hoy" | "abiertas" | "movimientos" | "ov_abiertas" | "renglones", number>>;
  vigia: { problema: string; sku: string | null; almacen: string | null; ref: string | null; esperado: unknown; encontrado: unknown; detalle: string | null }[];
  que_falta: { paso: string; hecho: boolean }[];
  odoo: {
    ok: boolean;
    motivo?: string | null;
    viejo: boolean;
    edad_s: number | null;
    leido: string | null;
    /** La lectura es posterior a la última pasada: sólo así se compara Odoo hoy contra la foto. */
    tras_pasada: boolean;
    skus_tex2: number | null;
    archivados_tex2: number;
    piezas_archivadas_tex2: number;
    duplicados: number;
  };
  columnas_odoo: { codigo: string; nombre: string; warehouse_id: number }[];
  columnas_kubera: { codigo: string; nombre: string; cuenta_para_woo: boolean }[];
  /** Sin Odoo no se sabe qué hay en TEX2: la tabla sólo trae lo de kubera. */
  universo_parcial: boolean;
  conteo: { filas: number; no_coincide: number; de_mas: number; por_copiar: number; esperando: number; kubera: number };
  filas: FilaBodega[];
}

export interface MovLibro {
  id: number;
  almacen: string;
  delta: number;
  saldo_despues: number;
  motivo: string;
  ref: string | null;
  nota: string | null;
  quien: string | null;
  via: string | null;
  hora: string;
}

export interface RenglonFormato {
  folio: string;
  estado: "por_confirmar" | "confirmado" | "descartado";
  almacen: string;
  fila: number;
  cantidad: number;
  cantidad_archivo: number;
  sku_archivo: string;
  ubicacion: string | null;
  nota: string | null;
  aviso: string | null;
  via: string | null;
  via_t: string | null;
  ref_odoo: string | null;
  odoo_tex2_al_cargar: number | null;
  odoo_tex2_al_confirmar: number | null;
  odoo_tex2_al_abrir: number | null;
  abierta: string | null;
  cargado: string | null;
  confirmado: string | null;
}

export interface LineaOv {
  folio: string;
  estado: "borrador" | "confirmada" | "entregada" | "cancelada" | "entregada_cancelada";
  tipo: "venta" | "full";
  canal: string | null;
  full_tienda: string | null;
  borrada: boolean;
  linea: number;
  cantidad: number;
  almacen: string | null;
  reservado: number;
  entregado: number | null;
  creada: string | null;
  confirmada: string | null;
  entregada: string | null;
  cancelada: string | null;
}

export interface DetalleBodega {
  ok: boolean;
  motivo?: string;
  sku: string;
  existe: boolean;
  hoy: string;
  tablas: ResumenBodegas["tablas"];
  fila: FilaBodega;
  odoo: { ok: boolean; motivo?: string | null; edad_s?: number | null; viejo?: boolean };
  columnas_odoo: ResumenBodegas["columnas_odoo"];
  libro: MovLibro[];
  libro_total: number;
  renglones: RenglonFormato[];
  ov: LineaOv[];
  formula: ResumenBodegas["formula"];
}

/** La celda «Coincide» reusa los colores de la matriz contra Woo (contraste ≥ 4.5:1). */
export const COINCIDE_BODEGA: Record<CoincideBodega, { texto: string; cls: string }> = {
  igual: { texto: "Coincide", cls: CELDA_MATRIZ_SOLIDO.igual },
  mas: { texto: "Woo de más", cls: CELDA_MATRIZ_SOLIDO.mas },
  menos: { texto: "Woo de menos", cls: CELDA_MATRIZ_SOLIDO.menos },
  por_copiar: { texto: "Por copiar", cls: CAUSA_CLS.camino },
  no_toca: { texto: "Sin comparar", cls: "border border-slate-200 bg-slate-50 text-slate-700" },
};

/** Chip de la puerta (contraste ≥ 4.5:1). «Esperando» usa el mismo índigo que «En camino». */
export const PUERTA_CLS: Record<EstadoPuerta, string> = {
  sin_formato: "bg-slate-100 text-slate-700",
  por_confirmar: "bg-amber-100 text-amber-900",
  esperando: CAUSA_CLS.camino,
  abierta: "bg-emerald-50 text-emerald-800",
};

/** Los motivos del libro (`stock_mov_motivo_chk`, guía §3.12). */
export const MOTIVO_MOV: Record<string, string> = {
  entrada: "Entrada",
  salida_ov: "Salida por OV",
  traspaso_salida: "Traspaso: sale",
  traspaso_entrada: "Traspaso: entra",
  devolucion: "Devolución",
  ajuste_conteo: "Ajuste por conteo",
  merma: "Merma",
  correccion: "Corrección",
};

const MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** «11:50» si la hora local (CDMX) es de `hoy`; si no, «2 oct 17:58». */
export function horaCorta(hora: string | null | undefined, hoy: string): string {
  if (!hora) return "—";
  if (hora.slice(0, 10) === hoy) return hora.slice(11, 16);
  const [, m, d] = hora.slice(0, 10).split("-").map(Number);
  return `${d} ${MESES_CORTOS[m - 1]} ${hora.slice(11, 16)}`;
}

/** Segundos de antigüedad → «hace segundos» · «hace 4 min» · «hace 3 h» · «hace 2 d». */
export function haceSegundos(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return "sin registro";
  const min = Math.floor(s / 60);
  if (min < 1) return "hace segundos";
  if (min < 60) return `hace ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.floor(h / 24)} d`;
}
