"""Versioned, strict configuration and measurement contracts (SI-derived units)."""
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
from pathlib import Path


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not low <= value <= high:
        raise ValueError(f"{name} must be within [{low}, {high}]")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def strict(cls, data):
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__} must be an object")
    unknown = set(data) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} fields: {sorted(unknown)}")
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"Invalid {cls.__name__}: {exc}") from exc


@dataclass(frozen=True)
class Limits:
    voltage_min_v: float = 4.75
    voltage_max_v: float = 5.25
    off_max_v: float = 0.05
    current_error_a: float = 0.02
    temperature_min_c: float = -40
    temperature_max_c: float = 125

    def __post_init__(self):
        for key, value in asdict(self).items():
            number(value, key, -100 if key.startswith('temperature') else 0, 200)
        if self.voltage_min_v > self.voltage_max_v or self.temperature_min_c > self.temperature_max_c:
            raise ValueError("Inverted acceptance limits")


@dataclass(frozen=True)
class Scenario:
    name: str
    load_a: float
    initial_temperature_c: float
    expect_shutdown: bool
    ambient_c: float = 25
    samples: int = 5
    interval_s: float = 0.1
    fault: str = "none"
    description: str = ""

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100:
            raise ValueError("Scenario name must contain 1..100 characters")
        if not isinstance(self.description, str):
            raise ValueError("description must be text")
        number(self.load_a, "load_a", 0, 5)
        number(self.initial_temperature_c, "initial_temperature_c", -40, 150)
        number(self.ambient_c, "ambient_c", -40, 125)
        number(self.interval_s, "interval_s", 0.001, 10)
        if type(self.samples) is not int or not 1 <= self.samples <= 10000:
            raise ValueError("samples must be an integer in 1..10000")
        if type(self.expect_shutdown) is not bool:
            raise ValueError("expect_shutdown must be boolean")
        if not isinstance(self.fault, str) or self.fault not in {
            "none", "voltage_bias", "protection_bypass", "timeout_once",
            "timeout_always", "nonfinite", "unexpected_state", "current_bias"}:
            raise ValueError("Unknown fault injection")


@dataclass(frozen=True)
class Plan:
    name: str
    scenarios: tuple[Scenario, ...]
    limits: Limits = Limits()
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Only schema_version 1 is supported")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Plan name must be non-empty text")
        if not isinstance(self.scenarios, tuple) or not all(isinstance(s, Scenario) for s in self.scenarios):
            raise ValueError("scenarios must be a tuple of Scenario objects")
        if not isinstance(self.limits, Limits):
            raise ValueError("limits must be a Limits object")
        if not self.scenarios or len(self.scenarios) > 1000:
            raise ValueError("Plan must contain 1..1000 scenarios")
        if sum(s.samples for s in self.scenarios) > 20000:
            raise ValueError("Plan exceeds 20000 sample budget")
        if len({s.name for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("Duplicate scenario names")

    @property
    def digest(self):
        canonical = json.dumps(asdict(self), sort_keys=True, allow_nan=False).encode()
        return hashlib.sha256(canonical).hexdigest()


def load_plan(path):
    if Path(path).stat().st_size > 1_000_000:
        raise ValueError("Plan file exceeds 1 MB")
    def reject_constant(value):
        raise ValueError(f"Non-standard JSON number {value}")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key {key}")
            result[key] = value
        return result
    data = json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=reject_constant, object_pairs_hook=unique_keys)
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise ValueError("Plan requires scenarios array")
    data["scenarios"] = tuple(strict(Scenario, s) for s in data["scenarios"])
    data["limits"] = strict(Limits, data.get("limits", {}))
    return strict(Plan, data)


@dataclass(frozen=True)
class Measurement:
    time_s: float
    voltage_v: float
    current_a: float
    temperature_c: float
    shutdown: bool
    reason: str

    def validate(self):
        number(self.time_s, "time_s", 0, 1e8)
        number(self.voltage_v, "voltage_v", -100, 100)
        number(self.current_a, "current_a", -1, 100)
        number(self.temperature_c, "temperature_c", -100, 250)
        if type(self.shutdown) is not bool or self.reason not in {"none", "thermal", "current", "thermal+current"}:
            raise ValueError("Malformed state telemetry")
