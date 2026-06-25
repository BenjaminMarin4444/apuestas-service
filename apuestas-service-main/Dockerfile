# Etapa 1: Construcción y preparación de dependencias de Python
FROM python:3.12-slim AS builder
WORKDIR /app

# Copiamos las dependencias oficiales del microservicio Python
COPY requirements.txt .

# Instalamos las librerías en el espacio de usuario para transferirlas limpias
RUN pip install --no-cache-dir --user -r requirements.txt

# Etapa 2: Runtime definitivo, liviano y seguro
FROM python:3.12-slim AS runtime
LABEL maintainer="VidalCasino"
WORKDIR /app

# Copiamos únicamente las dependencias listas de la etapa anterior
COPY --from=builder /root/.local /root/.local
# Copiamos el código fuente de apuestas (la carpeta app/ y sus scripts)
COPY . .

# Configuramos las variables de entorno para Python y uvicorn
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Creamos un usuario no-root por seguridad para cumplir la pauta (Puntaje Destacado)
RUN useradd -m -u 1001 casino-user && chown -R casino-user:casino-user /app
USER casino-user

# El puerto oficial que exige la pauta para apuestas
EXPOSE 8005

# Comando de arranque nativo para FastAPI usando uvicorn apuntando a app/main.py
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]