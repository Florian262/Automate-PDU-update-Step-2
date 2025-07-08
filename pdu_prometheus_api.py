# Optimized PDU Power Monitoring - Using YAML Config + PySNMP

from fastapi import FastAPI, Response
from datetime import datetime
import time
import yaml
from pysnmp.hlapi import *

app = FastAPI()

# --- Load config from YAML ---
def load_pdu_config(yaml_path="pdu_config.yaml"):
    try:
        with open(yaml_path, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load YAML config: {e}")
        return {}

CONFIG = load_pdu_config()
PDU_IP = CONFIG["pdu"]["ip"]
VOLTAGE_OID = CONFIG["pdu"]["voltage_oid"]
ENERGY_OID = CONFIG["pdu"]["energy_oid"]
COMPUTES = CONFIG["pdu"]["servers"]

# --- SNMP Utility (pysnmp) ---
def snmp_get(ip, oid, community="public", port=161):
    iterator = getCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=0),
        UdpTransportTarget((ip, port), timeout=2.0, retries=1),
        ContextData(),
        ObjectType(ObjectIdentity(oid))
    )
    errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

    if errorIndication:
        raise Exception(f"SNMP error: {errorIndication}")
    elif errorStatus:
        raise Exception(f"SNMP error: {errorStatus.prettyPrint()} at {errorIndex}")
    else:
        for varBind in varBinds:
            return str(varBind).split("=")[1].strip()


# --- Data parsing helpers ---
def spl_current(val):
    return 0.001 * int(val)

def spl_voltage(val):
    return 0.01 * int(val)

def spl_energy(val):
    return 10 * int(val)


# --- Main SNMP Reading and Metrics Logic ---
def get_sensor_data():
    start = time.time()
    result_lines = []

    try:
        voltage_raw = snmp_get(PDU_IP, VOLTAGE_OID)
        energy_raw = snmp_get(PDU_IP, ENERGY_OID)
        voltage = spl_voltage(voltage_raw)
        total_energy = spl_energy(energy_raw)
    except Exception as e:
        return f"# Error fetching shared voltage/energy: {e}"

    compute_data = []
    total_current = 0.0

    for compute in COMPUTES:
        try:
            current_raw = snmp_get(PDU_IP, compute["current_oid"])
            current = spl_current(current_raw)
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

@app.get("/metrics")
async def metrics():
    sensor_data = get_sensor_data()
    print("[INFO] /metrics hit @", datetime.now())
    return Response(sensor_data, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8099)
