"""Lifecycle contract and deterministic first-order simulated power module."""
import math
from typing import Protocol
from .model import Measurement, Scenario, number


class CommunicationError(RuntimeError):
    """Adapter communication failure; safe output cannot be assumed."""


class DUT(Protocol):
    """Adapters must bound all operations; sample timeout must not apply stimulus."""
    def connect(self) -> None: ...
    def configure(self, scenario: Scenario) -> None: ...
    def sample(self, interval_s: float) -> Measurement: ...
    def safe_shutdown(self) -> None: ...
    def close(self) -> None: ...


class SimulatedDUT:
    """No wall-clock sleeps, random numbers, hardware, or physical measurements."""
    def __init__(self):
        self.connected = False
        self.enabled = False
        self.scenario = None
        self.attempts = 0
        self.time_s = 0.0
        self.temperature_c = 25.0
        self.reason = "none"

    def connect(self):
        self.connected = True

    def configure(self, scenario):
        if not isinstance(scenario, Scenario):
            raise ValueError("configure requires Scenario")
        if not self.connected:
            raise CommunicationError("DUT not connected")
        self.scenario = scenario
        self.enabled = True
        self.attempts = 0
        self.time_s = 0.0
        self.temperature_c = scenario.initial_temperature_c
        self.reason = "none"

    def sample(self, interval_s):
        number(interval_s, "interval_s", .001, 10)
        if not self.connected or self.scenario is None:
            raise CommunicationError("DUT not configured")
        s = self.scenario
        self.attempts += 1
        if s.fault == "timeout_always" or (s.fault == "timeout_once" and self.attempts == 1):
            raise TimeoutError("Injected sample timeout (no time advance)")
        thermal, current = self.temperature_c >= 80.0, s.load_a >= 2.0
        if (thermal or current) and s.fault != "protection_bypass":
            self.enabled = False  # Trip latches until the next explicit configure.
            if self.reason == "none":
                self.reason = "thermal+current" if thermal and current else "thermal" if thermal else "current"
        voltage = 5.0 - 0.07 * s.load_a if self.enabled else 0.0
        current_a = s.load_a if self.enabled else 0.0
        actual_current_a = current_a
        if s.fault == "voltage_bias" and self.enabled:
            voltage += 0.65
        if s.fault == "current_bias":
            current_a += 0.2
        if s.fault == "nonfinite":
            voltage = math.nan
        reading = Measurement(round(self.time_s, 9), voltage, current_a, self.temperature_c,
                              not self.enabled, self.reason)
        if s.fault == "unexpected_state":
            reading = Measurement(reading.time_s, voltage, current_a, self.temperature_c, "off", "none")
        # Exact discrete update avoids Euler instability for large permitted intervals.
        equilibrium = s.ambient_c + (8.0 * actual_current_a * actual_current_a if self.enabled else 0.0)
        self.temperature_c = equilibrium + (self.temperature_c - equilibrium) * math.exp(-interval_s / 20.0)
        self.time_s += interval_s
        return reading

    def safe_shutdown(self):
        self.enabled = False

    def close(self):
        self.enabled = False
        self.connected = False
