# Arneses de verificación — qué se corre cada mañana y qué NO

> Lista viva. La lee una persona **y** el agente de cowork que corre los
> chequeos diarios. Al final hay un manifiesto en JSON para que el agente no
> tenga que interpretar prosa.
>
> **Regla de oro: sumar o quitar un arnés es un commit a este archivo, no un
> cambio en el agente.**

## Por qué existe este archivo

En `backend/scripts/` hay diez scripts que se llaman `comparar_*` o
`vigilar_*`. **Siete de ellos ya no miden nada** y correrlos daría alarma todos
los días. La razón está en una sola frase:

> Desde el 13-ago-2026 los espejos a MySQL están apagados. MySQL está congelado
> **a propósito**. Todo arnés que compare "MySQL como fuente de verdad contra
> kubera como espejo" mide, a partir de esa fecha, una divergencia que **crece
> sola cada día**.

Medido el 14-ago: `costos_finales` llevaba 93.7 h sin escribirse, `pedidos_ml`
36.7 h, `canal_inventario` 37.3 h. Eso no es una falla: es el resultado
esperado del corte.

Meter esos arneses en el chequeo diario no es "ser precavido". Es fabricar una
alarma que suena siempre, y una alarma que siempre suena entrena a la gente a
ignorarla — el mismo motivo por el que `alertas.py` avisa por CAMBIO DE ESTADO
y no por repetición.

---

## ✅ Activos — van al chequeo diario

### `vigilar_congelacion.py` — el latido de kubera

```bash
python backend/scripts/vigilar_congelacion.py
```

El único diseñado **para después** del corte. No compara contra MySQL (esa
referencia ya no existe): mide que **kubera siga moviéndose** y que los espejos
sigan quietos. Cinco latidos: `turno_sync`, `pedidos`, `costos`, `padron`, y las
tres tablas congeladas.

⚠️ **EL CÓDIGO DE SALIDA NO ALCANZA.** Hoy devuelve `1` de forma rutinaria
porque el latido `costos` marca ALTO cuando nadie ha recalculado un costo en
72 h — y eso pasa cualquier fin de semana. El propio script lo dice: *"normal si
no se usó el panel"*.

El agente tiene que **leer qué latido falló**, no el código:

| Latido | ¿Alarma de verdad? |
|---|---|
| `pedidos` (tope 6 h) | **SÍ.** Ventas paradas o el flujo roto |
| las tres congeladas | **SÍ**, si alguna dice `AÚN ESCRIBIENDO` en vez de `congelada`: algo volvió a escribir MySQL |
| `costos` (tope 72 h) | **NO por sí solo.** Benigno en fines de semana |
| `turno_sync` | **NO.** Sale `n/d` a propósito (`_MIDE_COBERTURA = False`): la métrica que medía resultó inválida |
| `padron` | **NO — ya no puede sonar.** Retirado del veredicto el 16-ago (ver abajo); ahora sale `n/d` informativo |

**`padron` se retiró, y vale la pena saber por qué**: medía `max(created_at)` de
`core.products` —*cuándo entró el último producto nuevo*— y lo reportaba como
*"el ETL de las 06:15 no corrió"*. Medido el 16-ago marcaba ALTO con 58.7 h,
mientras las actas mostraban `core-etl-v2 ok` el 15 y el 16 a las 06:17: **el
cron corrió los dos días**, simplemente no hubo altas. Sin altas, el contador
crece 1:1 con el reloj y cruza el umbral solo — el mismo defecto que invalidó
`turno_sync`.

**No se perdió vigilancia**: `alertas._revisar_actas` ya vigila ESE MISMO ETL
cada 15 min contra `migration.reconciliation_runs` (`core-etl-v2` está en sus
dominios) y avisa por Slack *"Acta de Maestro (ETL) NO generada hoy"* si el cron
falla. Eso mide lo que el latido decía medir.

Con esto, **los tres arneses quedan sin falsas alarmas conocidas**. Es lo que
hace que valga la pena leer el reporte: si algo suena, es real.

Retiro: nunca mientras kubera sea la fuente de verdad.

### `comparar_stock_watch_foto.py` — el del paso 2

```bash
python backend/scripts/comparar_stock_watch_foto.py
```

Compara la foto del vigilante de inventario en sus dos casas
(`stock_watch_foto` en MySQL contra `ops.stock_watch_photo` en kubera) mientras
dure la doble escritura del PASO 2 ([PLAN_31_TABLAS.md](PLAN_31_TABLAS.md)).

Aquí el código de salida **sí** es fiable: `0` limpio, `1` con diferencias. Sin
falsos positivos conocidos.

Cinco bloques. El que decide es el cuarto: no compara datos, compara **el delta
que se aplicaría con cada foto**. Que los datos coincidan es la hipótesis; que
la decisión coincida es la conclusión. En el incidente de los 964 pedidos, los
datos coincidían.

Un desfase de ≤100 publicaciones en el bloque 5 es normal y el script ya lo
tolera: el espejo del DROP corre en su propio job y va una pasada atrás.

Opción: `--max-atraso-min 90` si el vigilante llegara a correr más espaciado.

**Retiro:** cuando `SUPABASE_READ_STOCK_WATCH=true` lleve días estable y
`stock_watch_foto` se archive. Este arnés existe para autorizar ese encendido;
después no mide nada.

### `comparar_seam_publicar.py` — el del paso 3

```bash
python backend/scripts/comparar_seam_publicar.py
```

Vigila que ninguna publicación reciente se quede sin `listing_id` en kubera, y
—cuando se encienda `SUPABASE_SEAM_PUBLICAR`— que el seam esté escribiendo de
verdad. Código de salida fiable.

Arbitra por RECENCIA en vez de exigir igualdad: un `listing_id` distinto entre
`ml_progress` y `channel.listings` **no es fallo** — es un SKU republicado, y
kubera tiene el vivo. Solo reprueba si el id del publicador es el más nuevo.

⚠️ **Lo que este arnés deliberadamente NO mide: el retraso.** No es medible con
lo que hay — `listing_history` no registra `listing_id`, y
`listings.updated_at` significa "cuándo CAMBIÓ", no "cuándo se enteró". La
primera versión lo calculaba igual e imprimía una mediana de 889 minutos: un
número real que medía otra cosa. Mismo error que invalidó `turno_sync`. Está
escrito en el código para que nadie lo "arregle" reponiéndolo.

Mientras el seam esté apagado, el bloque 3 sale `n/d` — es la línea base, no una
aprobación. **No se repunta ningún lector del grupo 4 hasta que ese bloque
muestre tráfico real por la vía `publicar`.**

### `verificar_tokens_ml.py` — el del paso 6

```bash
python backend/scripts/verificar_tokens_ml.py
```

Contesta una sola pregunta: **¿hay otro proceso renovando los tokens de ML?**
Si lo hay, migrarlos le quita el piso — y como ML **rota el `refresh_token` en
cada uso**, dos renovadores no divergen: se invalidan mutuamente y la cuenta
pierde la sesión.

Solo lectura, y **nunca imprime un token ni el `client_secret`**: fechas,
longitudes y una huella de 8 hex que solo dice si el valor cambió.

Dos señales independientes: el desfase entre los `updated_at` de las dos tablas,
y —la que más vale— **la huella del `refresh_token`**, que no depende del reloj.

⚠️ **Sin renovaciones en la ventana, una racha limpia NO es evidencia**: nadie
escribió, ni nosotros ni un tercero. El script lo dice en vez de dar un verde
vacío. Es el mismo error que un "0 avisos" de un panel que nadie abrió.

**Retiro:** cuando los tokens vivan en kubera y `ml_tokens` sea archivo.

---

## 🔑 La regla que decide si un arnés sobrevive al corte

Esto se aprendió **midiendo**, no razonando: la primera versión de este archivo
daba por muertos a los siete de abajo, y uno resultó estar vivo.

> **Sobrevive el arnés que arbitra contra un tercero VIVO. Muere el que usa
> MySQL como referencia.**

- `comparar_lecturas_core.py` sobrevive porque cuando encuentra una diferencia
  **le pregunta a Woo quién tiene razón**. Corrido el 14-ago: 28 diferencias,
  Woo le dio la razón a kubera en 28 de 28 → `difs_reales=0`, veredicto
  `EQUIVALENTE`, código 0.
- Los otros tres comparan contra MySQL a secas y por eso reprueban: MySQL está
  congelado a propósito y la diferencia crece sola cada día.
- `vigilar_congelacion.py` sobrevive por otra vía: no compara igualdad, mide
  **movimiento**.
- `comparar_stock_watch_foto.py` sobrevive porque sus dos lados están vivos.

**Todo arnés nuevo se diseña con esa regla.** Un arnés que solo sabe comparar
dos tablas caduca el día del corte; uno que sabe a quién preguntarle, no.

## ❌ Retirados — NO meter al chequeo diario

No se borran: son la evidencia de cómo se cerró cada corte. Se archivan en F8
junto con el resto del andamiaje. **Los códigos de salida son medidos, no
supuestos** (y ojo: no son uniformes — `orders` sale con 2, no con 1).

| Script | Medido 14-ago | Por qué queda fuera |
|---|---|---|
| `comparar_costos.py` | no corrido a propósito | **ESCRIBE** acta en `migration.reconciliation_runs` y su cron está retirado. Razón independiente de si pasa |
| `comparar_channel.py` | ídem | ídem |
| `comparar_orders.py` | ídem | ídem |
| `comparar_lecturas_costing.py` | `CON DIFERENCIAS`, exit **1** | Compara contra MySQL congelado. Alarma garantizada, todos los días |
| `comparar_lecturas_channel.py` | `CON DIFERENCIAS`, exit **1** | Ídem |
| `comparar_lecturas_orders.py` | `CON DIFERENCIAS (15)`, exit **2** | Ídem. Las diferencias son el corte: kubera trae 1,702 `completed` de BEKURA contra 1,004 de MySQL — kubera siguió, MySQL no |

### Caso aparte: `comparar_lecturas_core.py`

**Pasa** (`EQUIVALENTE`, exit 0) por la regla de arriba, así que no ensuciaría
el chequeo diario. Aun así **no va al agente**, por dos razones que no son
"falla":

1. Tarda **más de dos minutos**, y la mitad de lo que compara —el lado MySQL—
   ya es peso muerto.
2. Lo único que sigue midiendo de verdad es *kubera contra Woo*, y eso merece un
   arnés propio, sin MySQL en medio.

Queda anotado para F8: **partirlo en dos y quedarse con la mitad viva.** Es
trabajo chico y convierte un arnés que caduca en uno permanente.

**Aparte, y no es de migración:** `comparar_variantes_wpdb.py` verifica que la
ruta rápida de variantes de WordPress devuelva lo mismo que el REST que
sustituye. Sigue siendo válido —WordPress se queda— pero es el arnés de un
cambio de rendimiento puntual, no algo que cambie de día a día. Se corre a mano
si alguien toca `wp_db.variantes_por_padre`.

---

## 🔜 Los que faltan

Cada paso del plan trae el suyo, con el molde de seis pasos que salió del PASO 1
(gemela → copia → comparación → escritor → lector → verificación):

| Paso | Arnés que va a necesitar |
|---|---|
| 3 — publicador (grupo 4) | ~19 lectores partidos por intención. El más grande de todos |
| 4 — cachés | uno por caché, cortos |
| 5 — bitácoras | paridad de eventos, como el que se usó para `crear_logs` |
| 0 — candados de `fanout_log` | **no lleva arnés de datos**: lleva prueba de COMPORTAMIENTO (que el candado diga "no sé" y no "no lo hice" cuando la base falla) |

Cuando nazca uno, se agrega arriba **y** al manifiesto de abajo, en el mismo
commit que lo crea.

---

## Cómo lo corre el agente

- Desde la raíz del repo (`OMNICANAL`); necesita el `.env`.
- Los dos activos son **SOLO LECTURA** sobre producción. Se pueden correr sin
  riesgo y sin avisar a nadie.
- Duración: `comparar_stock_watch_foto` segundos; `vigilar_congelacion` cerca de
  un minuto.
- Lo que ya avisa solo por Slack cada 15 min (`services/alertas.py`) es OTRA
  cosa: actas de los ETLs, silencio de ventas, tokens rancios y pedidos
  duplicados. Ni la foto ni los latidos están ahí — por eso este chequeo diario
  tiene sentido y no duplica nada.

### Manifiesto

```json
{
  "version": "2026-08-16b",
  "cwd": "OMNICANAL",
  "activos": [
    {
      "id": "latido_kubera",
      "cmd": "python backend/scripts/vigilar_congelacion.py",
      "cadencia": "diaria",
      "solo_lectura": true,
      "codigo_salida_confiable": false,
      "leer_salida": true,
      "alarma_si_contiene": ["[ALTO] pedidos", "AÚN ESCRIBIENDO"],
      "ignorar_si_solo": ["[ALTO] costos", "[ n/d] turno_sync", "[ n/d] padron"],
      "nota": "El codigo 1 es rutinario. Decide el latido, no el exit code.",
      "retiro": "nunca mientras kubera sea la fuente de verdad"
    },
    {
      "id": "paso2_foto_vigilante",
      "cmd": "python backend/scripts/comparar_stock_watch_foto.py",
      "cadencia": "diaria",
      "solo_lectura": true,
      "codigo_salida_confiable": true,
      "leer_salida": false,
      "nota": "Autoriza encender SUPABASE_READ_STOCK_WATCH. Verde varios dias seguidos o no se avanza.",
      "retiro": "cuando la lectura pase a kubera y stock_watch_foto se archive"
    },
    {
      "id": "paso3_seam_publicar",
      "cmd": "python backend/scripts/comparar_seam_publicar.py",
      "cadencia": "diaria",
      "solo_lectura": true,
      "codigo_salida_confiable": true,
      "leer_salida": false,
      "nota": "Con el seam apagado el bloque 3 sale n/d: es linea base. No repuntar lectores del grupo 4 hasta ver trafico por la via 'publicar'.",
      "retiro": "cuando los 25 lectores esten repuntados y ml_progress sea archivo"
    },
    {
      "id": "paso6_tokens_ml",
      "cmd": "python backend/scripts/verificar_tokens_ml.py",
      "cadencia": "diaria",
      "solo_lectura": true,
      "codigo_salida_confiable": true,
      "leer_salida": true,
      "nota": "Busca un segundo renovador de tokens de ML. SIN renovaciones en la ventana, una racha limpia no prueba nada - el script lo avisa. Nunca imprime secretos.",
      "retiro": "cuando los tokens vivan en kubera y ml_tokens sea archivo"
    }
  ],
  "retirados": [
    {"script": "comparar_costos.py",           "motivo": "escribe acta; cron retirado"},
    {"script": "comparar_channel.py",          "motivo": "escribe acta; cron retirado"},
    {"script": "comparar_orders.py",           "motivo": "escribe acta; cron retirado"},
    {"script": "comparar_lecturas_costing.py", "motivo": "compara contra MySQL congelado", "medido_exit": 1},
    {"script": "comparar_lecturas_channel.py", "motivo": "compara contra MySQL congelado", "medido_exit": 1},
    {"script": "comparar_lecturas_orders.py",  "motivo": "compara contra MySQL congelado", "medido_exit": 2}
  ],
  "pasa_pero_fuera": [
    {"script": "comparar_lecturas_core.py", "medido_exit": 0, "veredicto": "EQUIVALENTE",
     "motivo": "arbitra contra Woo vivo, pero tarda >2 min y la mitad MySQL es peso muerto",
     "pendiente": "F8: partirlo y quedarse con kubera-vs-Woo"}
  ],
  "fuera_de_migracion": ["comparar_variantes_wpdb.py"]
}
```
