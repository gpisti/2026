FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    python -m nltk.downloader punkt punkt_tab -d /usr/local/nltk_data

ENV NLTK_DATA=/usr/local/nltk_data

COPY src/ ./src/

CMD ["python", "-m", "src.main"]
