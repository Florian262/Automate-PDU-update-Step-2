from fastapi import FastAPI, Response
from datetime import datetime
import subprocess
import time
import yaml
import threading

app = FastAPI()

# --- Config loader with auto-reload ---
CONFIG_PATH = "config.yaml"
CONFIG = {}
CONFIG_LOCK = threading.Lock()


def load_pdu_config():
    global CONFIG
    try:
        with open(CONFIG_PATH, "r") as f:
            loaded = yaml.safe_load(f)
            with CONFIG_LOCK:
                CONFIG = loaded
            print("[INFO] Config reloaded.")
    except Exception as e:
        print(f"[ERROR] Failed to reload config: {e}")


def auto_reload_config(interval=60):
    def loop():
        while True:
            time.sleep(interval)
            load_pdu_config()
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


def snmp_walk(ip, oid_root="1.3.6.1.4.1.42578"):
    try:
        result = subprocess.check_output([
            "snmpwalk", "-v1", "-c", "public", ip, oid_root
        ]).decode("utf-8")
        return result.splitlines()
    except Exception as e:
        return [f"# SNMP walk failed: {e}"]


# --- SNMP Parsing Helpers ---
def spl_current(data):
    return 0.001 * int(data.split(":")[1])

def spl_voltage(data):
    return 0.01 * int(data.split(":")[1])

def spl_energy(data):
    return 10 * int(data.split(":")[1])

def snmp_get(ip, oid):
    result = subprocess.Popen(
        ["snmpget", "-v1", "-c", "public", ip, oid],
        stdout=subprocess.PIPE
    ).communicate()[0].decode("utf-8")
    return result


# --- Sensor Logic ---
def get_sensor_data():
    start = time.time()
    result_lines = []

    with CONFIG_LOCK:
        ip = CONFIG["pdu"]["ip"]
        voltage_oid = CONFIG["pdu"]["voltage_oid"]
        energy_oid = CONFIG["pdu"]["energy_oid"]
        computes = CONFIG["pdu"]["servers"]

    try:
        voltage = spl_voltage(snmp_get(ip, voltage_oid))
        total_energy = spl_energy(snmp_get(ip, energy_oid))
    except Exception as e:
        return f"# Error fetching shared voltage/energy: {e}"

    compute_data = []
    total_current = 0.0

    for compute in computes:
        try:
            current = spl_current(snmp_get(ip, compute["current_oid"]))
        except Exception as e:
            print(f"[Error] SNMP fail for {compute['name']}: {e}")
            continue

        power = round(current * voltage, 3)
        total_current += current

        compute_data.append({
            "id": compute["name"],
            "current": current,
            "power": power,
            "voltage": voltage
        })

    for item in compute_data:
        prop = item["current"] / total_current if total_current else 0
        item["energy"] = round(total_energy * prop, 3)

    for item in compute_data:
        cid = item["id"]
        result_lines.append(f'pdu_power{{compute_id="{cid}"}} {item["power"]}')
        result_lines.append(f'pdu_voltage{{compute_id="{cid}"}} {item["voltage"]}')
        result_lines.append(f'pdu_current{{compute_id="{cid}"}} {item["current"]}')
        result_lines.append(f'pdu_energy{{compute_id="{cid}"}} {item["energy"]}')

    end = time.time()
    print(f"[INFO] SNMP scrape completed in {round(end - start, 2)} sec")
    return "\n".join(result_lines)


# --- API Endpoints ---
@app.get("/metrics")
async def metrics():
    sensor_data = get_sensor_data()
    print("[INFO] /metrics hit @", datetime.now())
    return Response(sensor_data, media_type="text/plain")


@app.get("/reload")
async def reload_config():
    load_pdu_config()
    return {"status": "reloaded"}


@app.get("/walk")
async def walk():
    with CONFIG_LOCK:
        ip = CONFIG["pdu"]["ip"]
    return {"oids": snmp_walk(ip)}


# --- Startup ---
if __name__ == "__main__":
    load_pdu_config()
    auto_reload_config(interval=60)
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8099)
