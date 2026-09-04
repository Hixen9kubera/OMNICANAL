r"""
crear_usuarios.py — Da de alta al equipo en el panel.

CÓMO FUNCIONA LA IDENTIDAD (y por qué son dos pasos)
----------------------------------------------------
  auth.users      → Supabase Auth. Guarda el correo y la CONTRASEÑA.
                    Nosotros nunca la vemos, igual que WordPress con los
                    usuarios de WooCommerce.
  core.usuarios   → El PERFIL: nombre, correo y ROL. Atada a auth.users por
                    `id` con ON DELETE CASCADE, así que al borrar un usuario
                    sus permisos se van con él.

Por eso `core.usuarios` no tiene columna de contraseña: nunca debió tenerla.

LOS ROLES
---------
La tabla tiene un CHECK que solo admite 'admin', 'operador' y 'lectura'. El rol
que el equipo llama **KAM** se guarda como `operador` y el panel lo MUESTRA como
"KAM" — así no hay que alterar el esquema del equipo de migración.

  admin     Eduardo, José, Brandon. Todas las pestañas.
  operador  (KAM) Análisis, Productos, Omnicanal, Crear Productos, Costos y
            Competencia. Sin Operaciones, Migración ni Facturas.

LA CONTRASEÑA
-------------
Se genera una temporal por persona y se imprime UNA sola vez. No se guarda en
ningún lado: si se pierde, se restablece desde el panel de Supabase. Cada quien
debería cambiarla al primer ingreso.

CÓMO SE CORRE (Brandon, con la llave de Railway)
------------------------------------------------
La SERVICE_ROLE_KEY es la llave maestra de Supabase: se salta las políticas de
seguridad de fila. Por eso NO va en el repo ni en un chat — se toma de Railway
(BackendOmnicanal → Variables → SUPABASE_SERVICE_ROLE_KEY) y se pasa por entorno:

    cd backend
    $env:SUPABASE_URL="https://tukwcvsitthplhswsblt.supabase.co"
    $env:SUPABASE_SERVICE_ROLE_KEY="<la de Railway>"
    .\.venv\Scripts\python.exe -m scripts.crear_usuarios            # simulación
    .\.venv\Scripts\python.exe -m scripts.crear_usuarios --aplicar  # de verdad

Es IDEMPOTENTE: a quien ya exista solo se le corrige el rol, no se le cambia la
contraseña ni se duplica.

⚠️ OJO CON *CUÁL* LLAVE (el error del 4-sep-2026)
--------------------------------------------------
Hay DOS proyectos de Supabase y sus llaves se parecen:

    tukwcvsitthplhswsblt   kubera     ← el panel: auth.users y core.usuarios
    xaxbkijcxzvrwyrqnjzi   analytics  ← otra cosa; es la que trae el .env local

Con la llave de analytics y la URL de kubera, la API contesta
`401 Invalid API key`. El mensaje es cierto y engañoso a la vez: suena a llave
vencida o mal pegada, y lo que pasa es que es de otra base. Por eso el script
ahora lo comprueba solo, leyendo el `ref` que el JWT lleva firmado dentro, antes
de tocar la red.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import string
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import httpx

# (nombre, correo, rol en la base)
EQUIPO: tuple[tuple[str, str, str], ...] = (
    ("Brandon Grajales",  "brandon@kubera.mx",           "admin"),
    ("Eduardo",           "eduardo@kubera.mx",           "admin"),
    ("José",              "jose@kubera.mx",              "admin"),
    ("Alejandro",         "alejandro@kubera.mx",         "operador"),
    ("Andrea Pardo",      "andrea.pardo@kubera.mx",      "operador"),
    ("Cinthya",           "cinthya@kubera.mx",           "operador"),
    ("Denisse Jaimes",    "denisse.jaimes@kubera.mx",    "operador"),
    # Alta pedida por Brandon el 4-sep-2026. Entra como KAM, que es lo que son
    # todos los no-admin; si debe ver Operaciones o Migración, se cambia aquí a
    # 'admin' y se vuelve a correr — el script corrige el rol sin duplicar.
    ("Fernando Ernesto",  "fernando@kubera.mx",          "operador"),
    ("Gabriela Ramírez",  "gabriela.ramirez@kubera.mx",  "operador"),
    ("Haim",              "haim@kubera.mx",              "operador"),
    ("Nancy Cruz",        "nancy.cruz@kubera.mx",        "operador"),
    # Thalía Saavedra tiene DOS cuentas y entra con cualquiera de las dos
    # (Brandon, 5-ago). En Google Workspace una figura como "Zavedra" y la otra
    # como "Saavedra"; aquí van con el apellido correcto y se distinguen por la
    # etiqueta. OJO: siendo la misma persona, la bitácora no puede decir con
    # cuál de las dos trabajó.
    ("Thalía Saavedra",   "thalias@kubera.mx",           "operador"),
    ("Thalía Saavedra (San Corpe)", "sancorpethalia@kubera.mx", "operador"),
    ("Valeria",           "valeria@kubera.mx",           "operador"),
)

ETIQUETA = {"admin": "Admin", "operador": "KAM", "lectura": "Lectura"}


def contrasena() -> str:
    """
    La contraseña con la que nace cada cuenta.

    Si se define `CLAVE_TEMPORAL`, TODOS nacen con la misma — es lo pedido para
    el arranque, porque repartir once contraseñas distintas por chat es peor que
    una sola que se cambia enseguida.

    Sin esa variable, cada quien recibe una aleatoria de 16 caracteres.

    NO se escribe ninguna contraseña en el código: este repositorio es PÚBLICO.
    Se toma del entorno y no se guarda en ningún lado.

    ⚠️ Una clave compartida es temporal por definición: mientras exista,
    cualquiera que la conozca puede entrar como cualquiera, y la bitácora de
    auditoría atribuiría la acción a la persona equivocada. Hay que forzar el
    cambio en el primer ingreso.
    """
    fija = (os.environ.get("CLAVE_TEMPORAL") or "").strip()
    if fija:
        return fija
    alfabeto = (string.ascii_letters + string.digits + "!@#$%*?")
    # Se quitan los caracteres que se confunden al dictarla en voz alta.
    alfabeto = alfabeto.replace("l", "").replace("I", "").replace("O", "").replace("0", "")
    return "".join(secrets.choice(alfabeto) for _ in range(16))


def _proyecto_de_url(url: str) -> str:
    """`https://tukwcvsi....supabase.co` -> `tukwcvsi...`. Cadena vacia si no lo es."""
    host = (url or "").strip().split("//", 1)[-1].split("/", 1)[0].lower()
    return host.split(".", 1)[0] if host.endswith(".supabase.co") else ""


def _sello_de_llave(llave: str) -> tuple[str, str]:
    """
    A qué PROYECTO pertenece la llave y con qué ROL, leído de ella misma.

    Un JWT de Supabase no es opaco: su carga lleva `ref` (el proyecto) y `role`
    (`anon` o `service_role`). O sea que se puede saber si es la llave
    equivocada SIN preguntarle nada a la red — y sin imprimir el secreto.

    Devuelve ('', '') si no se puede leer, para no convertir una llave con
    formato raro en un bloqueo: de eso ya se encarga el 401.
    """
    try:
        carga = (llave or "").strip().split(".")[1]
        carga += "=" * (-len(carga) % 4)
        d = json.loads(base64.urlsafe_b64decode(carga))
        return str(d.get("ref") or "").lower(), str(d.get("role") or "").lower()
    except Exception:  # noqa: BLE001
        return "", ""


def sql_perfiles(perfiles: list[tuple[str, str, str, str]]) -> str:
    """El INSERT que deja a cada quien con su rol. Idempotente."""
    filas = ",\n       ".join(
        "('{}'::uuid, '{}', '{}'::citext, '{}')".format(
            uid, nombre.replace("'", "''"), correo, rol)
        for uid, nombre, correo, rol in perfiles)
    return (
        "insert into core.usuarios (id, nombre, email, rol, activo)\n"
        "select id, nombre, email, rol, true from (values\n"
        f"       {filas}\n"
        ") as x(id, nombre, email, rol)\n"
        "on conflict (id) do update set nombre = excluded.nombre,\n"
        "    email = excluded.email, rol = excluded.rol, activo = true;"
    )


def guardar_perfiles(perfiles: list[tuple[str, str, str, str]]) -> None:
    """
    Escribe el PERFIL y el ROL en core.usuarios.

    POR QUÉ NO SE USA LA API REST (aprendido en carne propia, 4-ago-2026)
    --------------------------------------------------------------------
    PostgREST solo expone los esquemas `public` y `graphql_public`; una escritura
    a `core.usuarios` responde PGRST106 y falla EN SILENCIO. Exponer `core` sería
    abrirle a la API pública el esquema del equipo de migración — no se hace.
    Por eso el perfil se escribe por conexión DIRECTA a Postgres.

    Sin cadena de conexión no se inventa nada: se imprime el SQL para pegarlo en
    el editor de Supabase. Un usuario sin fila aquí NO queda suelto — `identidad.
    _perfil_en_kubera` le da el rol mínimo (`lectura`), nunca admin.
    """
    if not perfiles:
        return
    sql = sql_perfiles(perfiles)
    dsn = (os.environ.get("KUBERA_DB_URL") or os.environ.get("SUPABASE_DB_URL") or "").strip()
    if dsn:
        try:
            import psycopg2
            with psycopg2.connect(dsn, connect_timeout=15) as cx:
                with cx.cursor() as cur:
                    cur.execute(sql)
            print(f"\nPerfiles y roles guardados en core.usuarios: {len(perfiles)}")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"\nNo se pudo escribir core.usuarios ({type(exc).__name__}: {exc}).")

    print("\n" + "=" * 74)
    print("FALTA EL PERFIL Y EL ROL — pega esto en el editor SQL de Supabase")
    print("(sin esta fila, cada quien entra con el rol MÍNIMO: solo lectura)")
    print("=" * 74)
    print(sql)


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    llave = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not url or not llave:
        print("Faltan SUPABASE_URL y/o SUPABASE_SERVICE_ROLE_KEY.")
        print("La segunda está en Railway → BackendOmnicanal → Variables.")
        return 1

    # ── ¿ES LA LLAVE DE ESTE PROYECTO? ───────────────────────────────────
    # Hay DOS proyectos de Supabase y el `.env` de cualquier máquina trae el de
    # ANALYTICS (`xaxbkijc…`), no el de kubera. Pegar esa llave junto a la URL
    # de kubera devuelve `401 Invalid API key`: cierto, pero engañoso — suena a
    # llave vencida o mal copiada, cuando lo que pasa es que es de OTRA base.
    # Costó una corrida el 4-sep-2026, y el mensaje no daba ninguna pista.
    #
    # El propio JWT lo dice, así que se comprueba ANTES de listar al equipo y
    # antes de tocar la red. El secreto no se imprime: solo el proyecto y el rol.
    proyecto = _proyecto_de_url(url)
    ref, rol = _sello_de_llave(llave)
    if proyecto and ref and ref != proyecto:
        print()
        print("  LA LLAVE ES DE OTRO PROYECTO.")
        print()
        print(f"      SUPABASE_URL apunta a   {proyecto}")
        print(f"      y la llave es de        {ref}")
        print()
        print("  `tukwcvsitthplhswsblt` es kubera, la del panel;")
        print("  `xaxbkijcxzvrwyrqnjzi` es analytics. El .env local trae la")
        print("  segunda. La de kubera se toma de Railway, o del panel de")
        print("  Supabase DE ESE PROYECTO → Settings → API keys.")
        print()
        return 1
    if rol and rol != "service_role":
        print()
        print(f"  Esa llave tiene rol '{rol}', y hace falta 'service_role'.")
        print("  La `anon` no puede crear usuarios: la rebota el mismo 401.")
        print()
        return 1

    # El perfil NO se escribe por la API (ver `guardar_perfiles`), sino por
    # conexión directa. Si esa base fuera otra, el INSERT chocaría contra la
    # llave foránea a `auth.users` — quedaría la identidad sin permisos.
    dsn = (os.environ.get("KUBERA_DB_URL") or os.environ.get("SUPABASE_DB_URL") or "")
    if proyecto and dsn and proyecto not in dsn:
        print()
        print(f"  AVISO: la identidad se crearía en `{proyecto}`, pero")
        print("  KUBERA_DB_URL/SUPABASE_DB_URL apuntan a otra base. El perfil")
        print("  fallaría por llave foránea; abajo queda el SQL de rescate.")

    h = {"apikey": llave, "Authorization": f"Bearer {llave}",
         "Content-Type": "application/json"}

    print("=" * 74)
    print(f"EQUIPO A DAR DE ALTA: {len(EQUIPO)}")
    print("=" * 74)
    for nombre, correo, rol in EQUIPO:
        print(f"   {ETIQUETA[rol]:7} {nombre:20} {correo}")

    if not aplicar:
        print("\n(simulación — agrega --aplicar para crearlos)")
        return 0

    with httpx.Client(timeout=45.0) as cx:
        # Quién existe ya (para no duplicar ni pisar contraseñas).
        r = cx.get(f"{url}/auth/v1/admin/users", headers=h, params={"per_page": 200})
        if r.status_code != 200:
            print(f"No se pudo listar usuarios: HTTP {r.status_code} {r.text[:160]}")
            return 1
        existentes = {u["email"].lower(): u["id"]
                      for u in (r.json().get("users") or []) if u.get("email")}
        print(f"\nYa existían en Supabase Auth: {len(existentes)}")

        nuevos: list[tuple[str, str, str]] = []
        perfiles: list[tuple[str, str, str, str]] = []
        print("\n" + "=" * 74)
        for nombre, correo, rol in EQUIPO:
            clave = correo.lower()
            uid = existentes.get(clave)
            if uid:
                print(f"   = {correo:32} ya existía; solo se ajusta el rol")
            else:
                tmp = contrasena()
                c = cx.post(f"{url}/auth/v1/admin/users", headers=h, json={
                    "email": correo, "password": tmp,
                    "email_confirm": True,        # sin correo de confirmación
                    "user_metadata": {"nombre": nombre},
                })
                if c.status_code not in (200, 201):
                    print(f"   ! {correo:32} NO se creó: {c.text[:110]}")
                    continue
                uid = c.json().get("id")
                nuevos.append((nombre, correo, tmp))
                print(f"   + {correo:32} creado")

            if uid:
                perfiles.append((uid, nombre, correo, rol))

    guardar_perfiles(perfiles)

    if nuevos:
        compartida = bool((os.environ.get("CLAVE_TEMPORAL") or "").strip())
        print("\n" + "=" * 74)
        if compartida:
            print(f"{len(nuevos)} cuentas creadas con la MISMA contraseña temporal")
            print("(la de CLAVE_TEMPORAL — no se imprime aquí, ya la tienes)")
            print("\n⚠️  Mientras todos compartan clave, la bitácora no puede")
            print("    distinguir quién hizo qué. Cámbienlas pronto.")
        else:
            print("CONTRASEÑAS TEMPORALES — se muestran UNA sola vez")
            print("Entrégalas por un canal privado.")
            print("=" * 74)
            for _, correo, tmp in nuevos:
                print(f"   {correo:32} {tmp}")
    print("\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
