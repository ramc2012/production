"""Non-negotiable #1: FlowTwin never writes to a plant system (DCS, PLC, SCADA, OPC UA, Modbus, historian).

The build fails if any Python file in app/ or flowval/ contains:
  - an OPC UA write call (write_value, set_value, write_attribute, write_values, set_attribute);
  - a Modbus write call (write_register(s), write_coil(s), mask_write_register, readwrite_registers);
  - an HTTP verb other than GET in the connectors package;
  - an import of a plant-protocol library outside the connectors package.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = [ROOT / "app", ROOT / "flowval"]
CONNECTORS = ROOT / "app" / "backend" / "connectors"

WRITE_CALLS = re.compile(
    r"\.(write_value|write_values|set_value|write_attribute|set_attribute|write_registers?|write_coils?|"
    r"mask_write_register|readwrite_registers|write_multiple_registers|write_single_register|write_tag)\s*\(")
HTTP_WRITES = re.compile(r"\.(post|put|patch|delete)\s*\(|method\s*=\s*['\"](POST|PUT|PATCH|DELETE)['\"]", re.I)
PROTOCOL_LIBS = re.compile(r"^\s*(import|from)\s+(asyncua|opcua|pymodbus|pycomm3|snap7|pylogix|OpenOPC|PIconnect)\b",
                           re.M)


def _py_files():
    for base in SCANNED:
        for p in base.rglob("*.py"):
            if "node_modules" in p.parts or "static" in p.parts:
                continue
            yield p


def test_no_plant_write_calls():
    hits = []
    for p in _py_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if WRITE_CALLS.search(code):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "Plant write API found:\n" + "\n".join(hits)


def test_connectors_only_read():
    hits = []
    for p in CONNECTORS.rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if HTTP_WRITES.search(code):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "Connector uses a non-GET HTTP verb:\n" + "\n".join(hits)


def test_protocol_libraries_only_in_connectors():
    hits = []
    for p in _py_files():
        if CONNECTORS in p.parents:
            continue
        if PROTOCOL_LIBS.search(p.read_text(encoding="utf-8")):
            hits.append(str(p.relative_to(ROOT)))
    assert not hits, "Plant-protocol library imported outside app/backend/connectors: " + ", ".join(hits)


def test_guard_catches_a_planted_write(tmp_path):
    """The guard itself works (a write call is detected)."""
    assert WRITE_CALLS.search("await node.write_value(ua.DataValue(5))")
    assert WRITE_CALLS.search("client.write_register(40001, 1)")
    assert HTTP_WRITES.search("httpx.post(url, json=x)")
    assert PROTOCOL_LIBS.search("from asyncua import Client")
    assert not WRITE_CALLS.search("client.read_holding_registers(0, 10)")


def test_l5_targets_simulator_only():
    src = (ROOT / "app" / "backend" / "services" / "twin.py").read_text(encoding="utf-8")
    assert "make_plant(" in src            # the sandbox loop acts on the simulator built from the twin
    assert "connectors" not in src
