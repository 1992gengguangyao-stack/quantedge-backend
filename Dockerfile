FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir fastapi==0.115.0 uvicorn[standard]==0.30.0 && \
    pip install --no-cache-dir sqlalchemy==2.0.35 pydantic==2.9.2 pydantic-settings==2.5.2 && \
    pip install --no-cache-dir python-jose[cryptography]==3.3.0 passlib[bcrypt]==1.7.4 bcrypt==4.2.0 && \
    pip install --no-cache-dir email-validator==2.2.0 python-multipart==0.0.12 && \
    pip install --no-cache-dir requests==2.32.3 && \
    pip install --no-cache-dir numpy==1.26.4 && \
    pip install --no-cache-dir pandas==2.2.3 && \
    pip install --no-cache-dir ccxt==4.5.64 && \
    pip install --no-cache-dir web3==7.1.0 eth-account==0.13.1

COPY . .

EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}