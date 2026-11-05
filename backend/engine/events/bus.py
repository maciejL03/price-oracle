"""Direct, in-process asynchronous event dispatch."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeAlias, TypeVar, cast

from .models import EngineEvent

logger = logging.getLogger(__name__)

EventT = TypeVar("EventT", bound=EngineEvent)
EventHandler: TypeAlias = Callable[[EventT], Awaitable[None]]
_StoredHandler: TypeAlias = Callable[[EngineEvent], Awaitable[None]]


class EventBus:
    """Dispatch typed events to registered async handlers in registration order.

    The bus intentionally owns neither a queue nor background tasks. Awaiting
    :meth:`publish` invokes handlers directly, so a slow handler delays the
    publisher and a completed call means every handler completed successfully.
    """

    def __init__(self) -> None:
        self._subscribers: dict[type[EngineEvent], list[_StoredHandler]] = {}

    def subscribe(self, event_type: type[EventT], handler: EventHandler[EventT]) -> bool:
        """Register an async handler, returning ``False`` when already present."""
        self._validate_event_type(event_type)
        if not callable(handler):
            raise TypeError("handler must be callable")

        handlers = self._subscribers.setdefault(event_type, [])
        stored_handler = cast(_StoredHandler, handler)
        if stored_handler in handlers:
            return False
        handlers.append(stored_handler)
        return True

    def unsubscribe(
        self, event_type: type[EventT], handler: EventHandler[EventT]
    ) -> bool:
        """Remove a handler, returning whether a registration was removed."""
        self._validate_event_type(event_type)
        stored_handler = cast(_StoredHandler, handler)
        handlers = self._subscribers.get(event_type)
        if handlers is None:
            return False
        try:
            handlers.remove(stored_handler)
        except ValueError:
            return False
        if not handlers:
            del self._subscribers[event_type]
        return True

    async def publish(self, event: EngineEvent) -> None:
        """Deliver one event to its exact-type subscribers.

        A snapshot makes subscription changes made by a running handler take
        effect on the next publish. Non-cancellation failures are logged and
        collected so later handlers still run, then reported as an
        :class:`ExceptionGroup`. Cancellation always propagates immediately.
        """
        if not isinstance(event, EngineEvent):
            raise TypeError("event must be an EngineEvent")

        handlers = tuple(self._subscribers.get(type(event), ()))
        failures: list[Exception] = []
        for handler in handlers:
            try:
                await handler(event)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception(
                    "Event subscriber failed: event_type=%s handler=%s",
                    type(event).__name__,
                    _handler_name(handler),
                )
                failures.append(error)

        if failures:
            raise ExceptionGroup(
                f"{len(failures)} subscriber failure(s) while publishing "
                f"{type(event).__name__}",
                failures,
            )

    @staticmethod
    def _validate_event_type(event_type: object) -> None:
        if not isinstance(event_type, type) or not issubclass(event_type, EngineEvent):
            raise TypeError("event_type must be an EngineEvent subclass")


def _handler_name(handler: _StoredHandler) -> str:
    return getattr(handler, "__qualname__", repr(handler))
