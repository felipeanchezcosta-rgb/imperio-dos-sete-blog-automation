"""
imperio_dos_sete
-----------------
Automação de blog com IA (Groq) em 4 etapas: geração de pautas, escrita de
artigos, geração de imagem de destaque e publicação no WordPress.

Cada etapa é um módulo independente, orquestrado externamente por scripts
de linha de comando (veja o README). O módulo `comum` concentra tudo que é
compartilhado entre as 4 etapas: cliente HTTP com retry, configuração do
blog (config/blog.yaml), parsing de frontmatter e utilidades de arquivo.

Uso típico (via linha de comando, na raiz do projeto):

    python -m imperio_dos_sete.gerador_temas
    python -m imperio_dos_sete.escritor_materias --indices 1,7
    python -m imperio_dos_sete.gerador_imagem
    python -m imperio_dos_sete.publicador_wordpress --pasta materias
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
