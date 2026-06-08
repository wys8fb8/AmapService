import os
from pathlib import Path
from typing import Mapping, Optional

import yaml

from .schema import AppConfig

ENV_PREFIX = "AMAP__"


def _apply_env_overrides(data: dict, environ: Mapping[str, str]) -> dict:
    """Override config values from AMAP__SECTION__KEY env vars (double-underscore = nesting)."""
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().split("__")
        if not path or path[-1] == "":
            continue
        node = data
        ok = True
        for part in path[:-1]:
            child = node.get(part)
            if child is None:
                child = {}
                node[part] = child
            if not isinstance(child, dict):
                ok = False
                break
            node = child
        if ok:
            node[path[-1]] = value
    return data


def load_config(path, environ: Optional[Mapping[str, str]] = None) -> AppConfig:
    environ = os.environ if environ is None else environ
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    raw = _apply_env_overrides(raw, environ)
    return AppConfig.model_validate(raw)
