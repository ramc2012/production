"""Live plant-data connectors - READ ONLY by construction (v2, not implemented yet).

Planned: OPC UA client reads (asyncua), PI Web API GET requests, Modbus TCP reads (pymodbus).
Every connector here may only *read*. tests/app/test_readonly_guard.py fails the build if any
module in app/ or flowval/ calls a write API (OPC UA write_value/set_value/write_attribute,
Modbus write_register(s)/write_coil(s)) or uses an HTTP verb other than GET in this package.

Deployment rule: the FlowTwin server sits on the IT/DMZ side and reads from the historian or a
DMZ OPC UA server. It never reaches the control network directly.
"""
