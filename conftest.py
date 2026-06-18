"""Conftest raiz — garante que dependências C ausentes não quebrem testes."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

# pyodbc é uma extensão C que requer libodbc.so.2 em runtime.
# No CI (Jenkins) a lib nativa pode não estar instalada; como os
# testes sempre mocam pyodbc.connect, basta injetar um módulo fake
# para que o patch funcione sem a biblioteca real.
if "pyodbc" not in sys.modules:
    try:
        import pyodbc  # noqa: F401
    except ImportError:
        _fake = ModuleType("pyodbc")
        _fake.connect = MagicMock()  # type: ignore[attr-defined]
        _fake.Error = Exception  # type: ignore[attr-defined]
        sys.modules["pyodbc"] = _fake
