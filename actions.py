from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

from controller import ObotController

ActionHandler: TypeAlias = Callable[[ObotController], Awaitable[None] | None]


class ActionRegistry:
    """Maps action tags to robot motion functions."""

    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, name: str) -> Callable[[ActionHandler], ActionHandler]:
        def decorator(handler: ActionHandler) -> ActionHandler:
            self._handlers[name] = handler
            return handler

        return decorator

    async def execute(self, name: str, controller: ObotController) -> None:
        handler = self._handlers.get(name)
        if handler is None:
            print(f"[action] unknown: {name}")
            return

        result = handler(controller)
        if result is not None:
            await result


def default_action_registry() -> ActionRegistry:
    registry = ActionRegistry()

    @registry.register("nod")
    async def _nod(controller: ObotController) -> None:
        await controller.nod()

    @registry.register("wave")
    async def _wave(controller: ObotController) -> None:
        await controller.wave()

    @registry.register("look_left")
    async def _look_left(controller: ObotController) -> None:
        await controller.look_left()

    @registry.register("look_right")
    async def _look_right(controller: ObotController) -> None:
        await controller.look_right()

    return registry
