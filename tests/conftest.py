"""
Configuração compartilhada dos testes.

O projeto usa o layout `src/` (código em `src/imperio_dos_sete/`), então
adicionamos essa pasta ao sys.path aqui em vez de exigir uma instalação
formal do pacote (`pip install -e .`) só pra rodar `pytest`.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
PASTA_SRC = RAIZ_PROJETO / "src"

if str(PASTA_SRC) not in sys.path:
    sys.path.insert(0, str(PASTA_SRC))
