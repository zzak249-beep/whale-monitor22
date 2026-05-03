FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# -u → stdout sin buffer (logs visibles en Railway en tiempo real)
# Sin EXPOSE → Railway lo trata como worker, sin healthcheck HTTP
CMD ["python", "-u", "src/bot.py"]
