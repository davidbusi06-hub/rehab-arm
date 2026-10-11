"""Switch every follower motor off and clear the gripper's overload latch.

    python clear_gripper_overload.py

The gripper motor (id 6) trips its overload protection when it is squeezed against
something for a while. LeRobot then fails at shutdown with
"Failed to write 'Torque_Enable' on id_=6 ... Overload error!", can leave other motors
powered, and the next start reports motor 6 as missing. Run this after any such error.
The recorded data is not affected.

It clears the latch by switching the gripper's torque on at its current position for a
moment and off again, so the gripper does not move. Power-cycling the follower does the same.
"""

import time

import scservo_sdk as scs

PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C125047-if00"
TORQUE_ENABLE, GOAL_POSITION, PRESENT_POSITION, STATUS, GRIPPER, OVERLOAD = 40, 42, 56, 65, 6, 32

port = scs.PortHandler(PORT)
packet = scs.PacketHandler(0)
port.openPort()
port.setBaudRate(1_000_000)
port.setPacketTimeoutMillis(40)
for _ in range(2):
    for motor in range(1, 7):
        packet.write1ByteTxRx(port, motor, TORQUE_ENABLE, 0)
if packet.read1ByteTxRx(port, GRIPPER, STATUS)[0] & OVERLOAD:
    position = packet.read2ByteTxRx(port, GRIPPER, PRESENT_POSITION)[0]
    packet.write2ByteTxRx(port, GRIPPER, GOAL_POSITION, position)
    packet.write1ByteTxRx(port, GRIPPER, TORQUE_ENABLE, 1)
    time.sleep(0.3)
    packet.write1ByteTxRx(port, GRIPPER, TORQUE_ENABLE, 0)
    time.sleep(0.2)
    print("cleared the gripper overload")
print("torque:", [packet.read1ByteTxRx(port, m, TORQUE_ENABLE)[0] for m in range(1, 7)],
      "status:", [packet.read1ByteTxRx(port, m, STATUS)[0] for m in range(1, 7)])
port.closePort()
