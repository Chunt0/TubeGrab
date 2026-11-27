"""Change notifications every client watches (/api/events): a library version
that bumps when anything shown changes, progress patches and download status."""

import json
import threading
import uuid


_bus = threading.Condition()
_bus_seq = 0
_bus_last: dict[str, tuple[int, str]] = {}          # latest event of each kind
_bus_prog: list[tuple[int, str]] = []               # recent progress patches (all of them matter)
_lib_version = 0


def _publish(kind: str, data) -> None:
    _publish_raw(kind, json.dumps(data, separators=(",", ":")))


def _publish_raw(kind: str, payload: str) -> None:
    """`payload`: the event's JSON text."""
    global _bus_seq
    with _bus:
        _bus_seq += 1
        if kind == "progress":
            _bus_prog.append((_bus_seq, payload))
            del _bus_prog[:-200]
        else:
            _bus_last[kind] = (_bus_seq, payload)
        _bus.notify_all()


def _lib_bump() -> None:
    """Something clients show changed: they re-check what they have (ETags make
    unchanged answers free)."""
    global _lib_version
    _lib_version += 1
    _publish("lib", {"v": f"{_BOOT}-{_lib_version}"})


_BOOT = uuid.uuid4().hex[:6]


_jobs_dirty = threading.Event()
  # download status changed (jobs._jobs_publisher sends it)
