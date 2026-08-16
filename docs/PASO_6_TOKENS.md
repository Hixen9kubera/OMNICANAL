# PASO 6 — Los tokens de Mercado Libre

> Medido el 16-ago-2026. **Nada tocado todavía.** Es el bloqueador real del
> retiro del esquema MySQL, y salió del barrido de lectores
> ([BARRIDO_LECTORES.md](BARRIDO_LECTORES.md)).

## Qué hay que mover, exactamente

Barrido exhaustivo del repo: **cuatro consultas**, todas en `meli.py`, más una
lectura de vigilancia. Ni una más.

| Sitio | Qué hace |
|---|---|
| `meli.py:424` | **lee** `app_id`, `client_secret`, `refresh_token` de `ml_tokens_dashboard` |
| `meli.py:434` | **lee** `refresh_token` de `ml_tokens` (respaldo del anterior) |
| `meli.py:486` | **escribe** `access_token` + `refresh_token` en `ml_tokens` |
| `meli.py:492` | **escribe** los mismos en `ml_tokens_dashboard` (best-effort) |
| `meli.py:246` | lee el `access_token` vigente de las dos tablas (`_access_token`) |
| `alertas.py:392` | vigila `MAX(updated_at)` del dashboard — alerta de "tokens rancios" |

Fuera de eso solo hay menciones en comentarios y el censo del espejo.
`tiktok.py` **no** toca estas tablas: reusa la misma `DB_ENCRYPTION_KEY`, que es
otra cosa.

## El riesgo que hace distinto a este paso

**ML rota el `refresh_token` en cada uso.** No es un dato que se copia: es una
credencial que se consume. Eso cambia dos cosas frente a los pasos anteriores:

1. **Dos renovadores se destruyen entre sí.** Si dos procesos refrescan, el
   segundo usa un `refresh_token` ya invalidado y la cuenta se queda sin
   acceso. No es divergencia de datos: es perder la sesión.
2. **La doble escritura sigue siendo segura, la doble RENOVACIÓN no.** Lo que
   hace hoy `meli.py` está bien: refresca UNA vez y guarda el resultado en las
   dos tablas. El peligro no es escribir en dos lados, es *refrescar* en dos.

Por eso este paso no se valida como los otros. La pregunta no es "¿coinciden los
datos?" sino **"¿hay otro renovador vivo?"**.

## El bloqueo P3, y por qué hay que re-verificarlo

El esquema v4 dejó `ops.ml_tokens` creada y **bloqueada**:

> *"Los SECRETOS van en Supabase Vault (vault.secrets); esta tabla guarda solo
> referencias y metadatos. BLOQUEADA por P3: no converger sin acuerdo con el
> dueño de `ml_tokens_dashboard` (sistema externo, refresca ~6 h)."*

Y `meli.py` la llama *"fuente única de verdad — todos los proyectos de ML se
conectan ahí"*.

**Ese supuesto es de julio y hay que medirlo, no heredarlo.** La evidencia de hoy
apunta a que ya no hay sistema externo:

- `MLREgisterDaily` —el candidato obvio, con código que lee y escribe
  `ml_tokens`— **no se despliega desde el 26-may** y su repo se reutilizó para
  otra cosa.
- Las dos tablas tienen **el mismo `updated_at` al segundo** por cuenta, y **la
  misma huella de `refresh_token`**. Es la firma de un único escritor: el
  nuestro.

Pero "no observé a nadie" no es "no hay nadie" — es la lección de toda esta
migración. De ahí el verificador.

## El verificador: `verificar_tokens_ml.py`

Solo lectura, y **nunca imprime un token ni un `client_secret`**: fechas,
longitudes y una huella de 8 hex que solo sirve para ver si el valor CAMBIÓ.

Usa dos señales independientes:

| Señal | Qué delata |
|---|---|
| `dashboard.updated_at − ml_tokens.updated_at` | si el dashboard se mueve solo, hay escritor externo |
| **huella del `refresh_token` en las dos tablas** | ML lo rota en cada uso: huellas distintas = alguien renovó por su cuenta |

La segunda vale más, porque **no depende del reloj** ni de que las dos
escrituras hayan sido casi simultáneas.

Y acumula: cada corrida se guarda en un registro local y reporta la racha.

**El criterio para desbloquear P3** — y el script lo exige explícitamente:

> Cero divergencias durante varios días **que incluyan renovaciones reales por
> cuenta**. Sin renovaciones, la racha limpia no prueba nada: nadie escribió, ni
> nosotros ni un tercero. El script lo dice en vez de dar un verde vacío.

Línea base del 16-ago: las dos cuentas con Δ = 0 s y huellas idénticas.

## El plan, cuando el verificador dé el visto

1. **Migración**: llenar `vault.secrets` (hoy tiene **0 secretos**; la extensión
   `supabase_vault` está instalada y el esquema existe) y `ops.ml_tokens` con
   las referencias. `ops.ml_tokens` hoy tiene **0 filas**.
2. **Gemela** `tokens_read.py` con las cinco operaciones, detrás de flag apagado.
3. **Doble ESCRITURA** (que es segura) unos días: se refresca una vez y se
   guarda en MySQL y en kubera. La doble renovación nunca ocurre.
4. **Cambiar la lectura** a kubera. Aquí sí hay corte: es la credencial viva.
5. Recién entonces MySQL deja de importar para ML.

## Las pruebas

Contra el **sandbox**, con el molde de `probar_corte_costing.py` (stub de MySQL
+ guardia triple de ref):

| Prueba | Qué demuestra |
|---|---|
| T1 | la gemela devuelve el mismo `access_token` que la ruta MySQL |
| T2 | **kubera caída al leer** → NO se inventa un token vacío; propaga |
| T3 | un 401 dispara UNA sola renovación, no dos |
| T4 | tras renovar, el valor nuevo queda en los dos lados y coincide |
| T5 | el `client_secret` **nunca** aparece en logs ni en la salida |
| T6 | con `ops.ml_tokens` vacía, la lectura NO cae a "sin token" en silencio |

T3 es la que importa: es el modo de fallo propio de una credencial rotatoria.

## Lo que NO se hace en este paso

`ml_tokens_dashboard` tiene `app_id` y `client_secret` — credenciales de la app
de ML, no tokens de sesión. Van a Vault también, pero **el `client_secret` de ML
está expuesto en el repo externo `publicador`** (pendiente #9 de CLAUDE.md, sin
rotar). Moverlo a Vault sin rotarlo mueve el secreto de lugar sin dejar de estar
comprometido. **La rotación es una tarea aparte y anterior**, y no es de
migración.
