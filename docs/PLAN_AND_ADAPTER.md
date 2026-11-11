# Plan format and adapter boundary

Each plan is a UTF-8 JSON object with `schema_version: 1`, nonempty `name`, `scenarios` array and optional `limits`. Unknown fields are errors. No Python expressions or arbitrary plugin names are executed from configuration.

| Scenario field | Meaning / accepted values |
|---|---|
| `name` | Unique nonblank text, up to 100 characters |
| `load_a` | Commanded load, 0..5 A |
| `initial_temperature_c` | Initial lumped module temperature, -40..150 C |
| `expect_shutdown` | Boolean expectation for every sample |
| `ambient_c` | Ambient, -40..125 C; default 25 |
| `samples` | Integer 1..10,000; default 5 |
| `interval_s` | Virtual sampling interval, .001..10 s; default .1 |
| `fault` | Explicit supported injection string; default `none` |
| `description` | Free text explaining scenario intent |

The default limits are 4.75..5.25 V running voltage, 0..0.05 V shutdown voltage, absolute current error <=0.02 A, and -40..125 C measured temperature. Bounds are inclusive. Current expectation is commanded load while expected running and zero while expected shutdown. A hot but correctly tripped DUT can still fail the measured temperature acceptance limit if its initial temperature exceeds 125 C: device shutdown and test acceptance are separate requirements.

Current-error equality uses a 1e-12 relative floating-point comparison tolerance (zero absolute tolerance) to preserve inclusive decimal boundaries after subtraction. This is a numerical rounding allowance, not an instrument uncertainty budget; a configured zero current tolerance still requires exact equality.

Example:

```json
{
  "schema_version": 1,
  "name": "Load verification",
  "limits": {"current_error_a": 0.01},
  "scenarios": [{
    "name": "one_amp", "load_a": 1.0,
    "initial_temperature_c": 25, "expect_shutdown": false,
    "samples": 10, "interval_s": 0.1
  }]
}
```

Supported injected faults are `voltage_bias` (+0.65 V reported while enabled), `current_bias` (+0.2 A reported), `protection_bypass` (trip disabled), `timeout_once`, `timeout_always`, `nonfinite` (NaN voltage) and `unexpected_state` (nonboolean state). They are simulation controls, not instrument instructions.

## Future adapter contract

`DUT` is a Python `Protocol`; implementations can be passed as `run(plan, factory=MyAdapter)`. `factory()` must produce an independent adapter instance. The runner does not dynamically import adapters from untrusted JSON.

1. `connect()` establishes communication and must return or raise within a documented bound.
2. `configure(Scenario)` applies a complete controlled setup. It is not retried. A real implementation must avoid blindly accepting simulation fault fields as physical commands.
3. `sample(interval_s)` returns a `Measurement` containing time, voltage, current, temperature, boolean shutdown and reason (`none`, `thermal`, `current`, `thermal+current`). Invalid telemetry is ERROR, not coerced. Time starts at zero in this simulation contract. A retriable timeout must not make sample identity ambiguous.
4. `safe_shutdown()` requests safe output disable, tolerates partial initialization, and raises if confirmation is unavailable.
5. `close()` releases resources and tolerates partial initialization. It is attempted independently after shutdown failure.

An adapter must implement actual transport timeouts; the runner cannot interrupt a blocking call. A future physical implementation must add a run-inhibit policy on unknown output state, external emergency stop/interlocks, supply compliance limits, calibrated acquisition, settling delay, time-alignment rules and safe connection/disconnection sequencing. None of those integrations are present or verified here.
