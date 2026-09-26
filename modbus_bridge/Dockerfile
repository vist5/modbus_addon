ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19
FROM $BUILD_FROM

# Устанавливаем Python и pip
RUN apk add --no-cache python3 py3-pip

# Создаём рабочую директорию
WORKDIR /app

# Копируем файлы приложения
COPY main.py /app/main.py
COPY templates /app/templates

# Устанавливаем Python-зависимости
RUN pip install --no-cache-dir --break-system-packages \
    pymodbus \
    paho-mqtt \
    pyserial \
    flask

# Копируем run.sh и делаем его исполняемым
COPY run.sh /run.sh
RUN chmod +x /run.sh

# Точка входа
CMD [ "/run.sh" ]
