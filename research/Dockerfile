FROM python:3.10-slim

WORKDIR /app

COPY requirements_ds.txt .
RUN pip install --no-cache-dir -r requirements_ds.txt

EXPOSE 8888

CMD ["jupyter", "notebook", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root"]
