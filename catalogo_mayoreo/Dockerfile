# El módulo del catálogo de mayoreo: una página, sus archivos y un endpoint. Nada del panel entra aquí.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py pedidos.py ofertas.py servicio.py odoo.py ./
COPY web ./web
# El catálogo (catalogo.json + fotos) y los pedidos recibidos viven en el volumen, no en la imagen.
ENV MAYOREO_DATOS=/data PYTHONUNBUFFERED=1
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
