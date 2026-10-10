/**
 * Tipos de Inventario → ÓRDENES DE VENTA (las propias del panel, folio OV-00001…).
 *
 * Son la forma EXACTA de lo que contesta `backend/routers/ordenes_venta.py`
 * (prefijo `/api/ordenes-venta`). El backend manda las columnas de
 * `ventas.ov_ordenes` / `ventas.ov_lineas` tal cual (migración 0064), más unos pocos
 * campos derivados; aquí no se renombra nada, para que un `grep` del nombre de
 * una columna caiga en los dos lados.
 *
 * EL MODELO (plan v3 de Eduardo, 5-oct-2026; contrato en
 * docs/MIGRACION_0064_0065_GUIA_AGENTE.md): la orden sólo existe en BODEGAS DE
 * KUBERA y la bodega va POR RENGLÓN. Confirmar APARTA todo o nada contra
 * `almacen.stock_almacen`. Una CONFIRMADA todavía se puede corregir (0071,
 * 9-oct-2026): guardar vuelve a apartar, también todo o nada, y deja en la
 * bitácora quién cambió qué. Lo que ya salió, y la orden entregada o cancelada,
 * no cambian.
 *
 * La regla que atraviesa todos los tipos: `null` significa «no lo sabemos» y
 * NUNCA se colapsa a 0. `libre: null` es un SKU sin fila de saldo en esa
 * bodega, no un SKU agotado, y la pantalla los pinta distinto.
 */

/** Los cinco estados. En pantalla se rotulan con `ROTULO_ESTADO` (ui.tsx). */
export type EstadoOrden =
  | "borrador"
  | "confirmada"
  | "entregada"              // DELIVERED: almacén la entregó a la paquetería
  | "cancelada"
  | "entregada_cancelada";   // DELIVERED but CANCELLED: pide devolución

/** `venta` es la de siempre; `full` (envío a FULL) la crea Crear FULL, no esta pantalla. */
export type TipoOrden = "venta" | "full";

/** Qué pasó con el apartado de la orden. Se DERIVA de los renglones; no es columna. */
export type Reserva =
  | "sin_apartar"       // borrador
  | "apartada"          // confirmada, nada ha salido
  | "entrega_parcial"   // confirmada, ya salieron algunos renglones
  | "surtida"           // entregada
  | "liberada";         // cancelada o borrada

/** Por dónde entró un movimiento: persona, llave de API, Claude o un proceso automático. */
export type Via = "panel" | "api" | "claude" | "automatico";

export type DevolucionEstado = "pendiente" | "recibida" | "cerrada";

/**
 * El catálogo CERRADO de `ventas.ov_mensajes.evento` (CHECK de la 0064, más
 * `editada` —la corrección de una confirmada— que agregó la 0071).
 */
export type EventoOrden =
  | "creada" | "borrador_guardado" | "descartada" | "confirmada" | "editada" | "no_alcanzo"
  | "entregada_parcial" | "entregada" | "cancelada" | "borrada_admin" | "canal_cancelo"
  | "devolucion_esperada" | "devolucion_recibida" | "devolucion_aprobada"
  | "devolucion_merma" | "devolucion_cerrada";

/** Una bodega del catálogo `almacen.almacenes`. Las OV sólo viven en las de kubera con `admite_ov`. */
export interface Bodega {
  codigo: string;
  nombre: string;
  fuente: "odoo" | "kubera";
  admite_ov: boolean;
  surte_ventas: boolean;
  cuenta_para_woo: boolean;
}

/** Un renglón tal como se CAPTURA (el alta, y el guardado de un borrador o de una confirmada). */
export interface LineaEntrada {
  sku: string;
  cantidad: number;
  precio_unitario: number;
  titulo?: string | null;
  imagen?: string | null;
  /**
   * Código de la bodega de kubera de la que sale. Obligatoria para confirmar, y
   * en todo renglón por entregar de una confirmada (ahí se aparta al guardar).
   */
  almacen?: string | null;
}

/** Un renglón tal como lo DEVUELVE el backend. */
export interface LineaOrden {
  id: number;
  linea: number;
  sku: string;
  titulo: string | null;
  imagen: string | null;
  cantidad: number;
  precio_unitario: number;
  /** cantidad × precio_unitario. */
  importe: number;
  /** Bodega de kubera del renglón (`null` sólo en un borrador que aún no la elige). */
  almacen: string | null;
  /** Piezas apartadas: 0 o `cantidad` (se aparta todo o nada). */
  reservado: number;
  /** Piezas que salieron al entregar este renglón (`null` = todavía no sale). */
  entregado: number | null;
  /** Con fecha, el renglón YA SALIÓ: al editar una confirmada se ve, pero no se toca ni se quita. */
  entregado_at: string | null;
  entregado_por: string | null;
  /** Saldo del SKU EN SU BODEGA (`almacen.stock_almacen`). `null` = sin bodega o sin fila de saldo. */
  fisico: number | null;
  apartado: number | null;
  /** fisico − apartado. */
  libre: number | null;
  /** ¿El SKU existe en el catálogo (core.products)? */
  conocido: boolean;
}

/** Lo que trae cada fila de la lista. Son las columnas de ventas.ov_ordenes + derivados. */
export interface OrdenResumen {
  id: number;
  folio: string;
  estado: EstadoOrden;
  tipo: TipoOrden;
  /** Candado optimista: se manda de vuelta en cada escritura. */
  rev: number;

  cliente: string | null;
  canal: string | null;
  mp_canal: string | null;
  mp_cuenta: string | null;
  /** ORDEN DE MARKETPLACE: el id de la venta en el canal. */
  mp_orden: string | null;
  full_tienda: string | null;
  envio_ref: string | null;
  descripcion: string | null;
  guia: string | null;
  paqueteria: string | null;
  fecha_venta: string | null;
  entrega_limite: string | null;

  moneda: string;
  total: number;
  comision: number;
  /** total − comision. */
  neto: number;
  precio_origen: "manual" | "marketplace";

  devolucion_estado: DevolucionEstado | null;
  /**
   * El canal canceló con el paquete ya en camino: la orden espera que Bodega
   * conteste «¿salió?». Con la marca puesta ya no se puede marcar DELIVERED.
   */
  canal_cancelo_at: string | null;
  canal_cancelo_ref: string | null;

  creado_at: string;
  creado_por: string;
  creado_nombre: string | null;
  creado_via: Via;
  confirmada_at: string | null;
  confirmada_por: string | null;
  confirmada_nombre: string | null;
  entregada_at: string | null;
  entregada_por: string | null;
  entregada_nombre: string | null;
  cancelada_at: string | null;
  cancelada_por: string | null;
  cancelada_nombre: string | null;
  cancelada_origen: "manual" | "marketplace" | "sistema" | null;
  cancelada_motivo: string | null;
  borrada_at: string | null;
  borrada_por: string | null;
  borrada_nombre: string | null;
  borrada_motivo: string | null;
  actualizado_at: string;

  /** Derivados de los renglones. */
  renglones: number;
  piezas: number;
  /** Piezas apartadas hoy (renglones confirmados que no han salido). */
  piezas_apartadas: number;
  /** Piezas que ya salieron. */
  piezas_entregadas: number;
  /** Renglones que ya salieron (para la entrega parcial). */
  renglones_entregados: number;
  /** Hasta tres SKUs, para la fila de la lista. */
  skus: string[];
  /** Las bodegas de sus renglones. */
  bodegas: string[];
  n_archivos: number;
  n_mensajes: number;
}

/** Qué puede hacer QUIEN PREGUNTA con esta orden. Lo decide el backend; aquí sólo se pinta. */
export interface Permisos {
  /**
   * Editar encabezado y renglones: un borrador, o una CONFIRMADA (0071) mientras
   * el backend lo permita —quien escribe, con la bandera encendida, sin el
   * «¿salió?» pendiente y con la migración aplicada—. Cuando no, el porqué
   * viene en `porque.editar`. Entregada y cancelada no se editan.
   */
  editar: boolean;
  confirmar: boolean;
  entregar: boolean;
  /** Cancelar: un borrador o una confirmada, quien escribe (la confirmada ya no es sólo de admin). */
  cancelar: boolean;
  /** Admin: borrado lógico (queda quién y por qué). */
  borrar: boolean;
  /** Contestar «¿salió?» cuando el canal canceló con el paquete en camino. */
  responder_salio: boolean;
  /** Admin: una cancelada que sí había salido (pasa a DELIVERED but CANCELLED). */
  salio_tarde: boolean;
  mensajes: boolean;
  subir_archivo: boolean;
  /** Bajar un PDF: operador o admin. */
  bajar_archivo: boolean;
  borrar_archivo: boolean;
  /** Por acción, POR QUÉ no se puede (va al `title` del botón apagado). */
  porque: Record<string, string>;
}

/** Sólo estos: las guías con la dirección del comprador NO se guardan en kubera. */
export type TipoArchivo = "comprobante" | "factura" | "envio_full";

export interface Archivo {
  id: number;
  orden_id: number;
  tipo: TipoArchivo;
  nombre: string;
  bytes: number;
  sha256: string;
  subido_at: string;
  subido_por: string;
  subido_nombre: string | null;
}

/** El detalle: `GET /api/ordenes-venta/{id}` y la respuesta de toda escritura. */
export interface Orden extends OrdenResumen {
  lineas: LineaOrden[];
  archivos: Archivo[];
  permisos: Permisos;
}

/** Respuesta de toda escritura sobre una orden. */
export interface RespOrden {
  ok: boolean;
  orden: Orden;
  /** Una línea para el aviso de pantalla («Confirmada: 3 renglones apartados»). */
  mensaje?: string;
}

/**
 * El cuerpo del alta y del guardado (de un borrador o de una confirmada). Lo
 * que no se manda no se toca. `lineas`, si viaja, es el documento ENTERO: en
 * una confirmada incluye los renglones que ya salieron, tal cual (el servidor
 * los reconoce y no los toca).
 */
export interface DatosOrden {
  cliente?: string | null;
  canal?: string | null;
  mp_canal?: string | null;
  mp_cuenta?: string | null;
  mp_orden?: string | null;
  descripcion?: string | null;
  guia?: string | null;
  paqueteria?: string | null;
  fecha_venta?: string | null;
  entrega_limite?: string | null;
  moneda?: string;
  /** `null` = que el total sea la suma de los renglones. */
  total?: number | null;
  comision?: number | null;
  precio_origen?: "manual" | "marketplace";
  lineas?: LineaEntrada[];
}

/** Lo que salió de cada renglón al entregar. Sin esto, salen todos completos. */
export interface EntregaLinea {
  id: number;
  /** Piezas que salieron: 0..cantidad. Lo que no sale se suelta. */
  n: number;
}

export type FiltroEstado = EstadoOrden | "todas" | "por_devolver" | "borradas";

export interface ListaOrdenes {
  ok: boolean;
  /** Faltan las migraciones 0064/0065 en kubera: la pestaña lo dice y no truena. */
  falta_migracion?: boolean;
  motivo?: string;
  ordenes: OrdenResumen[];
  total: number;
  pagina: number;
  por_pagina: number;
  paginas: number;
  /** Conteos de TODA la tabla (no de la página), por filtro. */
  conteos: Record<FiltroEstado, number>;
}

/** Una bandera de `ops.automatizacion_flags`. Las enciende un acta, no la pantalla. */
export interface Bandera {
  encendido: boolean;
  /** ¿Hay fila en la tabla? Si no, manda la variable de respaldo (que vale false). */
  persistido: boolean;
  actualizado_por: string | null;
  motivo: string | null;
  actualizado_at: string | null;
}

/** Quién soy para este módulo, y qué está encendido. `GET /api/ordenes-venta/estado`. */
export interface EstadoModulo {
  ok: boolean;
  falta_migracion: boolean;
  motivo?: string;
  /**
   * La bandera `ordenes_venta` (respaldo: ORDENES_VENTA_ENABLED). Apagada =
   * modo prueba: sólo borradores, sin confirmar ni entregar.
   */
  habilitado: boolean;
  banderas: {
    ordenes_venta: Bandera;
    /** Que el planeador genere órdenes solas (todavía no las genera). Sólo lectura aquí. */
    ov_generacion_auto: Bandera;
  };
  /** Catálogo de bodegas. Las elegibles en un renglón son las de kubera con `admite_ov`. */
  bodegas: Bodega[];
  /** ¿Se pueden adjuntar PDF? No, mientras no exista el bucket `ordenes-venta`. */
  archivos: { disponible: boolean; motivo: string | null };
  yo: {
    actor: string;
    nombre: string;
    rol: string;
    via: Via;
    /** Puede borrar, quitar un PDF y registrar un «salió tarde». (Cancelar una confirmada ya es de quien escribe.) */
    admin: boolean;
    /** Puede crear y mover órdenes (operador o admin). */
    escribe: boolean;
  };
}

export interface Mensaje {
  id: number;
  orden_id: number;
  tipo: "sistema" | "usuario";
  /** `null` en los mensajes de personas y en los avisos de PDF. */
  evento: EventoOrden | null;
  cuerpo: string;
  datos: Record<string, unknown> | null;
  /** Correo de quien lo mandó, o 'servicio' / 'automatico'. */
  autor: string;
  autor_nombre: string | null;
  via: Via;
  creado_at: string;
}

/**
 * Lo que traen los `datos` de un mensaje cuando se guardaron cambios en los
 * renglones (`borrador_guardado`, y `editada` al corregir una confirmada).
 * `datos` es un jsonb que sólo se agrega: el chat lo LEE con cuidado
 * (`renglonesDe` y `apartadoDe` de ChatOrden.tsx) y lo deja en estas formas.
 */
export interface RenglonCambiado {
  sku: string;
  /** [antes, después]. Sólo viene lo que cambió. */
  cantidad?: [number, number];
  precio_unitario?: [number, number];
  /**
   * [antes, después] del título y de la imagen del renglón (`null` = no tenía,
   * o se quedó sin). No son números: el chat sólo dice QUE cambiaron.
   */
  titulo?: [string | null, string | null];
  imagen?: [string | null, string | null];
}

/** `datos.renglones`: qué SKUs entraron, cuáles se quitaron y cuáles cambiaron. */
export interface RenglonesEditados {
  agregados: string[];
  quitados: string[];
  cambiados: RenglonCambiado[];
}

/**
 * Una entrada de `datos.apartado` (sólo en `editada`): cuánto se movió el
 * apartado de un SKU en una bodega al guardar. Positivo = se apartaron más
 * piezas; negativo = se soltaron. Sólo vienen los que se movieron.
 */
export interface ApartadoMovido {
  sku: string;
  almacen: string;
  delta: number;
}

/**
 * `GET /api/ordenes-venta/{id}/mensajes?desde_id=N&esperar=S`.
 * Con `esperar`, el backend sostiene la petición hasta que haya algo nuevo (o
 * venza): por eso el chat se ve en vivo sin sondear cada segundo.
 */
export interface RespMensajes {
  mensajes: Mensaje[];
  ultimo_id: number;
  /** Total de mensajes de la orden: si no cuadra con lo que tengo, recargo todo. */
  total: number;
  /** rev y estado ACTUALES de la orden: si cambiaron, alguien la movió → recargo el documento. */
  rev: number;
  estado: EstadoOrden;
}

/** El saldo de un SKU en una bodega de kubera. */
export interface Existencia {
  almacen: string;
  fisico: number;
  apartado: number;
  libre: number;
}

/** Una opción del buscador de productos. */
export interface SkuOpcion {
  sku: string;
  nombre: string | null;
  /** Una por bodega de kubera donde el SKU tiene fila de saldo. Vacío = sin existencias registradas. */
  existencias: Existencia[];
}

/** Una venta de marketplace, para prellenar un borrador a mano. */
export interface VentaMarketplace {
  canal: string;
  cuenta: string;
  orden: string;
  fecha: string | null;
  estado_canal: string | null;
  estado_wc: string | null;
  cancelada: boolean;
  /** FULL / FBA / WFS: sale del almacén del marketplace, NO lleva orden propia. */
  es_fulfillment: boolean;
  total: number | null;
  comision: number | null;
  neto: number | null;
  guia: string | null;
  paqueteria: string | null;
  entrega_limite: string | null;
  piezas: number;
  lineas: LineaEntrada[];
  /** Renglones de la venta SIN SKU: cuentan en `piezas` pero no entran en `lineas`. */
  renglones_sin_sku?: number;
  /** Si esa venta YA tiene orden propia viva. */
  ov: { id: number; folio: string; estado: EstadoOrden } | null;
}

export interface RespVentas {
  ok: boolean;
  motivo?: string;
  ventas: VentaMarketplace[];
}

/** `POST /api/ordenes-venta/conciliar`: lo que hizo la revisión de cancelaciones del canal. */
export interface RespConciliar {
  ok: boolean;
  motivo?: string;
  /** Canceladas (o DELIVERED but CANCELLED) porque el canal canceló. */
  canceladas: { id: number; folio: string; estado: EstadoOrden }[];
  /** El canal canceló con el paquete en camino: quedaron esperando el «¿salió?». */
  marcadas: { id: number; folio: string }[];
}
