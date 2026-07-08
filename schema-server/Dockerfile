# Hafif bir Python imajı kullanıyoruz
FROM python:3.11-slim

# Çalışma dizinini ayarla
WORKDIR /app

# Gereksinimleri kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kaynak kodu kopyala
COPY main.py .

# Servisi argumentler ile başlat (Dokümandaki varsayılan değerlerle)
ENTRYPOINT ["python", "main.py"]
CMD ["--schema-dir", "/data/schemas", "--listen", "0.0.0.0:5001"]