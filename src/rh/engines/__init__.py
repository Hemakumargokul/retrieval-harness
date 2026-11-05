from __future__ import annotations

from rh.config import Config
from rh.engines.base import Engine
from rh.engines.cbm import CbmEngine
from rh.engines.colgrep import ColgrepEngine
from rh.engines.ripgrep import RipgrepEngine


def get_engines(cfg: Config) -> list[Engine]:
    registry = {
        "ripgrep": lambda: RipgrepEngine(),
        "colgrep": lambda: ColgrepEngine(k=cfg.top_k + 2, update=cfg.colgrep_update),
        "cbm": lambda: CbmEngine(),
    }
    return [registry[n]() for n in cfg.engines if n in registry]
