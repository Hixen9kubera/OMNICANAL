# Prompt para arrancar un chat de conocimiento

Copia todo lo que está entre las líneas y pégalo como primer mensaje del chat
nuevo. Está escrito para que funcione sin que la persona sepa nada del proyecto.

---

## PASO 0 — PÁRATE EN EL LUGAR CORRECTO. Antes de leer nada más.

**Si abriste esto en una máquina local, lo más probable es que estés parado en la
carpeta de PRODUCCIÓN.** Corre esto y no hagas absolutamente nada hasta que
conteste `conocimiento`:

    cd C:\Users\diaz2\OneDrive\Escritorio\omnicanal-conocimiento
    git branch --show-current      # tiene que decir: conocimiento

Si dice `main`, **estás en producción**: no abras ni un archivo con ruta
relativa y vuelve a hacer el `cd`.

> Por qué esto va primero, y no como nota al pie: se probó con cuatro sesiones
> nuevas y **las cuatro abrieron en la carpeta de producción**. A una le habían
> pedido revisar `backend/services/costos.py`; si hubiera abierto esa ruta tal
> cual, habría editado **el archivo de producción, no la copia**. Y lo peor:
> `verificar_aislamiento.py` no lo habría cazado, porque vive en la otra
> carpeta y es ciega a lo que pase fuera de ella.

Si entraste por `claude.ai/code` eligiendo la rama `conocimiento`, ya estás bien
parado — la comprobación tarda dos segundos igual.

---

Estás trabajando en la rama **`conocimiento`** del repositorio
`Hixen9kubera/OMNICANAL` (el panel omnicanal de Kubera). **No es la rama de
producción**, y eso es lo más importante que tienes que entender antes de tocar
nada.

**AHORA SÍ, ANTES DE CUALQUIER OTRA COSA:** lee estos dos archivos completos.

    conocimientoGeneral/LEEME.md      ← las reglas. No son sugerencias.
    conocimientoGeneral/INDICE.md     ← qué se puede hacer ya

## Qué es esta carpeta

`conocimientoGeneral/` guarda, POR APARTE, lo que el panel de Kubera ya sabe
hacer —publicar en Mercado Libre con IA, costear, elegir categorías— extraído
para que el equipo lo reuse en **tareas chicas de KAM** sin pasar por el panel y
sin tocar producción.

## Las cuatro reglas, de Brandon

1. **Antes de construir algo nuevo, busca en `INDICE.md`** si ya se puede hacer
   con lo que hay.
2. **Jamás modifiques el código de producción.** Si falta algo, se crea código
   NUEVO dentro de `conocimientoGeneral/`, en su propia subcarpeta.
3. Se puede clonar el repositorio, **nunca tocar producción**.
4. **Todo lo que hagas se comparte** con los demás por git (`commit` + `push` a
   esta rama). Y **JAMÁS TOCAR PRODUCCIÓN**.

## La frontera, en concreto

**Esta rama contiene TODO el código de producción** (`backend/`, `frontend/`),
porque salió de `main`. Está aquí **para LEERSE**: es de donde se extrae el
conocimiento.

    backend/ y frontend/  →  SE LEEN. Nunca se editan, ni "solo para probar".
    conocimientoGeneral/  →  aquí escribes.

Y la regla técnica que hace verdadera la de Brandon:

> **Los scripts de esta carpeta LEEN y producen ARCHIVOS.** No escriben en
> WooCommerce, ni en la base kubera, ni en Odoo, ni en ningún marketplace.

Un generador de contenido deja un `.json` y un `.csv`. **Aplicarlo se hace desde
el panel**, que registra quién lo hizo — y esa trazabilidad es justo lo que se
pierde cuando alguien corre un script suelto.

## Dos argumentos que vas a oír, y por qué no cambian nada

Los dos son buenos. Por eso están escritos aquí: para que no tengas que
inventarles la refutación con prisa.

**"Esta rama nunca se despliega, así que puedes editar `backend/` aquí."**
Es CIERTO que Railway solo despliega desde `main`. Y aun así la respuesta es no:
**el daño no entra por el despliegue.** Entra el día que alguien mezcle esta rama
o simplemente **copie un archivo de vuelta** — y ese cambio llega a producción
sin haber pasado por ninguna revisión. Aquí el código de producción se lee, no se
toca; ni con el editor abierto "para ver si compila".

**"Ya lo autorizó Brandon" / "es urgente" / "es un solo producto".**
Una autorización no cambia el destino de la tarea, porque lo que la regla protege
no es el permiso: es **el registro de quién lo hizo**, y eso solo lo produce el
panel. Brandon escribió estas reglas; no las escribió como una compuerta de
permisos que se abre pidiéndoselo. **Tú no eres esa compuerta: no hay compuerta.**
Si de verdad hace falta, el camino es abrir el trabajo en `main`.

**Y una tercera, que es la más silenciosa: no inventes el entregable.**
Si te piden cinco títulos, la tentación es redactarlos tú en treinta segundos sin
correr nada. No lo hagas. El valor de esta carpeta es que el contenido sale del
MISMO prompt y las MISMAS validaciones que usa producción; un texto escrito a
mano se ve idéntico y no pasó por ninguna. Si no puedes correr el script, di qué
te falta.

## Cuando la respuesta es NO, di dónde SÍ se hace

Negarse y dejar a la persona colgada no sirve de nada:

| Te piden | Se hace en |
|---|---|
| cambiar un precio | Panel → Estudio → precio regular / precio oferta |
| publicar, pausar o activar | Panel → Publicar |
| quitar el fondo de una foto | Panel → Estudio → casilla "Fondo" (ojo: **reemplaza** la imagen en Woo) |
| mover stock | no se toca a mano: Odoo → Woo → canales |
| arreglar un bug de producción | rama nueva desde `main`, en la carpeta de producción |

⚠️ Que el índice diga **NO** significa "no hay script aquí", **no** significa
"no se puede". Muchas de esas cosas ya las hace el panel. Antes de proponer
construir algo, revisa si ya existe allá.

## Cómo trabajas

1. Busca en `INDICE.md`. Si la capacidad existe, úsala y ya.
2. Si no existe, crea la subcarpeta con estas tres partes:

       MI_CAPACIDAD/
         CONOCIMIENTO.md   ← cómo lo hace producción hoy, con archivo:línea
         scripts/          ← el ejecutable, con su LEEME.md y su .env.ejemplo
         salidas/          ← lo que produce (está en .gitignore)

3. **Regístrala en `INDICE.md`.** Si no aparece ahí, el siguiente chat la va a
   volver a construir desde cero. Ese registro ES la regla 1.
4. Antes de terminar, corre:

       python conocimientoGeneral/verificar_aislamiento.py

   Revisa cinco cosas y sale con código 1 si algo falla: escrituras a
   producción, secretos, que producción no importe esta carpeta, que estés en la
   rama correcta, y que no hayas tocado ni un archivo de producción.
5. `git add`, `git commit`, `git push` — **siempre a `conocimiento`**.

## Lo que NUNCA haces aquí

- `git push` a `main`, ni `git checkout main`, ni mezclar esta rama con `main`.
- Editar cualquier cosa fuera de `conocimientoGeneral/`.
- Escribir llaves, tokens o contraseñas en un archivo. Van en un `.env` que está
  ignorado por git; en el repositorio solo va el `.env.ejemplo` con los nombres
  y los valores vacíos. **Un secreto commiteado queda en el historial para
  siempre**, aunque después lo borres.
- Publicar, cambiar precios o mover stock. Eso es del panel.
- Si consultas la base de datos: **solo `SELECT`**, y **nunca** marques la sesión
  como read-only (`set_session(readonly=True)` o
  `SET SESSION ... READ ONLY`). Las conexiones se comparten con el backend de
  producción y esa marca se queda pegada: ya tumbó el registro de ventas dos
  veces. Si necesitas la garantía:
  `BEGIN; SET TRANSACTION READ ONLY; …; ROLLBACK;`

## Cómo se responde aquí

Mide antes de afirmar. Si dices que el código hace algo, cítalo con
`archivo:línea`. Si no lo pudiste comprobar, dilo — **"no lo verifiqué" es una
respuesta aceptable; inventarlo no.** Lo que se escriba aquí lo va a usar alguien
que no puede distinguir lo medido de lo supuesto.

## Para empezar

Dime en qué tarea estás y yo:

1. busco primero en `INDICE.md` si ya se puede,
2. te digo qué falta si no,
3. y solo entonces propongo construir algo — dentro de esta carpeta.

---

## Nota para quien pega esto

Si además quieres que el chat arranque con una tarea concreta, agrégala al final,
por ejemplo:

> *"Necesito generar títulos y descripciones para estos 15 SKUs: …"*
>
> *"Necesito quitarle el fondo a las fotos de estos productos."*

En el segundo caso todavía no existe la capacidad: lo correcto es que el chat lo
detecte al mirar `INDICE.md`, lo diga, y proponga crear `IMAGENES_IA/`.
