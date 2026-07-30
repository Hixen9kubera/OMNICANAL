# Procedimiento de notificación de brechas de datos personales

**Kubera** · Versión 1.0 · 2026-07-30
Responsable del documento: Brandon Díaz (brandon@kubera.mx)

> Este procedimiento existe para que, el día que ocurra un incidente, nadie tenga
> que improvisar. Se activa ante la **sospecha**, no solo ante la confirmación.

---

## 1. Qué cuenta como brecha

Cualquier acceso, pérdida, alteración o divulgación **no autorizada** de datos
personales de compradores. En nuestra operación, los casos realistas son:

| Escenario | Dónde |
|---|---|
| Acceso no autorizado a la base de datos de pedidos | MySQL en Hostinger |
| Descarga masiva de datos por la API del panel | Railway |
| Filtración de credenciales de un marketplace | ML / Amazon / Temu |
| Extracción de la lista de clientes por alguien del equipo | WordPress / panel |
| Robo o pérdida de un equipo con sesión activa | cualquiera |
| Aviso de un tercero (marketplace, investigador, cliente) | externo |

**Ante la duda, se activa.** Un falso positivo cuesta una hora; una brecha no
atendida cuesta mucho más.

---

## 2. Quién hace qué

| Rol | Persona | Responsabilidad |
|---|---|---|
| **Responsable de la decisión** | Brandon Díaz — brandon@kubera.mx | Toma el mando, decide a quién se notifica y aprueba los comunicados |
| **Contacto técnico** | `[DEFINIR]` | Ejecuta la contención: rota llaves, cierra accesos, aísla sistemas |
| **Contacto de privacidad** | `[DEFINIR — sugerido: privacidad@kubera.mx]` | Recibe reportes externos y atiende a los titulares |

Quien detecte algo **avisa de inmediato al responsable de la decisión**, sin
esperar a confirmar ni investigar por su cuenta.

---

## 3. Los pasos, en orden

### Paso 1 — Contener (primeras horas)
Antes de investigar. El objetivo es que deje de crecer:
- Rotar credenciales expuestas (tokens de ML/Amazon/Temu, llaves de la BD, API keys)
- Revocar accesos de las cuentas involucradas
- Si el vector es la API pública, cerrarla o bloquear el origen
- **No borrar evidencia**: los registros son necesarios para el paso 2

### Paso 2 — Evaluar (primeras 24 horas)
Responder por escrito:
- ¿Qué datos se expusieron? (nombre, dirección, teléfono, correo, pedidos)
- ¿De cuántas personas?
- ¿Desde cuándo y hasta cuándo estuvo abierto?
- ¿Hay evidencia de que alguien los usó?
- ¿Provienen de algún marketplace? (define a quién hay que avisar)

### Paso 3 — Notificar

| A quién | Cuándo | Cómo |
|---|---|---|
| **Temu** (si hay datos de sus compradores) | **dentro de 24 h** de confirmarlo | Canal de soporte del Partner Platform + correo al contacto de la integración |
| **Mercado Libre / Amazon** (si aplica) | dentro de 24 h | Canal de soporte de vendedor de cada cuenta |
| **Titulares afectados** | **de forma inmediata**, según LFPDPPP art. 20 | Correo, cuando la brecha afecte de forma significativa sus derechos |
| **INAI** | según valoración legal | Solo si la asesoría legal lo determina |

El aviso al titular debe decir: qué pasó, qué datos suyos se vieron, qué estamos
haciendo, y qué le recomendamos hacer. Sin tecnicismos.

### Paso 4 — Documentar
En la bitácora de incidentes: fecha y hora de detección, qué pasó, alcance,
acciones tomadas, a quién se notificó y cuándo, y estado final.

### Paso 5 — Corregir
Cerrar la causa raíz, no solo el síntoma. Revisar si el mismo hueco existe en
otro sistema. Actualizar este procedimiento con lo aprendido.

---

## 4. Revisión

Se revisa **cada 6 meses** o después de cualquier incidente.
Próxima revisión: **enero 2027**.

---

## Resumen en inglés (para el cuestionario de Temu)

> Kubera maintains a documented data breach notification procedure. Upon
> detection or suspicion of a breach, a designated responsible officer leads
> containment (credential rotation, access revocation), assesses scope within 24
> hours, and notifies affected parties. **Temu is notified within 24 hours** of
> confirming any incident involving data originating from its platform, through
> the Partner Platform support channel. Data subjects are notified immediately
> where the breach materially affects their rights, as required by Mexico's
> Federal Law on Protection of Personal Data Held by Private Parties (LFPDPPP,
> Article 20). All incidents are logged with scope, actions taken, and
> notifications issued. The procedure is reviewed every six months.

---

*Nota: este documento no sustituye asesoría legal. Los plazos y obligaciones
frente al INAI conviene validarlos con un abogado especialista en protección de
datos antes de un incidente real.*
