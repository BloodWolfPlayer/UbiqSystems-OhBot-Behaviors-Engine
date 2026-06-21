"""Obot voice pipeline skeleton."""

from .core.orchestrator import RobotPipeline
from .core.processor import StreamProcessor
from .llm.client import ScriptedLLMClient
from .robot.actions import default_action_registry
from .robot.controller import (
    ConsoleObotController,
    DemoObotController,
    HardwareObotController,
    ObotController,
)

__all__ = [
    "ConsoleObotController",
    "DemoObotController",
    "HardwareObotController",
    "ObotController",
    "RobotPipeline",
    "ScriptedLLMClient",
    "StreamProcessor",
    "default_action_registry",
]
