FROM python:3.13-slim

WORKDIR /app

COPY tracker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ shared/
COPY tracker/src/ src/
COPY tracker/run.py .

ENV PYTHONPATH=/app/src:/app

CMD ["python", "run.py"]
