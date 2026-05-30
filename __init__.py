"""Obot voice pipeline skeleton."""

from .actions import default_action_registry
from .controller import (
    ConsoleObotController,
    DemoObotController,
    HardwareObotController,
    ObotController,
)
from .llm_client import ScriptedLLMClient
from .orchestrator import RobotPipeline
from .processor import StreamProcessor

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
