"""Orbit B-hyve hose timer BLE control library."""

from .constants import MAX_TIMER_PORTS, MAX_TIMER_STATION_ID, VALID_TIMER_PORT_COUNTS
from .gen2_codec import merge_gen2_decoded

__version__ = "0.1.0"

__all__ = [
    "MAX_TIMER_PORTS",
    "MAX_TIMER_STATION_ID",
    "VALID_TIMER_PORT_COUNTS",
    "__version__",
    "merge_gen2_decoded",
]
