"""配置加载器——全局单例，统一加载所有 YAML 配置文件。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml


class ConfigLoader:
    """单例模式的配置加载器。所有 skill 通过此类读取配置，不硬编码任何业务规则。"""

    _instance: Optional[ConfigLoader] = None
    _config: dict[str, Any] = {}

    CONFIG_FILES = {
        "policy": "policy.yaml",
        "city_mapping": "city_mapping.yaml",
        "approval_flow": "approval_flow.yaml",
        "expense_types": "expense_types.yaml",
        "workflow": "workflow.yaml",
    }

    def __new__(cls, config_dir: Optional[str] = None) -> ConfigLoader:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def load(self, config_dir: Optional[str] = None) -> None:
        """加载所有配置文件。"""
        if config_dir is None:
            config_dir = str(Path(__file__).parent)

        for key, filename in self.CONFIG_FILES.items():
            filepath = os.path.join(config_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                self._config[key] = yaml.safe_load(f)

        self._loaded = True

    def get(self, key: str) -> dict:
        """获取某个配置文件的内容。"""
        if not self._loaded:
            self.load()
        return self._config.get(key, {})

    def get_all(self) -> dict[str, Any]:
        """获取所有配置。"""
        if not self._loaded:
            self.load()
        return dict(self._config)

    @classmethod
    def reset(cls) -> None:
        """重置单例（仅用于测试）。"""
        cls._instance = None
        cls._config = {}
