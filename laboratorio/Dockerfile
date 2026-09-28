# syntax=docker/dockerfile:1
#
# Laboratorio de precios — imagen del servicio `laboratorio-precios` en Railway.
#
# CONTEXTO = RAÍZ DEL REPO (el laboratorio reusa backend/services.*):
#     docker build -f laboratorio/Dockerfile -t laboratorio-precios .
#
# Dos etapas:
#   1. node   → export estático de laboratorio/web (LAB_EXPORT=1 → out/).
#   2. python → backend/ completo + el out/ en /app/web; uvicorn sirve la API
#               de SOLO LECTURA y la web en el mismo origen (la cookie httpOnly
#               viaja sola, sin CORS).
#
# Lo que NO entra a la imagen lo decide /.dockerignore: .env*, datos del
# laboratorio, node_modules, respaldos, reportes. El repo es PÚBLICO y la imagen
# vive en Railway: ni una credencial ni un JSON con costos reales se hornea aquí;
# las credenciales llegan como variables del servicio y los datos en el volumen.

# ── 1. Web ────────────────────────────────────────────────────────────────────
FROM node:20-slim AS fuente-web
WORKDIR /src
COPY laboratorio/ ./laboratorio/
# Solo los manifiestos pasan a la siguiente etapa primero: así `npm ci` queda en
# caché mientras no cambien las dependencias, aunque cambie el código de la web.
RUN mkdir -p laboratorio/web /manifiesto \
 && for f in package.json package-lock.json; do \
      if [ -f "laboratorio/web/$f" ]; then cp "laboratorio/web/$f" /manifiesto/; fi; \
    done

FROM node:20-slim AS web
ENV NEXT_TELEMETRY_DISABLED=1 LAB_EXPORT=1
WORKDIR /web
COPY --from=fuente-web /manifiesto/ ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    elif [ -f package.json ]; then npm install --no-audit --no-fund; \
    else echo "laboratorio/web no existe: se publica solo la API"; fi
COPY --from=fuente-web /src/laboratorio/web/ ./
# Si la web no existe todavía, la API sigue sirviendo: el portal dice "no construida".
RUN if [ -f package.json ]; then npm run build && test -f out/index.html; \
    else mkdir -p out; fi

# ── 2. API + web estática ─────────────────────────────────────────────────────
# 3.12 = la versión de backend/.python-version, la que corre producción.
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/backend

# Dependencias antes que el código (capa en caché mientras no cambien).
COPY backend/requirements.txt ./requirements.txt
COPY backend/sandbox_precios/requirements.txt ./sandbox_precios/requirements.txt
RUN pip install -r sandbox_precios/requirements.txt

COPY backend/ ./
COPY --from=web /web/out/ /app/web/

# LAB_DATOS_DIR=/data: ahí se monta el volumen de Railway (crudo/, cache/,
# ultimo/, snapshots/). Sin volumen, cada deploy empezaría sin historia.
# Se corre como root a propósito: los volúmenes de Railway se montan con dueño
# root y un usuario sin privilegios no podría escribir en /data.
ENV LAB_WEB_DIR=/app/web \
    LAB_DATOS_DIR=/data
RUN mkdir -p /data

EXPOSE 8080
# --proxy-headers: la IP real (freno de intentos de llave) y el esquema https
# llegan del proxy de Railway, el único que alcanza el contenedor.
CMD ["sh", "-c", "exec uvicorn sandbox_precios.api:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
