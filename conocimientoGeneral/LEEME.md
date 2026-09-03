# conocimientoGeneral — LÉEME ANTES DE TOCAR NADA

Esta carpeta existe por decisión de **Brandon (2026-09-03)**. Es el lugar donde
vive, POR APARTE, todo lo que OMNICANAL sabe hacer — para que el equipo pueda
tomar esas funciones y resolver tareas chicas de KAM **sin que nada de eso llegue
jamás a producción**.

---

## LAS CUATRO REGLAS, TAL COMO LAS PUSO BRANDON

**1 · Antes de construir, BUSCA AQUÍ.** Cada vez que se pida un proceso nuevo,
lo primero es revisar en `conocimientoGeneral` si ya se puede hacer con lo que
tenemos. → Se busca en [INDICE.md](INDICE.md).

**2 · JAMÁS se modifica el código de producción.** Si hace falta algo nuevo, se
crea código **nuevo dentro de esta carpeta**, en su propia subcarpeta (si se
necesita generación de imágenes con IA, se crea `IMAGENES_IA/` y ahí van los
scripts nuevos). Estos scripts **nunca pasan a producción**: son medio de
creación para solicitudes y tareas chicas de KAM.

**3 · Se puede descargar el repositorio, pero nunca se toca producción.**

**4 · Todo lo que se haga se comparte entre todos los chats**, para nuevos usos.
**Y JAMÁS TOCAR PRODUCCIÓN.**

---

## CÓMO ESAS REGLAS SON CIERTAS AQUÍ, Y NO SOLO UN LETRERO

Una regla que depende de que alguien se acuerde, tarde o temprano se rompe.
Éstas están montadas para que romperlas cueste trabajo:

### Vives en la rama `conocimiento`, no en `main`

**Railway despliega ÚNICAMENTE desde `main`.** Esta carpeta no está en `main`.
Por lo tanto **no puede llegar a producción por construcción** — no por una
regla que alguien pueda olvidar, sino porque el disparador del despliegue no la
mira.

Eso también contesta la regla 4: al vivir en git, todo chat y toda persona la
tiene con un `git fetch`.

### Está en OTRA CARPETA del disco (worktree)

```
C:\Users\diaz2\OneDrive\Escritorio\omnicanal                 ← main · PRODUCCIÓN
C:\Users\diaz2\OneDrive\Escritorio\omnicanal-conocimiento    ← esta rama
```

Las dos comparten el mismo repositorio pero son **carpetas distintas**. Es
deliberado: varios chats trabajan a la vez en la carpeta de producción, y si
alguien hiciera `git checkout conocimiento` ahí, a todos los demás les
cambiarían los archivos bajo los pies.

> **Cómo se entra:** `cd C:\Users\diaz2\OneDrive\Escritorio\omnicanal-conocimiento`
>
> **Nunca** `git checkout conocimiento` dentro de la carpeta de producción.

### Regla técnica que traduce "jamás tocar producción"

> **Los scripts de esta carpeta LEEN y PRODUCEN ARCHIVOS.**
> No escriben en WooCommerce, ni en kubera, ni en Odoo, ni en ningún
> marketplace. Ni una sola escritura, ni siquiera "de prueba".

Un script que genera contenido deja un `.json` y un `.csv`. **Quien decida
aplicarlo, lo aplica desde el panel**, que es donde queda registrado quién lo
hizo. Esa es la diferencia entre una herramienta y un agujero.

Se comprueba con:

```bash
python conocimientoGeneral/verificar_aislamiento.py
```

Sale con código 1 si encuentra una escritura, un secreto, o un import de
producción. **Córrelo antes de dar por terminado cualquier trabajo aquí.**

### Nada de aquí se importa desde producción

Producción no conoce esta carpeta y no debe conocerla. Si algún día un archivo
de `backend/` o `frontend/` importara algo de `conocimientoGeneral`, la
separación se acabó — y el verificador lo caza.

---

## ⚠️ EL REPOSITORIO ES PÚBLICO

`Hixen9kubera/OMNICANAL` es un repositorio **público**. Todo lo que se empuje a
esta rama queda visible para cualquiera.

**Nunca escribas aquí:**

- llaves, tokens, contraseñas, DSN, `client_secret` — usa `.env` propios de cada
  subcarpeta, con su `.env.ejemplo` de valores vacíos, y **el `.env` real nunca
  se sube**;
- costos de compra, márgenes, precios de proveedor;
- datos de clientes (nombres, direcciones, teléfonos, guías).

Conocimiento de **cómo funciona el sistema**: sí.
**Cuánto ganamos y con quién**: no.

---

## CÓMO SE AGREGA UNA CAPACIDAD NUEVA

1. **Primero busca en [INDICE.md](INDICE.md).** Si ya está, úsalo. La regla 1
   existe porque volver a construir lo que ya existe es el desperdicio más caro.
2. Si no está, crea **una subcarpeta con nombre en MAYÚSCULAS** que diga qué
   hace: `IMAGENES_IA/`, `ML_PUBLICACIONES_IA/`, `TRADUCCIONES/`.
3. Dentro, siempre estas tres cosas:

   ```
   MI_CAPACIDAD/
     CONOCIMIENTO.md     ← cómo lo hace producción hoy, con archivo:línea
     scripts/            ← el ejecutable, con su LEEME.md y su .env.ejemplo
     salidas/            ← lo que produce. Va en .gitignore
   ```

4. **Registra la capacidad en [INDICE.md](INDICE.md).** Sin eso, la regla 1 no
   funciona: el siguiente chat no va a encontrarlo y lo va a volver a hacer.
5. Corre `verificar_aislamiento.py`.
6. `git commit` **en la rama `conocimiento`**, y `git push`. Eso es lo que
   cumple la regla 4.

---

## LO QUE ESTA CARPETA NO ES

- **No es un respaldo de producción.** Es una COPIA DE CONSULTA. Si algo aquí no
  coincide con `main`, **gana `main`** y hay que re-extraer. Cada documento dice
  de qué fecha y de qué commit se sacó, justo para eso.
- **No es un lugar para arreglar bugs.** Si encuentras uno en producción, se
  arregla en `main` por el camino normal. Aquí solo se documenta.
- **No es un atajo para publicar.** Nada de aquí publica, cambia precios ni
  mueve stock. Para eso está el panel, que además registra quién lo hizo.

---

## SI ALGO DE AQUÍ RESULTA TAN ÚTIL QUE DEBERÍA SER DEL PANEL

Pasa, y es buena señal. **No se mueve el archivo.** Se abre el trabajo normal en
`main`: se escribe la versión de producción con su router, sus permisos en
`core/rbac.py`, su bitácora de quién lo hizo y su entrada en el README.

Lo de aquí se queda como estaba: es el borrador que sirvió para entender el
problema, no el producto.
