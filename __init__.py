"""Obot voice pipeline skeleton."""

from .actions import default_action_registry
from .controller import DemoObotController, ObotController
from .llm_client import ScriptedLLMClient
from .orchestrator import RobotPipeline
from .processor import StreamProcessor

__all__ = [
    "DemoObotController",
    "ObotController",
    "RobotPipeline",
    "ScriptedLLMClient",
    "StreamProcessor",
    "default_action_registry",
]
