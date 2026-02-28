FROM python:3.12-slim

WORKDIR /app

# Install dependencies at build time (source code is mounted as volume at runtime)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Entrypoint lives outside /app so the volume mount does not shadow it
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5003 5004

ENTRYPOINT ["/entrypoint.sh"]
