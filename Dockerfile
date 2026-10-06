FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt tzdata

COPY . .

# A copy of the data folder to seed an empty volume with; the volume itself mounts on /app/data.
RUN cp -r data seed_data && chmod +x docker-entrypoint.sh

CMD ["./docker-entrypoint.sh"]
