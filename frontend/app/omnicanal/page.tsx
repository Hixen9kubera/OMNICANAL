"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search, Filter, RotateCw, Sparkles, Layers, Loader2,
  ShoppingCart, AlertTriangle, Info,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import MarketplaceTabs from "@/components/MarketplaceTabs";
import AccountTabs from "@/components/AccountTabs";
import ChannelLegend from "@/components/ChannelLegend";
import Pagination from "@/components/Pagination";
import ProductGrid from "@/components/ProductGrid";
import ProductList from "@/components/ProductList";
import ProductControls, { type ModoFlujo, type Vista } from "@/components/ProductControls";
import ProductDetailDrawer from "@/components/ProductDetailDrawer";
import ResumenPublicacionesCanal from "@/components/ResumenPublicacionesCanal";

import {
  ApiError, flujoCanal, listarCanales, listarProductos, listarCategorias,
  mensajeDeError, type CategoriaWC,
} from "@/lib/api";
import type {
  CanalInfo, ConteoCanalFlujo, EstadoFotoFlujo, EtapaOmnicanal, FiltroActivas,
  Paginacion, Producto,
} from "@/lib/types";
import { criterioDe } from "@/lib/flujo";
import { THEME_FALLBACK, hexToRgba, variablesTema, type CanalTheme } from "@/lib/theme";

const PER_PAGE = 40;
const GENERAL = "general";

const PAG_CERO: Paginacion = {
  page: 1, per_page: PER_PAGE, total: 0, total_pages: 1,
  tiene_anterior: false, tiene_siguiente: false,
};

// La foto tarda 12–35 s en armarse tras un redeploy: 8 vueltas de 15 s cubren
// el caso normal sin martillar el backend si se atora.
const REINTENTOS_CONTEOS = 8;
const ESPERA_CONTEOS_MS = 15_000;
// Un error de red o un 502 de un backend que se está levantando no es lo mismo
// que «esta ruta no existe»: dos vueltas cortas y se deja de insistir.
const REINTENTOS_ERROR_CONTEOS = 2;
const ESPERA_ERROR_CONTEOS_MS = 2_000;

export default function OmnicanalPage() {
  const [canales, setCanales] = useState<CanalInfo[]>([]);
  const [canal, setCanal] = useState<string>(GENERAL);
  const [cuenta, setCuenta] = useState<string | null>(null);

  const [productos, setProductos] = useState<Producto[]>([]);
  const [pag, setPag] = useState<Paginacion>({
    page: 1, per_page: PER_PAGE, total: 0, total_pages: 1,
    tiene_anterior: false, tiene_siguiente: false,
  });
  const [page, setPage] = useState(1);
  const [busquedaInput, setBusquedaInput] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [skusInput, setSkusInput] = useState("");
  const [skusFiltro, setSkusFiltro] = useState("");
  const [soloPublicados, setSoloPublicados] = useState(false);
  // "Solo activas": lo que se puede COMPRAR hoy, con el criterio de cada canal.
  // Excluyente con `soloPublicados` porque en el backend `solo_activas` MANDA
  // sobre él (no se suman con AND): tenerlos encendidos a la vez mostraría un
  // chip que no está haciendo nada.
  const [soloActivas, setSoloActivas] = useState(false);
  // Qué hizo el backend con la petición del filtro. `null` = nadie lo pidió.
  // NO es decorativo: distingue "0 activas de verdad" de "no se pudo filtrar".
  const [filtroActivas, setFiltroActivas] = useState<FiltroActivas | null>(null);
  const [cargando, setCargando] = useState(true);
  // Arranque en frío del backend: el índice puede tardar varios segundos en
  // construirse. "0 resultados" en ese momento no significa catálogo vacío.
  const [preparando, setPreparando] = useState(false);
  const reintentos = useRef(0);
  // true hasta que la carga de este CANAL termine al menos una vez (con o sin
  // filtro). Evita ver "No se encontraron productos" al entrar a una pestaña,
  // antes de que llegue la respuesta real.
  const primeraCarga = useRef(true);
  const [sel, setSel] = useState<Producto | null>(null);

  // ── Llegar desde una alerta con el filtro ya puesto ──────────────────────
  // Las alertas de la campana enlazan a `/omnicanal?skus=A,B,C`. Sin esto el
  // enlace aterrizaba en el catálogo COMPLETO y había que copiar los SKUs a
  // mano: el aviso decía qué está mal y no llevaba a ningún lado.
  //
  // Se lee de `window.location.search` y no con `useSearchParams`: ese hook
  // obliga a envolver la pagina en un <Suspense> para el render estatico, y
  // ninguna pantalla del panel lo usa todavia. Dentro de un efecto el codigo
  // solo corre en el navegador, asi que no hay nada que envolver.
  //
  // SIN lista de dependencias: corre tras CADA render y decide con `urlPuesta`.
  // Estando ya en /omnicanal, un clic en la campana es navegacion de CLIENTE:
  // la URL cambia y el componente NO se vuelve a montar, asi que un efecto de
  // montaje (`[]`) no se entera y el filtro se queda con lo anterior. El
  // candado es la URL misma: solo se pisa el campo cuando el valor de `skus`
  // es DISTINTO al ya aplicado, asi que borrarlo a mano no lo vuelve a llenar
  // (la URL no cambio) y escribir en el no dispara nada.
  const abrirUnico = useRef<string | null>(null);
  const urlPuesta = useRef<string | null>(null);
  useEffect(() => {
    const par = new URLSearchParams(window.location.search);
    const q = par.get("skus") ?? "";
    // El canal entra en la llave del candado: dos renglones de la MISMA alerta
    // pueden traer el mismo SKU en cuentas distintas.
    const llave = `${q}|${par.get("canal") ?? ""}|${par.get("cuenta") ?? ""}`;
    if (llave === urlPuesta.current) return;
    urlPuesta.current = llave;

    const lista = q.split(",").map((s) => s.trim()).filter(Boolean);
    if (!lista.length) return;

    // La alerta manda a la PESTAÑA donde está el problema (Eduardo, 8-sep): el
    // margen negativo lo causa una publicación de BEKURA o San Corpe, y en
    // General ni siquiera se ve ese precio. Se llama a los setters sueltos y NO
    // a `seleccionarCanal`, que además LIMPIA el filtro de SKUs — borraría lo
    // que esta misma vuelta acaba de poner. De ahí solo se copia lo que hace
    // falta para que la pestaña se vea como si la hubieran elegido a mano.
    const canalUrl = par.get("canal");
    if (canalUrl && canalUrl !== canal) {
      setCanal(canalUrl);
      setSoloPublicados(canalUrl !== GENERAL);
    }
    // Sin `cuenta` se queda en "Todas", que es lo correcto cuando la lista
    // mezcla cuentas: fijar una escondería la mitad.
    setCuenta(par.get("cuenta"));

    // El mismo texto en los dos: el debounce de `skusInput` reescribe
    // `skusFiltro` 500 ms despues y, si difieren, la pagina se recarga dos
    // veces. El backend ya recorta los espacios (v0.432.0).
    const texto = lista.join(", ");
    setSkusInput(texto);
    setSkusFiltro(texto);
    setPage(1);
    // Y SE LIMPIA LA ETAPA: con una puesta, la alerta aterrizaría en la
    // intersección de sus SKUs con la etapa — casi siempre vacía — y el aviso
    // volvería a no llevar a ningún lado.
    setEtapa(null);
    setErrorFiltro(null);

    // Con UN solo SKU, ademas de filtrar se ABRE su ficha: venir de una alerta
    // de un producto concreto y tener que dar otro clic es un paso de mas. Con
    // varios no se abre ninguna -- elegir una de catorce seria arbitrario.
    // La ficha vieja se cierra siempre: al saltar de un SKU a otro se quedaba
    // abierta la del anterior, contradiciendo al filtro que ya cambio.
    setSel(null);
    abrirUnico.current = lista.length === 1 ? lista[0].toLowerCase() : null;

    // Y SE LIMPIA LA URL (Eduardo, 8-sep). Recargar volvia a poner el SKU de la
    // alerta, porque el filtro vivia en la direccion y F5 la conserva: quedabas
    // atrapado en un producto sin manera obvia de salir. La alerta es un ATAJO
    // de una vez, no un estado; una vez aplicada, la direccion vuelve a ser la
    // pestana normal y recargar devuelve el catalogo completo.
    // `replaceState` no es navegacion: no vuelve a disparar este efecto, y en el
    // siguiente render la llave queda vacia y se sale antes de tocar nada. El
    // segundo clic a la misma alerta sigue funcionando porque ese SI empuja una
    // direccion nueva. Se conservan los demas parametros por si algun dia hay.
    par.delete("skus"); par.delete("canal"); par.delete("cuenta");
    const resto = par.toString();
    window.history.replaceState(null, "", window.location.pathname + (resto ? `?${resto}` : ""));
  });

  // El detalle se abre cuando LLEGAN los productos, no al montar: en ese
  // momento todavía no existe la fila que hay que seleccionar.
  useEffect(() => {
    if (!abrirUnico.current || !productos.length) return;
    const p = productos.find((x) => x.sku.toLowerCase() === abrirUnico.current);
    if (p) {
      setSel(p);
      abrirUnico.current = null;   // una sola vez: que no reabra al paginar
    }
  }, [productos]);

  // Vista, orden y filtros
  const [vista, setVista] = useState<Vista>("mosaico");
  const [orden, setOrden] = useState("reciente");
  const [estados, setEstados] = useState<string[]>([]);
  const [categoria, setCategoria] = useState<number | null>(null);
  // "Costo validado": solo los productos con la marca `revisado_at`
  // (migración 0032). Vale en TODAS las pestañas: el costo validado es del
  // SKU, no de la publicación, y el backend lo cruza contra el filtro de
  // SKUs que cada canal ya aplicaba. Se acumula con la búsqueda y con
  // "Filtrar SKUs" en vez de reemplazarlos.
  const [revisado, setRevisado] = useState(false);
  // ETAPA DEL FLUJO. Absorbe al viejo chip "Solo DROP OFF" como `en_drop` — es
  // la misma pregunta, la misma consulta a Odoo y la misma caché de 30 min — y
  // suma Recibido, 3 de 4 y En FULL, que salen de la foto en memoria.
  // A diferencia del chip, NO persiste al cambiar de pestaña: heredar un filtro
  // de canal en canal deja la pestaña nueva en una lista que nadie pidió.
  const [etapa, setEtapa] = useState<EtapaOmnicanal | null>(null);
  // Conteos del stepper para ESTA pestaña, cuenta y criterio. `null` = todavía
  // no contestó (o falló): el stepper se pinta en esqueleto, no en ceros.
  const [conteos, setConteos] = useState<ConteoCanalFlujo | null>(null);
  // En qué estado venía la foto en la ÚLTIMA respuesta de la lista. Decide si
  // hay columna Flujo y si sus celdas dicen "calentando…".
  const [flujoEstado, setFlujoEstado] = useState<EstadoFotoFlujo>("apagado");
  // La lista pedida con etapa o con costo validado NO se pudo servir. No es
  // "no hay productos": es "no se pudo preguntar", y se dice con su motivo.
  const [errorFiltro, setErrorFiltro] = useState<{ mensaje: string; esperaFoto: boolean } | null>(null);
  // Con la foto apagada (INVENTARIO_FLUJO_ENABLED=false, que es como viene
  // producción) se pintan los dos chips de siempre. Lo decide la respuesta del
  // backend, nunca el cliente: adivinarlo escondería filtros que sí funcionan.
  const [modoFlujo, setModoFlujo] = useState<ModoFlujo>("legado");
  // Contadores de recarga: TODA carga pasa por su efecto. Sin esto, el botón
  // Recargar y los reintentos llamaban a `cargar` por su cuenta y la respuesta
  // vieja podía pisar a la nueva.
  const [recargaLista, setRecargaLista] = useState(0);
  const [recargaConteos, setRecargaConteos] = useState(0);
  const [categorias, setCategorias] = useState<CategoriaWC[]>([]);

  const topRef = useRef<HTMLDivElement>(null);
  // El único temporizador de la lista. Vive en un ref para que el cleanup del
  // efecto lo mate: si no, un reintento encolado revive una petición de un
  // filtro que el usuario ya cambió.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // El total de "Todas" cuando la lista venía SIN filtros, con la llave de la
  // vista que lo produjo. Sin la llave, el número de una pestaña se colaba en
  // otra y el stepper decía una cifra que no era de ahí.
  const totalTodasRef = useRef<{ llave: string; total: number } | null>(null);
  // La hora de la foto que están usando los conteos. Va en un ref y no en las
  // dependencias de `cargar`: compararlas no debe provocar otra carga.
  const conteosGeneradoRef = useRef<string | null>(null);
  const intentosConteos = useRef(0);
  // Rescates de "la foto se está armando": acotados, o la lista y el conteo se
  // llamarían el uno al otro para siempre. `rescatadoCon` guarda la foto con la
  // que YA se rescató: sin esa marca, cada conteo nuevo relanza la lista aunque
  // sea la misma foto de siempre.
  const rescates = useRef(0);
  const rescatadoCon = useRef<string | null>(null);
  // Fallos seguidos de /flujo/canal (que no sean 404). Un tropiezo pasajero en
  // la PRIMERA llamada no puede decidir el modo de toda la sesión.
  const fallosConteos = useRef(0);
  // El temporizador de ese reintento, para que el cleanup del efecto lo mate.
  const timerConteos = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Canal activo + tema ─────────────────────────────────────────────
  const canalActivo = useMemo(
    () => canales.find((c) => c.id === canal),
    [canales, canal],
  );
  const esGeneral = canal === GENERAL;

  // Con qué criterio pagina la lista HOY. El conteo del stepper sigue a estos
  // interruptores y no al contador de la pestaña: en TikTok y Walmart ese
  // contador cuenta todas las filas y "Solo publicados" filtra otra cosa.
  const criterio = criterioDe(esGeneral, soloActivas, soloPublicados);
  // Qué vista produjo un total. En General la cuenta siempre es null.
  const llaveVista = `${canal}|${esGeneral ? "" : cuenta ?? ""}|${criterio}`;

  // «incluye N borradores» (Eduardo, 23-sep). La cifra grande de General SIN
  // filtros sale de la vista «omnicanal» de Woo —todos los estados— y la
  // pestaña General, de la vista «productos», sin borradores: 7,288 contra
  // 3,035 se leía como un error. /api/canales manda los dos totales y la resta
  // es la diferencia. Solo se afirma cuando la cifra grande ES el total del
  // catálogo: General, sin ningún filtro, la lista que se ve salió de esta
  // misma vista sin filtrar (el ref que ya alimenta a «Todo el catálogo») Y
  // esa cifra es exactamente el `total_catalogo` con el que se hizo la resta.
  // La lista se relee en cada carga y /api/canales no: si el catálogo se movió
  // a media sesión, 7,295 − 4,253 ya no cuadra con la pestaña. Mientras no
  // cuadren se calla y se vuelven a pedir los canales (efecto de abajo). Si
  // falta un total no se inventa.
  const borradores = (() => {
    const cat = canalActivo?.total_catalogo;
    const pub = canalActivo?.total_productos;
    if (!esGeneral || typeof cat !== "number" || typeof pub !== "number") return null;
    const sinFiltros = !etapa && !revisado && !busqueda && !skusFiltro
      && !estados.length && !categoria;
    const limpio = totalTodasRef.current;
    if (!sinFiltros || errorFiltro || preparando) return null;
    if (limpio?.llave !== llaveVista || limpio.total !== pag.total) return null;
    if (pag.total !== cat) return null;
    const n = cat - pub;
    return n >= 0 ? n : null;
  })();

  // ── Las DOS lecturas de `filtro_activas` ────────────────────────────
  // Se separan a propósito: pintarlas igual es exactamente el error que el
  // bloque existe para evitar.
  //   · `aplicado: false` → la lista NO está filtrada aunque se haya pedido.
  //     Callarlo hace creer al usuario que está viendo activas cuando ve todo.
  //   · `aplicado: true` + total 0 → el CERO ES LA RESPUESTA (TikTok hoy).
  //     Nunca "no encontrados" ni "preparando": el canal sí contestó.
  const activasNoAplicado = soloActivas && !!filtroActivas && !filtroActivas.aplicado;
  const activasCeroReal =
    soloActivas && !!filtroActivas && filtroActivas.aplicado
    && pag.total === 0 && !cargando;
  // La `nota` del canal cuando el filtro SÍ se aplicó: es la que explica el
  // salto de Amazon (1,668 publicados → 138 activas) y el cero de TikTok.
  const activasNota =
    soloActivas && filtroActivas?.aplicado ? filtroActivas.nota : null;

  const tema: CanalTheme = useMemo(() => {
    const fb = THEME_FALLBACK[canal] ?? THEME_FALLBACK.general;
    if (!canalActivo) return fb;
    return {
      color: canalActivo.color,
      texto: canalActivo.color_texto,
      acento: canalActivo.acento,
      suave: fb.suave ?? hexToRgba(canalActivo.color, 0.1),
    };
  }, [canalActivo, canal]);

  const colorMap = useMemo(
    () => Object.fromEntries(canales.map((c) => [c.id, c.color])),
    [canales],
  );
  const labelMap = useMemo(
    () => Object.fromEntries(canales.map((c) => [c.id, c.label])),
    [canales],
  );
  // id de cuenta → nombre visible ("BEKURA" → "Kubera"). El sello dice «· por
  // San Corpe» y la fila solo trae el id, que nadie reconoce.
  const etiquetasCuenta = useMemo(
    () => Object.fromEntries((canalActivo?.subcuentas ?? []).map((s) => [s.id, s.label])),
    [canalActivo],
  );

  // ── Canales (pestañas y los dos totales de General) ─────────────────
  // Se leen al montar, al Recargar y cuando la cifra grande deja de cuadrar con
  // `total_catalogo`. Si dos lecturas se cruzan gana la última PEDIDA, no la
  // última en llegar. Solo la inicial vacía las pestañas si falla: una relectura
  // fallida deja las que ya había.
  const canalesSeq = useRef(0);
  const leerCanales = useCallback((inicial: boolean) => {
    const n = ++canalesSeq.current;
    listarCanales()
      .then((c) => { if (n === canalesSeq.current) setCanales(c); })
      .catch(() => { if (inicial && n === canalesSeq.current) setCanales([]); });
  }, []);

  useEffect(() => {
    leerCanales(true);
    listarCategorias()
      .then(setCategorias)
      .catch(() => setCategorias([]));
  }, [leerCanales]);

  // La lista se relee en cada carga (paginar, ordenar, Recargar) y trae el
  // total fresco; /api/canales no. Si una carga SIN filtros de General trae un
  // total distinto de `total_catalogo`, el catálogo cambió en la sesión
  // (borradores nuevos, uno publicado…) y se piden otra vez los canales para
  // que pestaña, cifra grande y «incluye N» se muevan juntos. Una vez por cada
  // total distinto: si el backend todavía responde el viejo (caché del
  // índice), no se insiste en bucle y «incluye N» sigue callado.
  const catalogoPedidoPara = useRef<number | null>(null);
  useEffect(() => {
    const cat = canalActivo?.total_catalogo;
    const limpio = totalTodasRef.current;
    if (!esGeneral || typeof cat !== "number") return;
    if (limpio?.llave !== llaveVista || limpio.total !== pag.total) return;
    if (limpio.total === cat || catalogoPedidoPara.current === limpio.total) return;
    catalogoPedidoPara.current = limpio.total;
    leerCanales(false);
  }, [esGeneral, canalActivo, llaveVista, pag.total, leerCanales]);

  // ── Debounce de búsqueda ────────────────────────────────────────────
  useEffect(() => {
    const t = setTimeout(() => {
      setBusqueda(busquedaInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [busquedaInput]);

  useEffect(() => {
    const t = setTimeout(() => {
      setSkusFiltro(skusInput.trim());
      setPage(1);
    }, 500);
    return () => clearTimeout(t);
  }, [skusInput]);

  // ── Carga de productos ──────────────────────────────────────────────
  // UNA SOLA VÍA: esta función solo se invoca desde su efecto. Recargar y los
  // reintentos suben `recargaLista`, que vuelve a dispararlo; el cleanup aborta
  // la petición anterior y mata el temporizador. Así no hay dos cargas vivas
  // pisándose ni respuestas viejas ganándole a la nueva.
  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    setCargando(true);
    // El aviso muere con el filtro que lo produjo. Sin esto, pulsar «Quitar
    // etapa» con el backend caído dejaba la caja ámbar del filtro anterior en
    // pantalla —ya sin ningún botón, porque los dos dependen de `etapa` y
    // `revisado`— diciendo «No se pudo filtrar» cuando ya no hay filtro puesto:
    // el camino de error de abajo reintenta en silencio y nunca la limpia.
    if (!etapa && !revisado) setErrorFiltro(null);
    listarProductos(
      {
        canal,
        page,
        perPage: PER_PAGE,
        search: busqueda || undefined,
        skus: skusFiltro || undefined,
        soloPublicados,
        soloActivas,
        cuenta: esGeneral ? null : cuenta,
        orden,
        estados,
        categoria: esGeneral ? categoria : null,
        revisado,
        etapa: etapa ?? undefined,
        // Omnicanal es la vista de CONTROL: muestra TODO el catálogo, incluidos
        // los drafts. Esconderlos hacía invisible un producto en draft pero VIVO
        // en un canal (TEC-1841-ROS vendió estando oculto; ver v0.29.0).
        vista: "omnicanal",
      },
      ctrl.signal,
    )
      .then((r) => {
        // PRUEBA de que el servidor aplicó la etapa. FastAPI ignora en silencio
        // los parámetros que no conoce, así que un backend anterior a la opción
        // B devolvería el catálogo ENTERO con el segmento encendido. Eso es
        // peor que un error: es una lista que miente.
        if (etapa && !r.filtro_etapa) {
          setProductos([]);
          setPag(PAG_CERO);
          setPreparando(false);
          setErrorFiltro({
            mensaje: "El servidor no aplicó la etapa (versión anterior del backend).",
            esperaFoto: false,
          });
          primeraCarga.current = false;
          return;
        }
        setProductos(r.items);
        setPag(r.paginacion);
        // Viene sólo si se pidió el filtro. Se guarda SIEMPRE (aunque sea
        // `null`) para no arrastrar la nota de una petición anterior.
        setFiltroActivas(r.filtro_activas ?? null);
        setFlujoEstado(r.flujo_estado ?? "apagado");
        setErrorFiltro(null);
        rescates.current = 0;
        rescatadoCon.current = null;

        // El "Todas" del stepper solo se puede afirmar con una lista SIN
        // filtrar, y solo vale para la vista que lo produjo.
        const sinFiltros = !etapa && !revisado && !busqueda && !skusFiltro
          && !estados.length && !(esGeneral && categoria);
        if (sinFiltros) {
          totalTodasRef.current = { llave: llaveVista, total: r.paginacion.total };
        }
        // La lista trae una foto más nueva que la del stepper: se vuelven a
        // pedir los conteos o las cifras contradirían a la paginación.
        if (r.flujo_generado && conteosGeneradoRef.current
            && r.flujo_generado !== conteosGeneradoRef.current) {
          setRecargaConteos((n) => n + 1);
        }

        // Sin búsqueda/filtro y 0 resultados → probablemente el índice todavía
        // se está construyendo (arranque en frío). Reintenta en vez de mostrar
        // "no encontrados". Solo aplica al canal GENERAL (WooCommerce);
        // ML/Amazon leen de MySQL propio y no tienen este arranque en frío.
        // Con etapa o costo validado NO se reintenta: ahí un 0 es la respuesta
        // del filtro, no un índice a medias.
        if (esGeneral && !busqueda && !skusFiltro && !etapa && !revisado
            && r.paginacion.total === 0 && reintentos.current < 45) {
          reintentos.current += 1;
          setPreparando(true);
          timerRef.current = setTimeout(() => setRecargaLista((n) => n + 1), 1000);
          return;
        }
        reintentos.current = 0;
        setPreparando(false);
        primeraCarga.current = false;
      })
      .catch((exc) => {
        if (exc?.name === "AbortError") return;
        // CON FILTRO DEL SISTEMA NO SE REINTENTA. El backend contesta 503 con
        // el motivo (Odoo caído, foto armándose, WordPress sin responder) y
        // machacarlo 45 veces ni lo arregla ni lo explica: la lista se vacía y
        // se dice qué pasó, con el botón para quitar el filtro.
        if (etapa || revisado) {
          const err = exc as { status?: number; detail?: string };
          setProductos([]);
          setPag(PAG_CERO);
          setPreparando(false);
          setErrorFiltro({
            mensaje: mensajeDeError(exc, `No se pudo aplicar el filtro (${err?.status ?? "red"}).`),
            esperaFoto: err?.status === 503 && /armando/i.test(err?.detail ?? ""),
          });
          primeraCarga.current = false;
          return;
        }
        // El backend puede tardar en levantarse (deploy/reinicio) y rechazar la
        // conexión: reintentamos igual que con 0 resultados, en vez de dejar la
        // pantalla en "no encontrados" por un error de red silencioso.
        if (reintentos.current < 45) {
          reintentos.current += 1;
          setPreparando(true);
          timerRef.current = setTimeout(() => setRecargaLista((n) => n + 1), 1000);
        } else {
          primeraCarga.current = false;
        }
      })
      // Tras un abort la petición nueva ya encendió `cargando`: apagarlo aquí
      // dejaría la rejilla en "no encontrados" mientras la buena viaja.
      .finally(() => { if (!ctrl.signal.aborted) setCargando(false); });
    return () => {
      ctrl.abort();
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    };
    // `soloActivas` va aquí SÍ O SÍ: sin él, encender el chip no vuelve a
    // pedir y la rejilla se queda igual — se ve como que el filtro no sirve.
  }, [canal, page, busqueda, skusFiltro, soloPublicados, soloActivas, cuenta, esGeneral, orden, estados, categoria, revisado, etapa, llaveVista, recargaLista]);

  useEffect(() => cargar(), [cargar]);

  // ── Conteos del stepper ─────────────────────────────────────────────
  // Cada vista empieza con su propia cuota de reintentos: si no, entrar a la
  // cuarta pestaña heredaría las vueltas gastadas en la primera.
  useEffect(() => {
    intentosConteos.current = 0;
    fallosConteos.current = 0;
  }, [canal, cuenta, criterio]);

  // Solo memoria en el backend (la misma foto que los sellos), así que se puede
  // pedir en cada cambio de pestaña, cuenta o criterio.
  useEffect(() => {
    const ctrl = new AbortController();
    flujoCanal({ canal, cuenta: esGeneral ? null : cuenta, criterio }, ctrl.signal)
      .then((r) => {
        setConteos(r);
        conteosGeneradoRef.current = r.generado;
        fallosConteos.current = 0;
        // El MODO lo decide el backend: con la foto apagada no hay camino que
        // pintar, pero DROP y costo validado siguen sirviendo.
        setModoFlujo(r.estado === "apagado" ? "legado" : "stepper");
      })
      .catch((exc) => {
        if ((exc as { name?: string })?.name === "AbortError") return;
        setConteos(null);
        conteosGeneradoRef.current = null;
        // 404 = backend anterior a la opción B (la ruta no existe y cae en la
        // ficha comodín). No hay nada que esperar: se baja a modo legado.
        if (exc instanceof ApiError && exc.status === 404) {
          setModoFlujo("legado");
          return;
        }
        // Cualquier OTRO error (502 de un backend reiniciándose, red) deja el
        // modo como estaba, y como arranca en «legado» un solo tropiezo en la
        // primera llamada pintaba los chips viejos toda la sesión: el esqueleto
        // del stepper se volvía inalcanzable. Se reintenta un par de veces, que
        // es lo que tarda un redeploy en aceptar conexiones.
        if (fallosConteos.current < REINTENTOS_ERROR_CONTEOS) {
          fallosConteos.current += 1;
          timerConteos.current = setTimeout(
            () => setRecargaConteos((n) => n + 1), ESPERA_ERROR_CONTEOS_MS);
        }
      });
    return () => {
      ctrl.abort();
      if (timerConteos.current) {
        clearTimeout(timerConteos.current);
        timerConteos.current = null;
      }
    };
  }, [canal, cuenta, esGeneral, criterio, recargaConteos]);

  // La foto se está armando: se vuelve a preguntar, acotado. Se limpia en cada
  // respuesta, así que nunca quedan dos temporizadores pidiendo lo mismo.
  useEffect(() => {
    if (conteos?.estado !== "calentando") return;
    if (intentosConteos.current >= REINTENTOS_CONTEOS) return;
    const t = setTimeout(() => {
      intentosConteos.current += 1;
      setRecargaConteos((n) => n + 1);
    }, ESPERA_CONTEOS_MS);
    return () => clearTimeout(t);
  }, [conteos]);

  // La lista se quedó esperando la foto y el conteo dice que ya está: se
  // recupera SOLA, sin que nadie tenga que volver a dar clic.
  //
  // UNA VEZ POR FOTO, anclado a su hora y no a la identidad del objeto. El
  // rescate se auto-realimentaba: subía también `recargaConteos`, el conteo
  // (memoria, ~50 ms) contestaba mucho antes que la lista (segundos), `conteos`
  // llegaba como otro objeto, el efecto volvía a correr con `errorFiltro` aún
  // puesto y el cleanup de `cargar` abortaba la petición en vuelo para lanzar
  // otra. El tope de 8 no evitaba el ciclo: garantizaba 8 consultas pesadas
  // encadenadas contra WordPress donde el plan promete UNA. Y no hay nada que
  // volver a pedirle al conteo: es justo el que disparó el rescate.
  useEffect(() => {
    if (!errorFiltro?.esperaFoto || !conteos) return;
    if (conteos.estado !== "listo" && conteos.estado !== "vieja") return;
    const marca = `${conteos.generado ?? ""}|${conteos.estado}`;
    if (rescatadoCon.current === marca) return;
    if (rescates.current >= REINTENTOS_CONTEOS) return;
    rescatadoCon.current = marca;
    rescates.current += 1;
    setRecargaLista((n) => n + 1);
  }, [conteos, errorFiltro]);

  // ── Cambio de canal ─────────────────────────────────────────────────
  function seleccionarCanal(nuevo: string) {
    if (nuevo === canal) return;
    setCanal(nuevo);
    setPage(1);
    const info = canales.find((c) => c.id === nuevo);
    // Cuenta por defecto (Mercado Libre → Kubera/BEKURA)
    const def = info?.subcuentas.find((s) => s.es_default)?.id ?? null;
    setCuenta(def);
    // Marketplaces: por defecto mostrar solo publicados
    setSoloPublicados(nuevo !== GENERAL);
    // "Solo activas" arranca apagado en cada pestaña. Además de higiene, es lo
    // que sostiene la invariante de la que depende el reintento de abajo: el
    // chip no existe en General y tampoco puede llegar encendido desde otro
    // canal, así que `esGeneral && soloActivas` es inalcanzable.
    setSoloActivas(false);
    setFiltroActivas(null);
    // Reiniciar filtros que dependen del canal
    setCategoria(null);
    setEstados([]);
    setRevisado(false);
    // La etapa se REINICIA, igual que el costo validado. El viejo chip DROP OFF
    // sí persistía, sin ninguna razón escrita, y arrastrarlo llevaría por un
    // clic de pestaña a una consulta de General por ids que nadie pidió.
    setEtapa(null);
    setErrorFiltro(null);
    // Los conteos son de la pestaña anterior: se borran para que el stepper
    // pinte esqueleto en vez de las cifras del canal que se acaba de dejar.
    setConteos(null);
    conteosGeneradoRef.current = null;
    rescates.current = 0;
    rescatadoCon.current = null;
    fallosConteos.current = 0;
    setOrden("reciente");
    // Buscador y "Filtrar SKUs": cada pestaña empieza limpia (evita que un
    // filtro de un canal se reaplique sin querer al cambiar a otro).
    setBusquedaInput("");
    setBusqueda("");
    setSkusInput("");
    setSkusFiltro("");
    // Nueva pestaña: trátala como "primera carga" hasta que responda.
    primeraCarga.current = true;
    reintentos.current = 0;
  }

  function irPagina(p: number) {
    setPage(p);
    topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="min-h-screen" style={variablesTema(tema)}>
      <AppNavbar />

      <main className="mx-auto max-w-[1600px] px-4 pb-16 pt-6 sm:px-6">
        {/* Banner del canal activo (cambia de color) */}
        <div
          ref={topRef}
          className="relative overflow-hidden rounded-3xl p-6 shadow-card transition-colors duration-300"
          style={{
            background: `linear-gradient(120deg, ${tema.color} 0%, ${hexToRgba(
              tema.acento,
              0.92,
            )} 100%)`,
            color: tema.texto,
          }}
        >
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
                Centro Omnicanal · WooCommerce
              </div>
              <h1 className="mt-1 text-3xl font-extrabold tracking-tight">
                {canalActivo?.label ?? "General"}
              </h1>
              <p className="mt-1 max-w-xl text-sm opacity-90">
                {canalActivo?.descripcion ??
                  "Todas las publicaciones de tu catálogo."}
              </p>
            </div>
            <div className="text-right">
              {/* Con el filtro caído la cifra grande sería la de la petición
                  anterior: se pone en "—" para que nadie la lea como el total
                  de lo que está viendo (que es nada). */}
              <div className="text-4xl font-black tabular-nums">
                {errorFiltro ? "—" : new Intl.NumberFormat("es-MX").format(pag.total)}
              </div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">
                {esGeneral ? "productos" : "publicaciones"}
              </div>
              {borradores !== null && (
                <div
                  className="mt-0.5 text-xs font-semibold tabular-nums opacity-85"
                  title="Borradores de Crear Productos: cuentan en esta cifra y en «Todo el catálogo», pero no en la pestaña General, que cuenta publicados, pendientes y listos."
                >
                  incluye {new Intl.NumberFormat("es-MX").format(borradores)}{" "}
                  {borradores === 1 ? "borrador" : "borradores"}
                </div>
              )}
            </div>
          </div>
          {/* Decoración */}
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full opacity-20"
            style={{ background: tema.texto }}
          />
        </div>

        {/* Pestañas de marketplace */}
        <div className="mt-6">
          <MarketplaceTabs
            canales={canales}
            activo={canal}
            onSelect={seleccionarCanal}
          />
        </div>

        {/* Sub-cuentas (Mercado Libre) + buscador + filtros */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            {canalActivo?.subcuentas?.length ? (
              <AccountTabs
                subcuentas={canalActivo.subcuentas}
                activa={cuenta}
                color={tema.color}
                textoColor={tema.texto}
                onSelect={(c) => {
                  setCuenta(c);
                  setPage(1);
                }}
              />
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Leyenda de canales (solo en GENERAL, donde están los puntos) */}
            {esGeneral && <ChannelLegend canales={canales} />}

            {/* Toggles de marketplace. "Publicado" y "ACTIVO" no son lo mismo y
                por eso son dos chips, no uno. Ninguno se ofrece en GENERAL: Woo
                es la FUENTE del catálogo y del stock, no un canal de venta, así
                que no tiene estado de publicación que filtrar (el backend
                contestaría `aplicado: false`). */}
            {!esGeneral && (
              <>
                <button
                  onClick={() => {
                    const nuevo = !soloPublicados;
                    setSoloPublicados(nuevo);
                    // Excluyentes: en el backend `solo_activas` MANDA sobre
                    // `solo_publicados` (no se suman con AND), así que dejar
                    // los dos encendidos pintaría un chip que no filtra nada.
                    if (nuevo) setSoloActivas(false);
                    setPage(1);
                  }}
                  className={[
                    "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-semibold transition-colors",
                    soloPublicados
                      ? "border-transparent text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                  ].join(" ")}
                  style={soloPublicados ? { backgroundColor: tema.color, color: tema.texto } : undefined}
                  // "Publicado" NO es "activo", y este filtro cuenta lo primero:
                  // en Mercado Libre deja pasar las PAUSADAS y en Amazon las
                  // DISCOVERABLE, que se ven y no se venden. Para lo segundo
                  // está el chip de al lado.
                  title={
                    "Deja las que existen en el canal: `listing_id` presente y no cerrada.\n"
                    + "INCLUYE pausadas y, en Amazon, las que se ven pero no se pueden "
                    + "comprar (DISCOVERABLE).\n"
                    + "Para quedarte sólo con las que se pueden comprar hoy, usa "
                    + "«Solo activas»."
                  }
                >
                  <Filter size={15} />
                  Solo publicados
                </button>

                <button
                  onClick={() => {
                    const nuevo = !soloActivas;
                    setSoloActivas(nuevo);
                    if (nuevo) setSoloPublicados(false);
                    else setFiltroActivas(null);
                    setPage(1);
                  }}
                  className={[
                    "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-semibold transition-colors",
                    soloActivas
                      ? "border-transparent text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                  ].join(" ")}
                  style={soloActivas ? { backgroundColor: tema.color, color: tema.texto } : undefined}
                  title={
                    "Deja sólo lo que se puede COMPRAR hoy, con el criterio de cada canal.\n"
                    + "En Amazon el corte es grande y es CORRECTO: sus 1,253 DISCOVERABLE "
                    + "se ven en el catálogo y no se pueden comprar (1,668 publicados → "
                    + "138 activas).\n"
                    + "En TikTok da 0, y también es correcto: sus 283 APPROVED están "
                    + "SELLER_DEACTIVATED.\n"
                    + "Manda sobre «Solo publicados»: encender éste apaga aquél."
                  }
                >
                  <ShoppingCart size={15} />
                  Solo activas
                </button>
              </>
            )}

            {/* Buscador */}
            <div className="relative">
              <Search
                size={16}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                value={busquedaInput}
                onChange={(e) => setBusquedaInput(e.target.value)}
                placeholder="SKU o nombre…"
                className="w-64 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none transition-shadow placeholder:text-slate-400 focus:ring-2"
                style={{ outlineColor: tema.acento }}
              />
            </div>

            {/* Filtrar SKUs: multi-término separado por coma */}
            <div className="relative">
              <Layers
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                value={skusInput}
                onChange={(e) => setSkusInput(e.target.value)}
                placeholder="Filtrar SKUs: TEC-0001, ORG-0885, caminadora…"
                title="Términos separados por coma: filtra y busca a la vez (SKU completo, parcial o palabra del nombre)"
                className="w-80 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 font-mono text-xs text-slate-700 outline-none transition-shadow placeholder:font-sans placeholder:text-sm placeholder:text-slate-400 focus:ring-2"
                style={{ outlineColor: tema.acento }}
              />
            </div>

            <button
              onClick={() => {
                // Se suben los contadores en vez de llamar a `cargar`: la carga
                // tiene UNA sola vía, la del efecto, que aborta la anterior.
                // Los canales también: si se publicó un borrador, la cifra
                // grande no se mueve pero la pestaña y «incluye N» sí.
                setRecargaLista((n) => n + 1);
                setRecargaConteos((n) => n + 1);
                leerCanales(false);
              }}
              title="Recargar"
              className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-2 text-slate-500 transition-colors hover:bg-slate-50"
            >
              <RotateCw size={16} className={cargando ? "animate-spin" : ""} />
            </button>
          </div>
        </div>

        {/* Controles: vista mosaico/lista, orden, categoría, filtro de estado */}
        <div className="mt-5">
          <ProductControls
            vista={vista}
            onVista={setVista}
            orden={orden}
            onOrden={(o) => { setOrden(o); setPage(1); }}
            esGeneral={esGeneral}
            categorias={categorias}
            categoria={categoria}
            onCategoria={(c) => { setCategoria(c); setPage(1); }}
            estados={estados}
            onEstados={(e) => { setEstados(e); setPage(1); }}
            revisado={revisado}
            onRevisado={(v) => { setRevisado(v); setPage(1); }}
            etapa={etapa}
            onEtapa={(e) => { setEtapa(e); setPage(1); }}
            conteos={conteos}
            modoFlujo={modoFlujo}
            // El conteo por canal manda; si no lo hay (General, o el conteo
            // caído), sirve el total de la propia lista, pero SOLO si salió de
            // esta misma vista y sin filtros.
            totalTodas={
              conteos?.total
              ?? (totalTodasRef.current?.llave === llaveVista ? totalTodasRef.current.total : null)
            }
            // Las cifras del stepper son del canal y la cuenta: no saben de
            // búsqueda, SKUs, estados ni categoría. Con alguno puesto se apagan
            // en vez de contradecir a la paginación.
            atenuar={!!(busqueda || skusFiltro || estados.length || revisado || (esGeneral && categoria))}
            atenuarCarril={etapa !== null}
            canal={canal}
            color={tema.color}
            textoColor={tema.texto}
          />
        </div>

        {/* Censo del canal: cuántas publicaciones hay y cuántas están ACTIVAS
            con el criterio de ESTE canal. Va aquí, arriba de la lista, y no en
            otra pestaña: es el encabezado de lo que se está mirando. En General
            no aplica —Woo es la fuente del catálogo, no un canal de venta. */}
        {!esGeneral && (
          <div className="mt-4">
            <ResumenPublicacionesCanal canal={canal} cuenta={cuenta} color={tema.color} />
          </div>
        )}

        {/* LECTURA 1 — se pidió el filtro y el canal NO pudo aplicarlo. Lo que
            se ve abajo es el catálogo SIN filtrar: hay que decirlo, o el chip
            encendido miente. */}
        {activasNoAplicado && (
          <div className="mt-4 flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber-500" />
            <span>
              <strong>La lista de abajo NO está filtrada por activas.</strong>{" "}
              Se pidió el filtro y este canal no puede evaluarlo, así que estás
              viendo el catálogo completo.
              {filtroActivas?.nota ? ` ${filtroActivas.nota}` : null}
            </span>
          </div>
        )}

        {/* LECTURA 2 — el filtro SÍ se aplicó: la nota explica la trampa del
            canal (el salto de Amazon, el cero de TikTok, el "puede estar
            activa" de Temu). */}
        {activasNota && (
          <div className="mt-4 flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-xs text-slate-600">
            <Info size={14} className="mt-0.5 shrink-0 text-slate-400" />
            <span>{activasNota}</span>
          </div>
        )}

        {/* EL FILTRO DEL SISTEMA NO SE PUDO APLICAR. No se pinta lista, ni
            paginación, ni "no encontrados": lo de abajo sería el catálogo sin
            filtrar, y con el segmento encendido eso se lee como si el filtro
            hubiera dado eso. El motivo lo escribe el backend (Odoo caído, la
            foto armándose, WordPress sin responder) y aquí solo se muestra,
            con la salida a la mano. */}
        {errorFiltro && (
          <div className="mt-4 flex flex-wrap items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber-500" />
            <span className="min-w-0 flex-1">
              <strong>No se pudo filtrar.</strong> {errorFiltro.mensaje}
              {errorFiltro.esperaFoto && (
                <span className="block text-xs text-amber-700">
                  Se recarga sola en cuanto la foto esté lista.
                </span>
              )}
            </span>
            <span className="flex flex-wrap items-center gap-2">
              {etapa && (
                <button
                  onClick={() => { setEtapa(null); setPage(1); }}
                  className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-800 hover:bg-amber-100"
                >
                  {modoFlujo === "legado" && etapa === "en_drop"
                    ? "Quitar Solo DROP OFF"
                    : "Quitar etapa"}
                </button>
              )}
              {revisado && (
                <button
                  onClick={() => { setRevisado(false); setPage(1); }}
                  className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-800 hover:bg-amber-100"
                >
                  Quitar Costo validado
                </button>
              )}
            </span>
          </div>
        )}

        {/* Paginación superior */}
        {!errorFiltro && (
          <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3">
            <Pagination pag={pag} color={tema.color} textoColor={tema.texto} onPage={irPagina} />
          </div>
        )}

        {/* Productos: mosaico o lista */}
        <div className="mt-5">
          {errorFiltro ? null : activasCeroReal ? (
            // El canal contestó y la respuesta es CERO. Va aquí, en lugar de la
            // rejilla, porque ProductGrid/ProductList dirían "No se encontraron
            // productos · Prueba con otra búsqueda" — que se lee como "no hay
            // nada" o "todavía no carga", y las dos lecturas son falsas.
            <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-slate-300 bg-white py-24 text-center">
              <ShoppingCart size={48} className="text-slate-300" strokeWidth={1.3} />
              <p className="text-base font-semibold text-slate-600">
                0 activas en este canal
              </p>
              <p className="max-w-xl px-6 text-sm text-slate-500">
                No es un error ni una carga a medias: el canal sí contestó la
                pregunta y la respuesta es cero. Hoy no hay ninguna publicación
                que se pueda comprar aquí.
              </p>
              {filtroActivas?.nota && (
                <p className="max-w-xl px-6 text-xs text-slate-400">
                  {filtroActivas.nota}
                </p>
              )}
            </div>
          ) : vista === "mosaico" ? (
            <ProductGrid
              productos={productos}
              canal={canal}
              esGeneral={esGeneral}
              cargando={cargando || (productos.length === 0 && primeraCarga.current)}
              preparando={preparando}
              color={tema.color}
              colorMap={colorMap}
              labelMap={labelMap}
              etiquetasCuenta={etiquetasCuenta}
              onSelect={(p) => setSel(p)}
            />
          ) : (
            <ProductList
              productos={productos}
              esGeneral={esGeneral}
              cargando={cargando || (productos.length === 0 && primeraCarga.current)}
              preparando={preparando}
              color={tema.color}
              colorMap={colorMap}
              labelMap={labelMap}
              onSelect={(p) => setSel(p)}
              flujoVisible={flujoEstado !== "apagado"}
              flujoCalentando={flujoEstado === "calentando"}
              canal={canal}
              etiquetasCuenta={etiquetasCuenta}
              etapaConSkus={!!etapa && !!skusFiltro}
            />
          )}
        </div>

        {/* Paginación inferior */}
        {!errorFiltro && (
          <div className="mt-6 rounded-xl border border-slate-200 bg-white px-4 py-3">
            <Pagination pag={pag} color={tema.color} textoColor={tema.texto} onPage={irPagina} />
          </div>
        )}

        {/* Aviso de canal de ejemplo */}
        {canalActivo && !canalActivo.habilitado && (
          <div className="mt-6 flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <Sparkles size={18} className="shrink-0 text-amber-500" />
            <span>
              <strong>{canalActivo.label}</strong> muestra datos de ejemplo. Cuando
              integres sus credenciales, este canal traerá información real
              automáticamente.
            </span>
          </div>
        )}
      </main>

      {/* Drawer de detalle 360° */}
      <ProductDetailDrawer
        sku={sel?.sku ?? null}
        producto={sel}
        canales={canales}
        onClose={() => setSel(null)}
      />
    </div>
  );
}
