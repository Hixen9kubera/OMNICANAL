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
  precio: number | null;
  precio_base: number | null;
  moneda: string;
  stock: number | null;
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
  full: boolean | null;
  full_label: string | null;
  categoria_id: string | number | null;
  categoria_path: CategoriaNivel[];
  estado: string | null;
  extra: Record<string, unknown>;
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
  stock_odoo: number | null;
  costo: number | null;
  peso_kg: number | null;
  dimensiones: string | null;
  canales: DetalleCanal[];
}
