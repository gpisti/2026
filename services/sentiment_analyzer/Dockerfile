FROM python:3.11-slim

WORKDIR /app

# Torch CPU-only változat — sokkal kisebb image, GPU nem kell
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY src/ ./src/

CMD ["python", "-m", "src.main"]
