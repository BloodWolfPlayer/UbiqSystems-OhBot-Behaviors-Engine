from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILENAME = "config.json"
EXAMPLE_FILENAME = "config.example.json"
#* Hard cap on how many "recent models" we remember per backend.
RECENTS_CAP = 3


@dataclass
class OllamaSSHConfig:
    host: str = ""
    port: int = 22
    user: str = ""
    key_path: str = ""
    remote_ollama_host: str = "localhost"
    remote_ollama_port: int = 11434


@dataclass
class Config:
    gemini_api_key: str = ""
    ollama_ssh: OllamaSSHConfig = field(default_factory=OllamaSSHConfig)
    recent_gemini_models: list[str] = field(default_factory=list)
    recent_ollama_models: list[str] = field(default_factory=list)

    _path: Path | None = field(default=None, repr=False, compare=False)

    def record_gemini_model(self, model: str) -> None:
        self.recent_gemini_models = _bump(self.recent_gemini_models, model)
        self.save()

    def record_ollama_model(self, model: str) -> None:
        self.recent_ollama_models = _bump(self.recent_ollama_models, model)
        self.save()

    def save(self) -> None:
        if self._path is None:
            return
        payload = {
            "gemini_api_key": self.gemini_api_key,
            "ollama_ssh": asdict(self.ollama_ssh),
            "recent_gemini_models": self.recent_gemini_models,
            "recent_ollama_models": self.recent_ollama_models,
        }
        #! Atomic write: write to a temp file in the same directory then rename.
        #! Protects the config from being half written if the process is killed mid save.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


def _bump(recents: list[str], model: str) -> list[str]:
    #* MRU ordering: drop the model if it already appears, then put it at the front.
    #* The cap keeps the list at three so the quick access menu never grows.
    deduped = [m for m in recents if m != model]
    return ([model] + deduped)[:RECENTS_CAP]


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def load_config() -> Config:
    root = _project_root()
    path = root / CONFIG_FILENAME
    if not path.exists():
        example = root / EXAMPLE_FILENAME
        raise FileNotFoundError(
            f"Missing {CONFIG_FILENAME}. Copy {example.name} to {path.name} and fill in your credentials."
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    ssh_data = data.get("ollama_ssh") or {}
    cfg = Config(
        gemini_api_key=data.get("gemini_api_key", ""),
        ollama_ssh=OllamaSSHConfig(
            host=ssh_data.get("host", ""),
            port=int(ssh_data.get("port", 22)),
            user=ssh_data.get("user", ""),
            key_path=ssh_data.get("key_path", ""),
            remote_ollama_host=ssh_data.get("remote_ollama_host", "localhost"),
            remote_ollama_port=int(ssh_data.get("remote_ollama_port", 11434)),
        ),
        recent_gemini_models=list(data.get("recent_gemini_models", [])),
        recent_ollama_models=list(data.get("recent_ollama_models", [])),
    )
    cfg._path = path
    return cfg
