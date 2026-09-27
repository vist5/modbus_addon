import time
import json
import os
import threading
import paho.mqtt.client as mqtt
from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient
from flask import Flask, jsonify, render_template, request, redirect, url_for

app = Flask(__name__)

from werkzeug.middleware.proxy_fix import ProxyFix

# === INGRESS FIX ===
# ProxyFix без x_prefix — HA Ingress использует другой заголовок
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
)


class IngressFix:
    """Middleware: подставляет X-Ingress-Path в Location-заголовки редиректов."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        ingress_path = environ.get("HTTP_X_INGRESS_PATH", "")

        def fixed_start_response(status, headers, exc_info=None):
            if ingress_path:
                new_headers = []
                for k, v in headers:
                    if k.lower() == "location" and v.startswith("/"):
                        v = ingress_path + v
                    new_headers.append((k, v))
                headers = new_headers
            return start_response(status, headers, exc_info)

        return self.wsgi_app(environ, fixed_start_response)


app.wsgi_app = IngressFix(app.wsgi_app)


@app.context_processor
def inject_ingress_path():
    from flask import request
    return {"ingress_path": request.headers.get("X-Ingress-Path", "")}


# === КОНФИГУРАЦИЯ ===

if os.path.isdir("/data"):
    SCRIPT_DIR = "/data"
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
config_lock = threading.Lock()

default_config = {
    "modbus": {
        "port": os.environ.get("MODBUS_PORT", "/dev/ttyUSB0"),
        "baudrate": int(os.environ.get("BAUDRATE", 9600)),
        "parity": "N",
        "stopbits": 1,
        "timeout": 2,
        "poll_interval": 5
    },
    "sensors": []
}


def load_config():
    with config_lock:
        if not os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4, ensure_ascii=False)
            return default_config
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def save_config(config):
    with config_lock:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)


# === FLASK МАРШРУТЫ ===

@app.route("/")
def index():
    config = load_config()
    sensors = config.get("sensors", [])
    return render_template("index.html", sensors=sensors, config=config)


@app.route("/hello")
def hello():
    return jsonify({"message": "Hello, World!"})


@app.route("/add", methods=["POST"])
def add_sensor():
    name = request.form.get("name")
    slave_id = int(request.form.get("slave_id"))
    temp_register = int(request.form.get("temp_register"))
    hum_register = int(request.form.get("hum_register"))
    register_type = request.form.get("register_type")
    scale = float(request.form.get("scale"))
    config = load_config()
    new_sensor = {
        "name": name,
        "slave_id": slave_id,
        "temp_register": temp_register,
        "hum_register": hum_register,
        "register_type": register_type,
        "scale": scale,
        "enabled": True
    }
    config["sensors"].append(new_sensor)
    save_config(config)
    return redirect(url_for("index"))


@app.route("/delete", methods=["POST"])
def delete_sensor():
    slave_id = int(request.form.get("slave_id"))
    config = load_config()
    config["sensors"] = [s for s in config["sensors"] if s["slave_id"] != slave_id]
    save_config(config)
    return redirect(url_for("index"))


@app.route("/settings", methods=["POST"])
def update_settings():
    config = load_config()
    config["modbus"] = {
        "port": request.form.get("port"),
        "baudrate": int(request.form.get("baudrate")),
        "parity": request.form.get("parity"),
        "stopbits": int(request.form.get("stopbits")),
        "timeout": int(request.form.get("timeout")),
        "poll_interval": int(request.form.get("poll_interval"))
    }
    save_config(config)
    return redirect(url_for("index"))


@app.route("/edit/<int:slave_id>", methods=["GET", "POST"])
def edit_sensor(slave_id):
    config = load_config()
    sensor = None
    for s in config["sensors"]:
        if s["slave_id"] == slave_id:
            sensor = s
            break
    if not sensor:
        return "Датчик не найден", 404
    if request.method == "POST":
        for i, s in enumerate(config["sensors"]):
            if s["slave_id"] == slave_id:
                config["sensors"][i] = {
                    "name": request.form.get("name"),
                    "slave_id": slave_id,
                    "temp_register": int(request.form.get("temp_register")),
                    "hum_register": int(request.form.get("hum_register")),
                    "register_type": request.form.get("register_type"),
                    "scale": float(request.form.get("scale")),
                    "enabled": s.get("enabled", True)
                }
                break
        save_config(config)
        return redirect(url_for("index"))
    return render_template("edit.html", sensor=sensor)


# === MQTT DISCOVERY ===

def setup_mqtt_discovery(mqtt_client, config):
    for sensor in config["sensors"]:
        if not sensor["enabled"]:
            continue

        slave_id = sensor["slave_id"]
        name = sensor["name"]

        # Температура
        temp_config = {
            "name": f"{name} (температура)",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "state_topic": f"homeassistant/sensor/sensor{slave_id}_temp/state",
            "unique_id": f"rs485_sensor{slave_id}_temp"
        }
        mqtt_client.publish(
            f"homeassistant/sensor/sensor{slave_id}_temp/config",
            json.dumps(temp_config),
            retain=True
        )

        # Влажность
        hum_config = {
            "name": f"{name} (влажность)",
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "state_topic": f"homeassistant/sensor/sensor{slave_id}_hum/state",
            "unique_id": f"rs485_sensor{slave_id}_hum"
        }
        mqtt_client.publish(
            f"homeassistant/sensor/sensor{slave_id}_hum/config",
            json.dumps(hum_config),
            retain=True
        )


# === MQTT КОЛБЭКИ ===

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT: подключено")
    else:
        print(f"MQTT: ошибка подключения, код {rc} (4=неверный логин/пароль, 5=не авторизован)")


def on_disconnect(client, userdata, rc):
    if rc == 0:
        print("MQTT: отключено (плановое)")
    else:
        print(f"MQTT: отключено, код {rc}")


# === MODBUS + MQTT ЦИКЛ ===

def modbus_loop():
    print("Modbus-цикл запускается...")
    
    
    # 1. Переменные окружения
    MQTT_HOST = os.environ.get("MQTT_HOST", "core-mosquitto")
    MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
    MQTT_USER = os.environ.get("MQTT_USER", "")
    MQTT_PASS = os.environ.get("MQTT_PASSWORD", "")

    # 2. Загрузка конфига
    config = load_config()

    # 3. Modbus-клиент
    client = ModbusSerialClient(
        port=config["modbus"]["port"],
        framer=FramerType.RTU,
        baudrate=config["modbus"]["baudrate"],
        parity=config["modbus"]["parity"],
        stopbits=config["modbus"]["stopbits"],
        bytesize=8,
        timeout=config["modbus"]["timeout"]
    )
    try:
        client.connect()
        print("Modbus: подключено")
    except Exception as e:
        print(f"Modbus: ошибка подключения: {e}")
        print("Modbus-цикл будет продолжать попытки...")

    # 4. MQTT-клиент
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "modbus_bridge")
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    print(f"DEBUG: host={MQTT_HOST}, port={MQTT_PORT}, user='{MQTT_USER}', pass='{MQTT_PASS}'")
    if MQTT_USER:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    try:
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"MQTT: ошибка подключения: {e}")

    # 5. MQTT Discovery
    setup_mqtt_discovery(mqtt_client, config)

    # 6. Основной цикл опроса
    last_config_hash = None
    try:
        while True:
            config = load_config()

            # Проверяем, изменился ли конфиг
            config_hash = json.dumps(config, sort_keys=True)
            if config_hash != last_config_hash:
                print("Конфиг изменился — обновляем discovery")
                setup_mqtt_discovery(mqtt_client, config)
                last_config_hash = config_hash

            for sensor in config["sensors"]:
                if not sensor["enabled"]:
                    continue

                slave_id = sensor["slave_id"]
                temp_reg = sensor["temp_register"]
                hum_reg = sensor["hum_register"]
                scale = sensor["scale"]
                name = sensor["name"]
                reg_type = sensor["register_type"]

                # Чтение регистров
                if reg_type == "holding":
                    result_temp = client.read_holding_registers(address=temp_reg, count=1, device_id=slave_id)
                    result_hum = client.read_holding_registers(address=hum_reg, count=1, device_id=slave_id)
                else:
                    result_temp = client.read_input_registers(address=temp_reg, count=1, device_id=slave_id)
                    result_hum = client.read_input_registers(address=hum_reg, count=1, device_id=slave_id)

                # Публикация температуры (независимо от влажности)
                if not result_temp.isError():
                    temp = result_temp.registers[0] * scale
                    mqtt_client.publish(
                        f"homeassistant/sensor/sensor{slave_id}_temp/state",
                        str(temp),
                        retain=True
                    )
                    print(f"{name}: T={temp:.1f}°C")
                else:
                    print(f"{name}: Ошибка чтения температуры: {result_temp}")

                # Публикация влажности (независимо от температуры)
                if not result_hum.isError():
                    hum = result_hum.registers[0] * scale
                    mqtt_client.publish(
                        f"homeassistant/sensor/sensor{slave_id}_hum/state",
                        str(hum),
                        retain=True
                    )
                    print(f"{name}: H={hum:.1f}%")
                else:
                    print(f"{name}: Ошибка чтения влажности: {result_hum}")

            time.sleep(config["modbus"]["poll_interval"])

    except KeyboardInterrupt:
        print("Modbus-цикл завершён")
    finally:
        try:
            client.close()
        except Exception:
            pass
        mqtt_client.loop_stop()


# === ТОЧКА ВХОДА ===

if __name__ == "__main__":
    modbus_thread = threading.Thread(target=modbus_loop, daemon=True)
    modbus_thread.start()
    print("Modbus-цикл запущен в фоне")
    print("Flask-сервер запускается...")
    app.run(host="0.0.0.0", port=8099)
