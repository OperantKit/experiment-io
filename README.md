# experiment-io

:jp: [日本語版 README](README.ja.md)

experiment-io connects a session to devices. It reads responses from operanda (levers, keys, nose-poke sensors) and operates reinforcer devices (feeders, dippers, pumps), and the same loop runs with virtual subjects in simulated time, with a microcontroller over a serial port, or with a hardware-in-the-loop (HIL) process. Within [OperantKit](https://github.com/OperantKit) it is the hardware abstraction layer (HAL) under [session-runner](https://github.com/OperantKit/session-runner).

- **Test the session without hardware.** Seeded virtual subjects and a manual clock run a whole session in milliseconds, and it repeats exactly.
- **One loop for every backend.** `drive` polls the operanda, passes responses to the session, and has the reinforcer delivered by the device paired with the operandum that produced it.
- **Time-based schedules keep running** when no response arrives, so FI, VI, FT, VT, DRO, and time limits are evaluated.

Notes for AI coding agents are in [AGENTS.md](AGENTS.md).

## Installation


Python 3.11 or newer. experiment-io is not on PyPI yet. It has no dependencies of its own and installs directly from GitHub:

```bash
pip install git+https://github.com/OperantKit/experiment-io
pip install pyserial     # optional: only to open a real serial port
```

The example below also needs a session, which comes from session-runner and the packages under it. Install them from GitHub together:

```bash
# Until the PyPI release, install from GitHub together with its OperantKit dependencies.
pip install git+https://github.com/OperantKit/contingency-dsl-py \
            git+https://github.com/OperantKit/contingency-py \
            git+https://github.com/OperantKit/experiment-core \
            git+https://github.com/OperantKit/session-runner
```

## Example

Drive a concurrent FR 5 FR 5 schedule from two virtual operanda, with one feeder per operandum, until ten reinforcers have been delivered:

```python
from contingency_dsl import parse
from contingency import from_dsl
from experiment_core import ReinforcementCountExit
from experiment_io import drive
from experiment_io.hw.backends.virtual import (
    ManualClock, MultiVirtualOperandum, MultiVirtualSubject, RecordingReinforcer,
)
from session_runner import SessionConfig, SessionRunner

clock = ManualClock()
subject = MultiVirtualSubject(rates_hz=[2.0, 0.5], seeds=[1, 2])  # seeded Poisson responders
operanda = MultiVirtualOperandum(subject, clock).split()
feeders = [RecordingReinforcer(operandum_index=i) for i in range(2)]

runner = SessionRunner(
    SessionConfig(
        name="conc-fr",
        schedule=from_dsl(parse("Conc(FR 5, FR 5)")),
        exit_condition=ReinforcementCountExit(count=10),
    ),
    clock=clock,
)
runner.start(start_time=clock())
drive(runner, operanda, feeders, clock, reinforcer_duration=3.0)

print(runner.state_name, runner.component_responses, [len(f.calls) for f in feeders], round(clock(), 1))
# FINISHED [45, 6] [9, 1] 49.8
```

The faster responder (2 responses/s) produced 9 of the 10 reinforcers, each delivered by the feeder paired with the operandum that completed the ratio; the clock includes 3 s of reinforcer access per delivery. Replacing `MultiVirtualOperandum` and `RecordingReinforcer` with `SerialOperandum` and `SerialReinforcer` runs the same session on a microcontroller.

## What is included

| Part | Names | What it does |
|---|---|---|
| Loop | `drive`, `SessionDriverLike` | Runs any session object with the `SessionDriverLike` methods (typically `session_runner.SessionRunner`) from the devices until it finishes; `max_duration` and `max_steps` bound the loop, and `on_response` receives each response with its session time |
| Interfaces | `Clock`, `Operandum`, `Reinforcer`, `ResponseSample` (`experiment_io.hw`) | What a device backend has to provide |
| Virtual devices | `ManualClock`, `VirtualOperandum`, `MultiVirtualOperandum`, `RecordingReinforcer` | Simulated time, simulated operanda, and a reinforcer device that records each delivery |
| Virtual subjects | `VirtualSubject`, `MultiVirtualSubject` | Poisson responders (exponential interresponse times), one per operandum in the multi version |
| | `ScallopSubject` | Response rate grows with time since the last reinforcer: a post-reinforcement pause followed by accelerated responding under FI |
| | `SpacedRespondingSubject` | Interresponse times (IRTs) shaped by which IRTs are reinforced |
| | `IrtCopyistSubject` | Copies the IRTs that were reinforced (Tanno & Silberberg, 2012) |
| | `MatchingSubject` | Shifts responding toward the operandum with the higher local reinforcement rate (melioration; Herrnstein & Vaughan, 1980) |
| Serial | `SerialOperandum`, `SerialReinforcer`, `open_serial` (`experiment_io.hw.backends.serial`) | A device that speaks newline-delimited JSON over any stream with `readline` / `write` / `flush`; `open_serial` opens a real port with pyserial |
| HIL | `HilBridge` (`experiment_io.hw.backends.hil_bridge`) | Starts a HIL program and talks to it over its standard input and output |

Virtual subjects that serve several operanda do not respond during reinforcer access.

Under yoked VR and VI, `IrtCopyistSubject` reproduces the rate difference of Catania et al. (1977), VR responding faster, but not the convergence of the two rates in the midrange reported by Baum (1993): across pool sizes 5 to 200 and IRT variations (CV) 0.1 to 0.5, the ten-seed median of the reinforcement rate at which its VR / yoked VI rate ratio falls to 1.1 never lies within 300 to 600 reinforcers per hour. The yoked comparison (pool size 70 and CV 0.2, chosen from that sweep) is `tests/laws/test_ratio_vs_interval.py` in [operantkit-py](https://github.com/OperantKit/operantkit-py).

### Wire formats

Serial device, one JSON object per line:

```text
device -> host   {"op": 0, "ts": 1.25, "kind": "response"}
host -> device   {"cmd": "reinforcer", "op": 0, "duration": 3.0}
```

A pyserial `Serial` can be passed as is, and so can a text stream such as `io.StringIO`. Commands are written as UTF-8 bytes, or as text when the stream is a text stream. With `timeout=0` a read can return a line whose newline has not arrived yet; `SerialOperandum` keeps that part and reads the line once the rest arrives, so a response split across two polls is not lost.

```python
# requires pyserial and a device on the port
from experiment_io.hw.backends.serial import SerialOperandum, SerialReinforcer, open_serial

port = open_serial("/dev/ttyACM0")  # pyserial Serial, timeout=0
lever = SerialOperandum(port, operandum_index=0)
feeder = SerialReinforcer(port, operandum_index=0)
```

HIL program, one JSON object per line on its standard input and output:

```text
host -> program  {"cmd": "start", "session_id": "..."}
                 {"cmd": "reinforcer", "op": 0, "duration": 3.0}
                 {"cmd": "stop"}
program -> host  {"event": "ready", "ts": 0.0}
                 {"event": "response", "op": 0, "ts": 1.25}
                 {"event": "reinforcer_done", "op": 0, "ts": 4.25}
                 {"event": "error", "msg": "..."}
```

Timestamps (`ts`) are seconds from the device's own clock; see [Session time](#session-time). Give `HilBridge` the program to start with `binary=` or the `OPERANTKIT_HIL_BINARY` environment variable:

```python
# requires a HIL program that speaks the protocol above
from experiment_io.hw.backends.hil_bridge import HilBridge

bridge = HilBridge(binary="/path/to/hil-program")
```

### Session time

The session runs on the `clock` given to `drive`, and on nothing else. `drive` hands each response to the session with `clock()` read at that moment. The device's `ts` stays in `ResponseSample.timestamp` as a record and does not become session time: a line reaches the host 1 to 7 ms after the response (measured on a Teensy 4.1 over USB), after `drive` has already stamped a tick with the host clock, so the device time would move session time backwards and the schedule would stop with `ScheduleStateError: Non-monotonic time`. The device's clock therefore needs no alignment with the host's.

To keep the device's time, pass `on_response`; it receives each sample with the session time it was given:

```python
# requires the runner, lever and feeder from the examples above
log = []
drive(runner, [lever], [feeder], time.monotonic,
      on_response=lambda sample, t: log.append((sample.timestamp, t)))
```

Virtual operanda advance `clock` themselves, so for them the two times are equal.

## Limitations

- Not on PyPI yet, and the API can still change.
- No firmware is distributed. A serial device has to speak the JSON lines above; you write that program yourself.
- The `contingency-hil` program of [contingency-rs](https://github.com/OperantKit/contingency-rs) is a TCP server with a different message format, so `HilBridge` cannot drive it as is. Without `binary=` or `OPERANTKIT_HIL_BINARY`, `HilBridge` falls back to a built-in relative path that contingency-rs does not build.
- With a real clock, `drive` waits with `time.sleep` and polls every 1 ms by default (`poll_interval`). Hardware timing has not been measured.
- With a real clock, `drive` does not poll during reinforcer access. Responses a device sends during access are read when access ends, are given the session time of that moment, and count toward the schedule.
- The virtual subjects are models. They show that the program delivers the schedule you wrote, not what an animal will do.

## Development

```bash
git clone https://github.com/OperantKit/experiment-io && cd experiment-io
python -m venv .venv
G=git+https://github.com/OperantKit
.venv/bin/python -m pip install --no-deps $G/contingency-dsl-py $G/contingency-py $G/experiment-core $G/session-runner -e .
.venv/bin/python -m pip install pytest pytest-cov hypothesis
.venv/bin/pytest
```

## References

- Baum, W. M. (1993). Performances on ratio and interval schedules of reinforcement: Data and theory. *Journal of the Experimental Analysis of Behavior*, 59(2), 245–264. https://doi.org/10.1901/jeab.1993.59-245
- Catania, A. C., Matthews, T. J., Silverman, P. J., & Yohalem, R. (1977). Yoked variable-ratio and variable-interval responding in pigeons. *Journal of the Experimental Analysis of Behavior*, 28(2), 155–161. https://doi.org/10.1901/jeab.1977.28-155
- Herrnstein, R. J., & Vaughan, W. (1980). Melioration and behavioral allocation. In J. E. R. Staddon (Ed.), *Limits to action: The allocation of individual behavior* (pp. 143-176). Academic Press.
- Tanno, T., & Silberberg, A. (2012). The copyist model of response emission. *Psychonomic Bulletin & Review*, 19(5), 759–778. https://doi.org/10.3758/s13423-012-0267-1

## License

MIT. See [LICENSE](LICENSE).
