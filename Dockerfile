FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# FIX: archivos están en la raíz, no en src/
COPY *.py .

# -u → stdout sin buffer (logs visibles en Railway en tiempo real)
CMD ["python", "-u", "bot.py"]
