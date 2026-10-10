"""Motor access for the rehab arm: the real Feetech bus and a simulator.

Both expose the same small interface so the session controller in
rehab_arm.py never knows which one it is talking to:

    read(id) -> Sample | None      set_torque(id, on)
    set_goal(id, ticks)            set_torque_limit(id, pct)
    now() -> seconds               sleep(seconds)

Positions are raw encoder ticks (4096 per turn), load is percent of the
motor's maximum torque.
"""

import time
from dataclasses import dataclass

TICKS_PER_REV = 4096
DEG_PER_TICK = 360.0 / TICKS_PER_REV

JOINTS = {
    "shoulder_pan": 1,
    "shoulder_lift": 2,
    "elbow_flex": 3,
    "wrist_flex": 4,
    "wrist_roll": 5,
    "gripper": 6,
}

# STS3215 control table. Everything written is in RAM, so nothing survives a power cycle.
ADDR_MIN_POSITION_LIMIT = 9   # EEPROM, set by lerobot-calibrate; only ever read here
ADDR_MAX_POSITION_LIMIT = 11  # EEPROM, set by lerobot-calibrate; only ever read here
ADDR_HOMING_OFFSET = 31  # EEPROM; only ever read here
ADDR_TORQUE_ENABLE = 40
ADDR_ACCELERATION = 41
ADDR_GOAL_POSITION = 42
ADDR_GOAL_VELOCITY = 46
ADDR_TORQUE_LIMIT = 48
ADDR_PRESENT_POSITION = 56  # then velocity, load, voltage, temperature

WRITE_RETRIES = 3


@dataclass
class Sample:
    pos: int        # ticks
    speed: float    # ticks/s, signed
    load: float     # percent of max torque, unsigned
    volt: float     # volts
    temp: int       # deg C


def _signed(value, sign_bit):
    """Decode Feetech sign-magnitude: sign_bit set means negative."""
    magnitude = value & ((1 << sign_bit) - 1)
    return -magnitude if value & (1 << sign_bit) else magnitude


class FeetechBus:
    """Real arm over USB serial, via the scservo_sdk that lerobot installs."""

    def __init__(self, port, baudrate=1_000_000):
        import scservo_sdk as scs

        self._scs = scs
        self._ph = scs.PortHandler(port)
        if not self._ph.openPort():
            raise IOError(f"could not open {port}")
        self._ph.setBaudRate(baudrate)
        self._pk = scs.PacketHandler(0)

    def close(self):
        self._ph.closePort()

    def now(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)

    def read(self, sid):
        try:
            data, result, _ = self._pk.readTxRx(self._ph, sid, ADDR_PRESENT_POSITION, 8)
        except OSError:  # serial glitch; callers treat it as a missed read
            # The SDK leaves the port flagged busy when a read raises, which
            # would make every later packet fail.
            self._ph.is_using = False
            return None
        if result != self._scs.COMM_SUCCESS or len(data) < 8:
            return None
        return Sample(
            pos=_signed(data[0] | data[1] << 8, 15),
            speed=float(_signed(data[2] | data[3] << 8, 15)),
            load=abs(_signed(data[4] | data[5] << 8, 10)) / 10.0,
            volt=data[6] / 10.0,
            temp=data[7],
        )

    def _write(self, sid, addr, value, size):
        write = self._pk.write1ByteTxRx if size == 1 else self._pk.write2ByteTxRx
        for _ in range(WRITE_RETRIES):
            result, _ = write(self._ph, sid, addr, int(value))
            if result == self._scs.COMM_SUCCESS:
                return
        raise IOError(f"write to motor {sid} address {addr} failed")

    def set_torque(self, sid, on):
        self._write(sid, ADDR_TORQUE_ENABLE, 1 if on else 0, 1)

    def set_goal(self, sid, ticks):
        ticks = int(ticks)
        self._write(sid, ADDR_GOAL_POSITION, abs(ticks) | (0x8000 if ticks < 0 else 0), 2)

    def position_bounds(self, sid):
        """Lowest and highest position this motor can report before its encoder wraps.

        Positions are reported with the stored homing offset subtracted, so
        the raw 0..4095 seam sits at a different place on every motor.
        """
        value, result, _ = self._pk.read2ByteTxRx(self._ph, sid, ADDR_HOMING_OFFSET)
        if result != self._scs.COMM_SUCCESS:
            raise IOError(f"could not read homing offset of motor {sid}")
        offset = _signed(value, 11)
        return -offset, TICKS_PER_REV - 1 - offset

    def travel_limits(self, sid):
        """(lowest, highest) position between this joint's mechanical end stops, or None.

        lerobot-calibrate stores the range swept during calibration in the
        motor, in the same units read() reports. None means the motor has no
        such range: it was never calibrated, or it is a full-turn joint.
        """
        low, result, _ = self._pk.read2ByteTxRx(self._ph, sid, ADDR_MIN_POSITION_LIMIT)
        if result != self._scs.COMM_SUCCESS:
            raise IOError(f"could not read position limits of motor {sid}")
        high, result, _ = self._pk.read2ByteTxRx(self._ph, sid, ADDR_MAX_POSITION_LIMIT)
        if result != self._scs.COMM_SUCCESS:
            raise IOError(f"could not read position limits of motor {sid}")
        if high <= low or high - low >= TICKS_PER_REV - 1:
            return None
        return low, high

    def set_torque_limit(self, sid, pct):
        self._write(sid, ADDR_TORQUE_LIMIT, max(0, min(1000, round(pct * 10))), 2)

    def get_torque_limit(self, sid):
        value, result, _ = self._pk.read2ByteTxRx(self._ph, sid, ADDR_TORQUE_LIMIT)
        if result != self._scs.COMM_SUCCESS:
            raise IOError(f"could not read torque limit of motor {sid}")
        return value / 10.0

    def set_motion_profile(self, sid, speed_deg_s, acceleration):
        """Cap how fast the motor itself will travel towards a goal."""
        self._write(sid, ADDR_ACCELERATION, acceleration, 1)
        self._write(sid, ADDR_GOAL_VELOCITY, round(speed_deg_s / DEG_PER_TICK), 2)

    def get_motion_profile(self, sid):
        """(acceleration, goal velocity in ticks/s) currently held in the motor's RAM.

        These registers survive until the motor loses power, so a session that
        caps the speed must put them back or the cap outlives the program.
        """
        acceleration, result, _ = self._pk.read1ByteTxRx(self._ph, sid, ADDR_ACCELERATION)
        if result != self._scs.COMM_SUCCESS:
            raise IOError(f"could not read acceleration of motor {sid}")
        velocity, result, _ = self._pk.read2ByteTxRx(self._ph, sid, ADDR_GOAL_VELOCITY)
        if result != self._scs.COMM_SUCCESS:
            raise IOError(f"could not read goal velocity of motor {sid}")
        return acceleration, velocity

    def restore_motion_profile(self, sid, profile):
        """Write back a profile previously returned by get_motion_profile."""
        acceleration, velocity = profile
        self._write(sid, ADDR_ACCELERATION, acceleration, 1)
        self._write(sid, ADDR_GOAL_VELOCITY, velocity, 2)

    def hold(self, sid):
        """Lock a joint where it currently is."""
        sample = self.read(sid)
        if sample is None:
            raise IOError(f"motor {sid} did not answer")
        self.set_goal(sid, sample.pos)
        self.set_torque(sid, True)


class SimBus:
    """One simulated joint with a scripted patient, on a virtual clock.

    The patient pushes out to `abilities[rep]` degrees on their own, then
    stalls until the controller assists with at least `needed_assist`
    percent torque. `spasm_rep` makes that rep jerk fast enough to trip
    the speed safety stop.
    """

    PATIENT_SPEED = 30.0   # deg/s
    ASSIST_SPEED = 15.0    # deg/s
    GIVE_UP_AFTER = 15.0   # s stalled before the patient goes back anyway

    def __init__(self, target_rom, abilities, needed_assist=25.0, spasm_rep=None, home=2048, travel=None):
        self.target_rom = target_rom
        self.abilities = abilities
        self.needed_assist = needed_assist
        self.spasm_rep = spasm_rep
        self.home = home
        self._t = 0.0
        self._pos = float(home)
        self._speed = 0.0
        self._torque = False
        self._goal = home
        self._limit = 100.0
        self._profile = (0, 0)  # (acceleration, goal velocity ticks/s), motor power-on default
        self._travel = travel  # (lowest, highest) tick between end stops; None = unlimited
        self._phase = "rest"
        self._rest = 1.0
        self._waited = 0.0
        self._rep = 0

    def close(self):
        pass

    def now(self):
        return self._t

    def sleep(self, seconds):
        self._t += seconds
        self._step(seconds)

    def _step(self, dt):
        exc = (self._pos - self.home) * DEG_PER_TICK
        velocity = 0.0
        if self._phase == "rest":
            self._rest -= dt
            if self._rest <= 0:
                self._phase, self._waited = "out", 0.0
        elif self._phase == "out":
            ability = self.abilities[self._rep % len(self.abilities)]
            if exc < ability:
                spasm = self._rep == self.spasm_rep and 10 < exc < 20
                velocity = 400.0 if spasm else self.PATIENT_SPEED
            elif self._torque and self._goal > self._pos and self._limit >= self.needed_assist:
                velocity = self.ASSIST_SPEED
            else:
                self._waited += dt
            if exc >= self.target_rom - 0.2 or self._waited > self.GIVE_UP_AFTER:
                self._phase = "back"
        elif self._phase == "back":
            velocity = -self.PATIENT_SPEED
            if exc <= 0:
                velocity = 0.0
                self._phase, self._rest = "rest", 1.0
                self._rep += 1
        self._pos += velocity * dt / DEG_PER_TICK
        self._speed = velocity / DEG_PER_TICK

    def read(self, sid):
        return Sample(
            pos=round(self._pos),
            speed=self._speed,
            load=self._limit if self._torque else 0.0,
            volt=12.0,
            temp=30,
        )

    def set_torque(self, sid, on):
        self._torque = on

    def set_goal(self, sid, ticks):
        self._goal = ticks

    def set_torque_limit(self, sid, pct):
        self._limit = pct

    def get_torque_limit(self, sid):
        return self._limit

    def position_bounds(self, sid):
        return 0, TICKS_PER_REV - 1

    def travel_limits(self, sid):
        return self._travel

    def set_motion_profile(self, sid, speed_deg_s, acceleration):
        self._profile = (acceleration, round(speed_deg_s / DEG_PER_TICK))

    def get_motion_profile(self, sid):
        return self._profile

    def restore_motion_profile(self, sid, profile):
        self._profile = profile

    def hold(self, sid):
        pass
