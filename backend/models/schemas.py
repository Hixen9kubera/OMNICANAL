"""
schemas.py — Modelos Pydantic que definen el contrato de la API que consume
el frontend Next.js. Un mismo producto se "proyecta" según el canal pedido:

  - canal=general          → precio/stock de WooCommerce + resumen de canales
  - canal=mercado_libre    → precio/stock/categoría/FULL de Mercado Libre
  - canal=amazon           → precio/stock/categoría/FBA de Amazon
  - ...etc
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CanalResumen(BaseModel):
    """Indicador de presencia de un SKU en un canal (los 'puntos' de colores)."""
    canal: str
    publicado: bool = False
    item_id: str | None = None     # ml_item_id / asin / etc.
    url: str | None = None


class CategoriaNivel(BaseModel):
    """Un nivel dentro de la ruta de categorías (breadcrumb)."""
    id: str | int | None = None
    nombre: str


class VarianteResumen(BaseModel):
    """Variante de un producto variable de WooCommerce (vista Crear Productos)."""
    sku: str
    nombre: str | None = None      # opciones de atributos ("Café / XL")
    precio: float | None = None
    costo: float | None = None     # costo_unitario de costos_finales
    stock: int | None = None
    valor: float | None = None     # stock × costo
    estado: str | None = None
    contenedor: str | None = None  # nº de contenedor (costos_validados)
    # Presencia de ESTA variante en cada marketplace (Productos / Omnicanal).
    canales: list[CanalResumen] = []


class Producto(BaseModel):
    """Producto proyectado al canal solicitado."""
    sku: str
    wc_id: int | None = None
    odoo_id: int | None = None
    nombre: str
    imagen: str | None = None
    marca: str | None = None
    descripcion_corta: str | None = None  # resumen para la lista de PRODUCTOS

    # Métricas del canal solicitado
    precio: float | None = None
    precio_base: float | None = None  # precio regular / sin descuento
    precio_oferta: float | None = None  # precio de oferta (_sale_price)
    moneda: str = "MXN"
    stock: int | None = None          # stock mostrado (= stock_real del canal)
    # Desglose de inventario (regla: total = real + full + fba)
    stock_real: int | None = None     # lo que se sincroniza (almacén propio / FBM / Flex)
    stock_full: int | None = None     # bodega Mercado Libre (FULL)
    stock_fba: int | None = None      # bodega Amazon (FBA)
    situacion: str | None = None      # estatus del listing (active/paused/published/...)
    estado: str | None = None         # publish / draft / activo / pausado...

    # Categoría completa (todos los niveles) del canal solicitado
    categoria_path: list[CategoriaNivel] = Field(default_factory=list)
    categoria_id: str | int | None = None

    # Específico de marketplaces: ¿logística gestionada? (FULL en ML, FBA en Amazon)
    full: bool | None = None
    full_label: str | None = None     # "FULL", "FBA", "Flex", etc.

    # Estado de publicación en el canal solicitado
    publicado: bool = False
    item_id: str | None = None        # id del listing en el canal
    url: str | None = None            # link al listing

    # Solo en canal=general: presencia en cada marketplace (puntos de colores)
    canales: list[CanalResumen] = Field(default_factory=list)

    # Cuenta del marketplace (Mercado Libre: BEKURA / SANCORFASHION)
    cuenta: str | None = None

    # Valor de inventario (vista Crear Productos): costo de costos_finales,
    # valor = stock × costo (para padres, suma de sus variantes)
    costo: float | None = None
    valor: float | None = None
    contenedor: str | None = None  # nº de contenedor (costos_validados)

    # Tipo de producto en WooCommerce: simple | variable (padre) | variation
    tipo: str | None = None
    # Si es padre (variable): sus variantes (vista Crear Productos)
    variantes: list[VarianteResumen] = Field(default_factory=list)

    # Origen del dato: woocommerce | db | ejemplo
    origen: str = "woocommerce"


class Paginacion(BaseModel):
    page: int
    per_page: int
    total: int
    total_pages: int
    tiene_anterior: bool
    tiene_siguiente: bool


class RespuestaProductos(BaseModel):
    canal: str
    items: list[Producto]
    paginacion: Paginacion
    # False mientras el índice se sigue construyendo (carga progresiva):
    # el total y el orden pueden crecer/acomodarse en los siguientes segundos.
    completo: bool = True


class SubCuentaInfo(BaseModel):
    id: str
    label: str
    es_default: bool = False
    total_productos: int | None = None


class CanalInfo(BaseModel):
    """Config de un canal expuesta al frontend para pintar las pestañas."""
    id: str
    label: str
    color: str
    color_texto: str
    acento: str
    habilitado: bool
    origen: str
    descripcion: str
    total_productos: int | None = None  # conteo (cuando se solicita)
    subcuentas: list[SubCuentaInfo] = Field(default_factory=list)


class DetalleCanal(BaseModel):
    """Detalle de un SKU en un canal concreto (panel de edición/lujo)."""
    canal: str
    publicado: bool
    item_id: str | None = None
    url: str | None = None
    precio: float | None = None
    precio_base: float | None = None
    stock: int | None = None
    stock_real: int | None = None
    stock_full: int | None = None
    stock_fba: int | None = None
    situacion: str | None = None
    full: bool | None = None
    full_label: str | None = None
    categoria_id: str | int | None = None
    categoria_path: list[CategoriaNivel] = Field(default_factory=list)
    estado: str | None = None
    extra: dict = Field(default_factory=dict)  # campos crudos adicionales


class AtributoProducto(BaseModel):
    """Atributo del producto (WooCommerce): nombre + valor."""
    nombre: str
    valor: str = ""


class DetalleProducto(BaseModel):
    """Vista 360°: el producto en TODOS los canales a la vez."""
    sku: str
    wc_id: int | None = None
    odoo_id: int | None = None
    nombre: str
    imagen: str | None = None
    imagenes: list[str] = Field(default_factory=list)
    marca: str | None = None
    descripcion: str | None = None
    descripcion_corta: str | None = None
    atributos: list[AtributoProducto] = Field(default_factory=list)
    precio_base: float | None = None
    precio_oferta: float | None = None
    stock_odoo: int | None = None
    costo: float | None = None
    peso_kg: float | None = None
    dimensiones: str | None = None
    canales: list[DetalleCanal] = Field(default_factory=list)


class HealthCheck(BaseModel):
    status: str
    woocommerce: bool
    base_datos: bool
    odoo: bool
