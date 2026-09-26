#!/usr/bin/with-contenv bashio

# Читаем опции из UI HA
MQTT_HOST=$(bashio::config 'mqtt_host')
MQTT_PORT=$(bashio::config 'mqtt_port')
MODBUS_PORT=$(bashio::config 'modbus_port')
BAUDRATE=$(bashio::config 'baudrate')

# Экспортируем как переменные окружения для Python
export MQTT_HOST
export MQTT_PORT
export MODBUS_PORT
export BAUDRATE

# Логируем параметры
bashio::log.info "Запуск Modbus Bridge..."
bashio::log.info "MQTT: ${MQTT_HOST}:${MQTT_PORT}"
bashio::log.info "Modbus: ${MODBUS_PORT} @ ${BAUDRATE}"

# Создаём папку /data для config.json (если не существует)
mkdir -p /data

# Запускаем приложение
exec python3 /app/main.py