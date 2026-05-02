FROM python:3.12-slim

WORKDIR /app

# Dependencias primero (capa cacheada)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código fuente
COPY src/ ./src/

# Sin EXPOSE — este contenedor no sirve HTTP
# Railway detectará automáticamente que es un worker

CMD ["python", "-u", "src/bot.py"]
