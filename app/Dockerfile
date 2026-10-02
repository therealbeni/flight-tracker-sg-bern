FROM python:3.12-slim

WORKDIR /app

COPY app/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY shared/ shared/
COPY app/ .
RUN chmod +x entrypoint.sh

# The web app only reads its files and talks to the database: no need for root.
RUN useradd --system --no-create-home app
USER app

EXPOSE 8000

CMD ["./entrypoint.sh"]
