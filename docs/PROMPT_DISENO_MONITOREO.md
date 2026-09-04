# Prompt para Claude Design — pestaña MONITOREO

> Pégale a Claude Design todo lo que sigue. Es **contexto y reglas de
> funcionamiento**: el diseño lo decide él.
>
> Todo número de aquí está **medido contra la base el 4-sep-2026**. Si algo se
> ve raro, vuélvelo a medir antes de diseñar encima.

---

## 1 · QUÉ ES ESTA PANTALLA

El panel OMNICANAL de **Kubera** gestiona un catálogo de ~14,700 SKUs en cinco
marketplaces (Mercado Libre, Amazon, TikTok Shop, Temu, Walmart). Lo opera un
equipo de **10 KAMs** más 3 admins.

`/monitoreo` es la pestaña que contesta **quién hizo qué**.

**Requisito principal (Brandon, textual):** *"monitorear qué procesos está
haciendo cada persona"*. Ese es el eje. No es un tablero de KPIs con gente
dentro: es un tablero de PERSONAS con su trabajo dentro.

**Requisito secundario:** encima de eso, **metas semanales** — el equipo tiene
objetivos "week over week" por KAM y por canal, y la pantalla debe dejar ver si
se están cumpliendo. Es una capa, no el reemplazo.

**Quién la lee:** Brandon (dueño), los admins, y **los propios KAMs, que ven su
avance**. Eso último manda sobre el tono: no puede sentirse un panóptico. Un KAM
tiene que poder abrirla y entender qué llevaba hecho, no sentirse vigilado.

---

## 2 · LOS DATOS QUE EXISTEN DE VERDAD

No diseñes nada que no se pueda llenar. Esta tabla es el techo.

### Procesos que hoy quedan registrados con nombre y apellido

Medido sobre los últimos 30 días:

| Proceso | Filas | Con persona | Personas | Qué es |
|---|---|---|---|---|
| `crear` | 2,487 | 59% | 5 | Alta de producto (Alibaba → contenido IA → Woo) |
| `costos` | 353 | 36% | 6 | Validar el costo de un SKU |
| `publicar` | 64 | 97% | 4 | Publicar a un marketplace |
| `competencia` | 59 | 100% | 1 | Raspado de precios de la competencia |

Y **nada más**. No hay registro de "editar precio" ni "cambiar stock": esas
acciones existen en el código como constantes, pero **ningún botón las escribe**.
No las dibujes.

⚠️ **`publicar` empezó a registrarse el 1-sep-2026 a las 20:02.** Antes de esa
fecha no hay una sola fila con persona. La comparación "semana contra semana por
persona" **no tiene semana anterior** hasta el 8-sep. El diseño tiene que
sobrevivir a eso sin verse roto.

### Las metas semanales, y qué se puede medir de cada una

| Meta | ¿Medible? | ¿Se sabe quién? | Número real de esta semana |
|---|---|---|---|
| 10 costos validados de los más vendidos | **Sí** | **Sí** | 33 (semana previa: 20) |
| 10 publicaciones nuevas **MELI** | **Sí** | Sí, con costura* | 185 (previa: 104) |
| 10 publicaciones nuevas **Amazon** | Parcial | Sí | **3** (previa: 6) |
| 10 publicaciones nuevas **TikTok** | **No** | **NO** | 1 |
| 10 publicaciones nuevas **Temu** | **No** | **NO** | **0** |
| 10 publicaciones nuevas **Walmart** | **No** | **NO** | **0** |
| 100% de los más vendidos con publicación activa | Parcial | No — es un estado | 426 de 773 |
| 5 productos TOP categoría MELI | Parcial | No — es resultado de mercado | 1 en top-5 |
| Tasa de devoluciones ≤3% | Parcial | No — es de equipo | 1.33% en unidades |

\* *En Mercado Libre la persona no viaja en la fila de la publicación: se empata
por SKU y tiempo. Funciona, pero es una heurística.*

---

## 3 · 🔴 LA REGLA DE ORO DEL DISEÑO

> **"No lo hizo" y "no lo sabemos" NO se pueden ver iguales.**

TikTok, Temu y Walmart se publican con **scripts de escritorio**, fuera del
panel. El sistema no registra quién los corrió: **0 filas con persona en toda su
historia** (2,048 publicaciones de TikTok, 325 de Temu, 127 de Walmart).

Si la pantalla le pinta un **0 rojo** a un KAM en TikTok, está mintiendo: esa
persona quizá publicó 200 productos, solo que por un camino que no deja rastro.
**El primer KAM que lo note deja de creerle al tablero, y con razón.**

Necesitamos un tercer estado visual, distinto de "cero" y distinto de "cumplido".
Algo que diga *"aquí todavía no medimos"*. Cómo se ve, lo decides tú.

Aplica a: TikTok, Temu y Walmart en todas sus metas, y a cualquier semana
anterior al 1-sep en las metas por persona.

---

## 4 · METAS DE PERSONA vs METAS DE EQUIPO

Se dibujan distinto, porque una admite una cara al lado y la otra no.

**DE PERSONA** — van en la rejilla persona × semana:
costos validados, publicaciones MELI, publicaciones Amazon.

**DE EQUIPO / DE LA OPERACIÓN** — bloque aparte, **sin columna de persona**:

- **Publicaciones activas**: es un estado del catálogo, no un acto. Y hay un
  matiz que importa: de los 347 SKUs que vendieron pero no tienen publicación
  activa, **257 están pausados porque se agotaron** — ML los pausa solo. El
  número accionable son los **90 que sí tienen stock y siguen apagados**.
  Mezclarlos le cobra al KAM un problema de inventario.
- **Top categoría MELI**: nadie "hace" un puesto en un ranking. Se publica, se
  ajusta precio, y semanas después el mercado decide.
- **Devoluciones**: no hay ninguna columna que guarde un empleado.

⚠️ **No existe ninguna tabla que asigne un KAM a un canal o a una categoría.** Sin
ese mapa, "10 por KAM" no tiene denominador, y **5 de los 10 operadores van a
aparecer con cero en todo** simplemente porque no actuaron esta semana. Tenlo en
cuenta al decidir si la lista muestra a todos o solo a quien tuvo actividad.

---

## 5 · EL ERROR, EXPLÍCITO — pedido de Brandon

Cada movimiento que falló guarda **el error textual del canal**. Ejemplo real:

```
proceso  publicar
estado   error
actor    cinthya@kubera.mx
detalle  { canal: "mercado_libre", cuenta: "SANCORFASHION",
           excepcion: "AttributeError: 'PublicarRequest' object has no
                       attribute 'precio_regular'" }
```

Requisito, textual: *"si tuvo algún error junto con el error que se pueda mostrar
con un botón, **el error debe mostrarse explícito**"*.

Es decir: **el mensaje crudo, completo, sin resumir**, detrás de una acción por
renglón. Puede ser larguísimo (hay trazas HTTP de varias líneas) y hay que poder
copiarlo. No lo escondas en un tooltip que no se puede seleccionar.

Y ojo con el equilibrio: **los éxitos son la mayoría** y no deben quedar
sepultados bajo el ruido de los errores.

---

## 6 · LO QUE YA ESTÁ DECIDIDO Y NO SE CAMBIA

Cada una costó un incidente. No son preferencias.

1. **Éxitos SOBRE intentos, siempre los dos números.** `6 / 7`, no `6`. Doce de
   doce no es lo mismo que doce de cuarenta: el primero mide productividad, la
   diferencia entre los dos es lo único que sirve para auditar.
2. **Cada proceso tiene su propio verbo.** Decir "publicado" de un recálculo de
   costo es falso. Publicado · costo validado · producto creado.
3. **Los movimientos automáticos NO aparecen** — fan-out, sondeos, Odoo. No los
   hizo nadie, y meterlos ahoga lo que sí hizo alguien.
4. **Una persona con dos correos se fusiona ARRIBA, no ABAJO.** Thalía tiene dos
   cuentas: en el resumen se suman, en el detalle cada fila conserva el correo
   real con el que se hizo. Fusionar también abajo dejaría la pantalla más limpia
   y la volvería inútil para auditar.
5. **El estado vacío dice la verdad** en lugar de fingir que no hubo actividad:
   el registro empezó el 1-sep-2026 y lo anterior no se puede reconstruir.
6. **No expone costos ni márgenes.** La pestaña la ven los KAMs, no solo los
   admins. Solo autoría.

---

## 7 · SISTEMA DE DISEÑO — lo que existe, y no hay que inventar

### 🔴 NO HAY LIBRERÍA DE GRÁFICAS, Y NO SE PUEDE AGREGAR

`package.json` tiene **cuatro dependencias**: `next`, `react`, `react-dom`,
`lucide-react`. Cero recharts, chart.js, d3 o visx.

**Todas las gráficas del panel son SVG escrito a mano** — barras con ejes y
`<title>` como tooltip (`app/analisis/page.tsx:1406`), sparklines de barras
(`:1394`), sparkline de línea con `<polyline>` (`app/flujo/page.tsx:53`).

Diseña gráficas que se puedan dibujar así: **barras, líneas, sparklines,
progreso contra meta, rejillas de calor**. Nada que necesite una librería.

### Colores de marketplace — exactos, no aproximados

| Canal | Principal | Texto | Acento | Suave |
|---|---|---|---|---|
| Mercado Libre | `#FFE600` | `#2D3277` | `#3483FA` | `#FFFBE0` |
| Amazon | `#FF9900` | `#131A22` | `#232F3E` | `#FFF4E0` |
| TikTok | `#111827` | `#FFFFFF` | `#FE2C55` | `#F1F1F4` |
| Walmart | `#0071DC` | `#FFFFFF` | `#FFC220` | `#E6F1FC` |
| Temu | `#FB7701` | `#FFFFFF` | `#FF5000` | `#FFF0E3` |
| Shein | `#111827` | `#FFFFFF` | `#7C3AED` | `#F1F1F4` |
| **Omnicanal (general)** | `#4F46E5` | `#FFFFFF` | `#818CF8` | `#EEF0FF` |

Se aplican como variables CSS —`--mp-color`, `--mp-text`, `--mp-accent`,
`--mp-soft`— expuestas en Tailwind como `bg-mp`, `text-mp-text`, `bg-mp-accent`,
`bg-mp-soft`.

⚠️ **Dos avisos de contraste que hay que resolver, no ignorar:** el amarillo de
Mercado Libre (`#FFE600`) es ilegible con texto blanco —por eso su texto es azul
marino—, y TikTok y Shein comparten el mismo negro `#111827`: solo los distingue
el acento. Si el diseño los pone juntos en una gráfica, hay que separarlos.

### Identidad del panel

- Fondo `bg-slate-50`. Tarjetas separadas con `ring-1 ring-slate-200`, no con
  sombra. Radios `rounded-lg`.
- **Índigo `#4F46E5` es el color del panel.** Monitoreo hoy usa violeta
  (`violet-600`) y es la excepción, no la regla — puedes conservarla o alinearla.
- **La firma visual del panel**, y conviene mantenerla: los rótulos van en
  `font-mono text-xs uppercase tracking-wider text-slate-500`.
- **Todos los números llevan `tabular-nums`** (se usa en 37 lugares). En una
  rejilla de cifras que cambian, no es cosmético.
- Sombras propias: `shadow-card` y `shadow-card-hover`. Animaciones:
  `animate-fade-in`, `animate-slide-in`.
- Iconos: **solo `lucide-react`**.
- Tipografía: se declara Inter pero **nunca se carga**; en la práctica se ve
  `system-ui`. Si tu diseño depende de Inter, dilo — hay que agregarla.
- **Todo en español.** Fechas cortas (`31-ago`), relativas cuando son recientes
  (`hace 4 h`).

### Restricciones técnicas

- **Next.js App Router + Tailwind.** Componente cliente (`"use client"`).
- Reusar `AppNavbar` y `Pagination`.
- Tiene que verse bien en **laptop de 1366px**, que es donde trabaja el equipo.
  El móvil no es prioridad.
- Datos que llegan por `fetch`: hay estados de **carga**, **error** y **vacío**,
  y los tres se ven seguido. Dibújalos, no los dejes para después.

---

## 8 · QUÉ NECESITO DE TI

Una propuesta de diseño para `/monitoreo` que:

1. Ponga **a la persona en el centro** — qué procesos está corriendo cada quien,
   con qué resultado, en qué canal.
2. Deje ver el **cumplimiento de meta semanal** sin robarle el protagonismo a lo
   anterior.
3. **Distinga con claridad "no lo hizo" de "no lo sabemos"**, que es el punto más
   importante de todo este documento.
4. Dé acceso al **error completo** de cada fallo.
5. Use los colores de canal de arriba y se sienta parte de este panel.
6. Se pueda construir **sin agregar una sola dependencia**.

Si algo de lo que pides no existe en los datos, **dilo en vez de diseñarlo**: es
más barato descubrirlo ahora que cuando alguien abra la pantalla y esté vacía.
