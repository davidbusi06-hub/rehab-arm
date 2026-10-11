"""Read-only status of the six follower-arm motors, printed as JSON.

Run with the lerobot environment's Python (it has scservo_sdk); the dashboard calls it
through arm_control.motor_status(). It only reads registers and never moves a motor, but
it does open the serial port, so it must not run while another program is driving the arm.
"""

import json
import os
import sys

PORT = os.environ.get("ARM_PORT", "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C125047-if00")
MOTORS = {1: "shoulder_pan", 2: "shoulder_lift", 3: "elbow_flex", 4: "wrist_flex", 5: "wrist_roll", 6: "gripper"}
# STS3215 registers
TORQUE_ENABLE, PRESENT_LOAD, PRESENT_VOLTAGE, PRESENT_TEMPERATURE, STATUS = 40, 60, 62, 63, 65


def main():
    import scservo_sdk as scs

    port, packet = scs.PortHandler(PORT), scs.PacketHandler(0)
    if not port.openPort() or not port.setBaudRate(1_000_000):
        print(json.dumps({"error": f"could not open {PORT}"}))
        return 1
    motors = []
    try:
        for motor_id, name in MOTORS.items():
            torque, comm, _ = packet.read1ByteTxRx(port, motor_id, TORQUE_ENABLE)
            if comm != 0:
                motors.append({"name": name, "reachable": False})
                continue
            status, _, _ = packet.read1ByteTxRx(port, motor_id, STATUS)
            temp, _, _ = packet.read1ByteTxRx(port, motor_id, PRESENT_TEMPERATURE)
            volts, _, _ = packet.read1ByteTxRx(port, motor_id, PRESENT_VOLTAGE)
            load, _, _ = packet.read2ByteTxRx(port, motor_id, PRESENT_LOAD)
            motors.append({
                "name": name, "reachable": True, "torque_on": bool(torque), "status": int(status),
                "temp_c": int(temp), "volts": volts / 10,
                "load_pct": (load & 0x3FF) * (-1 if load & 0x400 else 1) / 10,
            })
    finally:
        port.closePort()
    print(json.dumps({"motors": motors}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
