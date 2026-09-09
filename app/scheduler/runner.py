"""Один цикл в процессе; остановка дожидается текущего короткого tick."""

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


async def run_scheduler(engine, stop: asyncio.Event, *, interval: float = 30, evening_engine=None):
    while not stop.is_set():
        try:
            await run_in_threadpool(engine.tick, should_stop=stop.is_set)
            if evening_engine is not None and not stop.is_set():
                evening_engine.sender = engine.sender
                await run_in_threadpool(evening_engine.tick, should_stop=stop.is_set)
        except Exception:
            # Ошибки адаптера/SQL не должны раскрывать секреты или останавливать web.
            logger.error("Цикл напоминаний завершился ошибкой; следующая проверка по расписанию")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
