FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 🔥 IMPORTANT: only 1 worker
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8080", "app:app"]
