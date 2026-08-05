FROM python:3.11-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY skills ./skills
COPY hard_cases ./hard_cases

# Pre-descarga el modelo de embeddings en tiempo de build, no de request:
# en un container serverless que escala a cero, bajarlo en la primera
# request real de RAG agregaria varios segundos de latencia (y costo de
# GPU-seg) impredecibles al primer usuario de cada cold start.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
