// Tipos que reflejan el contrato del backend FastAPI (models/schemas.py).

export interface CategoriaNivel {
  id: string | number | null;
  nombre: string;
}

export interface CanalResumen {
  canal: string;
  publicado: boolean;
  item_id: string | null;
  url: string | null;
}

export interface Producto {
  sku: string;
  wc_id: number | null;
  odoo_id: number | null;
  nombre: string;
  imagen: string | null;
  marca: string | null;
  descripcion_corta: string | null;
  precio: number | null;
  precio_base: number | null;
  moneda: string;
  stock: number | null;
  stock_real: number | null;
  stock_full: number | null;
  stock_fba: number | null;
  situacion: string | null;
  estado: string | null;
  categoria_path: CategoriaNivel[];
  categoria_id: string | number | null;
  full: boolean | null;
  full_label: string | null;
  publicado: boolean;
  item_id: string | null;
  url: string | null;
  canales: CanalResumen[];
  cuenta: string | null;
  origen: string;
}

export interface Paginacion {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
  tiene_anterior: boolean;
  tiene_siguiente: boolean;
}

export interface RespuestaProductos {
  canal: string;
  items: Producto[];
  paginacion: Paginacion;
}

export interface SubCuentaInfo {
  id: string;
  label: string;
  es_default: boolean;
  total_productos: number | null;
}

export interface CanalInfo {
  id: string;
  label: string;
  color: string;
  color_texto: string;
  acento: string;
  habilitado: boolean;
  origen: string;
  descripcion: string;
  total_productos: number | null;
  subcuentas: SubCuentaInfo[];
}

export interface DetalleCanal {
  canal: string;
  publicado: boolean;
  item_id: string | null;
  url: string | null;
  precio: number | null;
  precio_base: number | null;
  stock: number | null;
  stock_real: number | null;
  stock_full: number | null;
  stock_fba: number | null;
  situacion: string | null;
  full: boolean | null;
  full_label: string | null;
  categoria_id: string | number | null;
  categoria_path: CategoriaNivel[];
  estado: string | null;
  extra: Record<string, unknown>;
}

export interface WebhookEvento {
  id: number;
  canal: string;
  topic: string | null;
  resource: string | null;
  cuenta: string | null;
  sku: string | null;
  resultado: string | null;
  recibido: string;
}

export interface AtributoProducto {
  nombre: string;
  valor: string;
}

export interface DetalleProducto {
  sku: string;
  wc_id: number | null;
  odoo_id: number | null;
  nombre: string;
  imagen: string | null;
  imagenes: string[];
  marca: string | null;
  descripcion: string | null;
  descripcion_corta: string | null;
  atributos: AtributoProducto[];
  precio_base: number | null;
  precio_oferta: number | null;
  stock_odoo: number | null;
  costo: number | null;
  peso_kg: number | null;
  dimensiones: string | null;
  canales: DetalleCanal[];
}

// ── IA: generadores de contenido por canal ──────────────────────────
export interface GeneradorDef {
  id: string;
  label: string;
  icono: string;
  descripcion: string;
  tipo?: "texto" | "imagenes";
  max_tokens?: number;
}

export interface GenerarIAResp {
  ok: boolean;
  texto?: string;
  modelo?: string;
  proveedor?: string;
  motivo?: string;
  canal: string;
  generador: string;
  label: string;
  tipo: "texto" | "imagenes";
}
