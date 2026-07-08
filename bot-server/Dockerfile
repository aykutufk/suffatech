FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
RUN pip install -r requirements.txt
EXPOSE 5003
ENTRYPOINT ["python", "main.py"]
CMD [ "--listen", "0.0.0.0:5003"]
