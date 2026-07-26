from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger('codeduck.measure')


@contextmanager
def log_duration(stage: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    logger.info('%s: %.3f с', stage, time.perf_counter() - start)
