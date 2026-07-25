from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger("codeduck.measure")


@contextmanager
def log_duration(stage: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    logger.info("%s: %.3f с", stage, time.perf_counter() - start)
