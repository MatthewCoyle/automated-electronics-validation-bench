"""Independent per-sample acceptance oracle and bounded lifecycle execution."""
from dataclasses import asdict
import logging
import math
from .device import SimulatedDUT
from .model import Measurement

LOG = logging.getLogger(__name__)


def evaluate(scenario, limits, measurements):
    checks = []
    def check(name, passed, expected, actual):
        checks.append(dict(name=name, passed=bool(passed), expected=expected, actual=actual))
    check("sample_count", len(measurements) == scenario.samples, scenario.samples, len(measurements))
    for i, m in enumerate(measurements):
        prefix = f"sample[{i}]"
        check(prefix + ".time", abs(m.time_s - i * scenario.interval_s) <= 1e-7,
              i * scenario.interval_s, m.time_s)
        check(prefix + ".state", m.shutdown == scenario.expect_shutdown, scenario.expect_shutdown, m.shutdown)
        lower, upper = (0.0, limits.off_max_v) if scenario.expect_shutdown else (limits.voltage_min_v, limits.voltage_max_v)
        check(prefix + ".voltage_v", lower <= m.voltage_v <= upper, [lower, upper], m.voltage_v)
        expected_current = 0.0 if scenario.expect_shutdown else scenario.load_a
        current_error = abs(m.current_a - expected_current)
        # Preserve inclusive decimal boundaries after binary floating-point subtraction.
        current_ok = current_error <= limits.current_error_a or math.isclose(
            current_error, limits.current_error_a, rel_tol=1e-12, abs_tol=0.0)
        check(prefix + ".current_a", current_ok,
              [expected_current - limits.current_error_a, expected_current + limits.current_error_a], m.current_a)
        check(prefix + ".temperature_c", limits.temperature_min_c <= m.temperature_c <= limits.temperature_max_c,
              [limits.temperature_min_c, limits.temperature_max_c], m.temperature_c)
        check(prefix + ".reason_consistency", (m.reason != "none") == m.shutdown,
              "trip reason iff shutdown", m.reason)
    return checks


def run(plan, factory=SimulatedDUT):
    results = []
    for scenario in plan.scenarios:
        LOG.info("Starting scenario %s", scenario.name)
        measurements, events, errors = [], [], []
        dut = None
        try:
            dut = factory()
            dut.connect()
            dut.configure(scenario)
            for _ in range(scenario.samples):
                try:
                    measurement = dut.sample(scenario.interval_s)
                except TimeoutError as exc:
                    # Only a read is retried, once. Writes/configure are never retried.
                    events.append(f"sample timeout; one read retry: {exc}")
                    LOG.warning("%s: read timeout, retrying once", scenario.name)
                    measurement = dut.sample(scenario.interval_s)
                if not isinstance(measurement, Measurement):
                    raise ValueError("Adapter returned wrong measurement type")
                measurement.validate()
                measurements.append(measurement)
        except Exception as exc:  # Isolate unexpected adapters; never report an error as PASS.
            errors.append(f"{type(exc).__name__}: {exc}")
            LOG.error("%s: %s", scenario.name, errors[-1])
        finally:
            if dut is not None:
                for action in ("safe_shutdown", "close"):
                    try:
                        getattr(dut, action)()
                    except Exception as exc:
                        errors.append(f"{action}: {type(exc).__name__}: {exc}")
                        LOG.error("%s cleanup: %s", scenario.name, errors[-1])
        checks = evaluate(scenario, plan.limits, measurements)
        results.append(dict(scenario=scenario.name, stimulus=asdict(scenario),
                            status="ERROR" if errors else "PASS" if all(c["passed"] for c in checks) else "FAIL",
                            measurements=[asdict(m) for m in measurements], checks=checks,
                            events=events, errors=errors))
    return results


def summarize(results):
    return {"total": len(results), **{s.lower(): sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "ERROR")}}
