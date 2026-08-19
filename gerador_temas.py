"""
gerador_temas.py
-----------------
Parte 1 da automação de blog com IA.

Responsabilidade deste arquivo (só isso, nada além):
  1. Montar um prompt pronto (o usuário não precisa digitar nada) pedindo
     N ideias de temas/títulos de cauda longa e MUITO específicos (micro-nicho),
     usando o domínio/identidade definidos em config/blog.yaml.
  2. Enviar esse prompt pra API da Groq.
  3. Devolver e exibir a lista de ideias já formatada, além de salvar
     em um .json pra ser consumido pela próxima etapa da automação
     (a que vai escrever as matérias).

Este script não sabe nada sobre nenhum nicho específico — o assunto do
blog, o tom e o vocabulário vêm de config/blog.yaml. Editando esse
arquivo, esse mesmo código gera pautas pra qualquer outro blog.

Como rodar:
  1. pip install -r requirements.txt
  2. Copie .env.example para .env e preencha GROQ_API_KEY
     (pegue em https://console.groq.com/keys)
  3. Edite config/blog.yaml com a identidade do seu blog
  4. python gerador_temas.py
  5. python gerador_temas.py --quantidade 10 --saida saidas/lote_especial.json

Pipeline completo (nesta ordem):
  1. gerador_temas.py        -> ideias
  2. escritor_materias.py    -> artigos em materias/*.md
  3. gerador_imagem.py       -> imagem de destaque em imagens/*.jpg
  4. publicador_wordpress.py -> publica texto + imagem no WordPress
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .comum import (
    CAMINHO_CONFIG_BLOG_PADRAO,
    ConfigBlog,
    ErroConfiguracao,
    ErroGroq,
    carregar_config_blog,
    chamar_groq_json,
    configurar_logging,
    escrever_arquivo_atomico,
    validar_chave,
)

load_dotenv()
logger = configurar_logging("gerador_temas")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

QUANTIDADE_IDEIAS_PADRAO = 20

PASTA_SAIDAS = Path("saidas")
PASTA_CONFIG = Path("config")
ARQUIVO_TEMAS_PUBLICADOS = PASTA_CONFIG / "temas_publicados.json"


# --------------------------------------------------------------------------- #
# Modelo de dados
# --------------------------------------------------------------------------- #

@dataclass
class Ideia:
    numero: int
    vertente: str
    titulo: str
    micro_nicho: str
    intencao_busca: str

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> "Ideia":
        return cls(
            numero=d.get("numero", 0),
            vertente=d.get("vertente", ""),
            titulo=d.get("titulo", ""),
            micro_nicho=d.get("microNicho", ""),
            intencao_busca=d.get("intencaoBusca", ""),
        )


@dataclass
class ResultadoIdeias:
    ideias: list[Ideia] = field(default_factory=list)

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> "ResultadoIdeias":
        return cls(ideias=[Ideia.de_dict(i) for i in d.get("ideias", [])])


# --------------------------------------------------------------------------- #
# Prompt — montado a partir de config/blog.yaml, não hardcoded no código
# --------------------------------------------------------------------------- #

def montar_system_prompt(config: ConfigBlog) -> str:
    return f"""Você é um estrategista de SEO e conteúdo especializado em blogs de \
{config.descricao_curta} ({config.dominio_extendido}). Você entende profundamente de \
{config.termos_especificos or config.dominio}, e das dúvidas reais que praticantes e \
curiosos pesquisam no Google.

Sua tarefa é gerar ideias de artigo de CAUDA LONGA e MICRO-NICHO: nada de temas amplos \
ou genéricos. Cada ideia precisa resolver uma dúvida MUITO específica, do tipo que uma \
pessoa realmente digitaria no Google quando está no meio de uma situação concreta \
(um problema, uma dúvida pontual, uma preocupação prática do dia a dia dentro do tema), \
nunca um resumo enciclopédico do assunto.

Regras obrigatórias para cada ideia:
- O título deve ser de cauda longa (long-tail), no formato de pergunta ou situação real \
de busca, com 8 a 14 palavras.
- O tema deve ser hiperespecífico: um micro-nicho dentro de {config.dominio}.
- Proibido repetir o mesmo tema geral que já apareceu na lista de artigos já publicados \
(ela será fornecida abaixo, quando existir).
- Proibido gerar títulos genéricos como "o que é X", "história de Y", "guia completo de Z".
- Cada ideia deve indicar claramente a categoria/vertente dentro de {config.dominio_extendido}.
- Escreva tudo em {config.idioma}.

Você deve responder APENAS com um JSON válido, sem nenhum texto antes ou depois, no formato:

{{
  "ideias": [
    {{
      "numero": 1,
      "vertente": "categoria dentro de {config.dominio_extendido}",
      "titulo": "título de cauda longa, específico, pronto pra usar como H1 do artigo",
      "microNicho": "explicação curta (1 frase) de qual dúvida/situação específica esse artigo resolve",
      "intencaoBusca": "informacional | transacional | comparativa"
    }}
  ]
}}
"""


def montar_prompt_usuario(quantidade: int, temas_publicados: list[str], config: ConfigBlog) -> str:
    temas_formatados = "\n".join(f"- {t}" for t in temas_publicados)
    return (
        f'Gere {quantidade} ideias novas de artigo para o blog "{config.nome}", '
        "seguindo todas as regras do system prompt.\n\n"
        "Temas/artigos já publicados no blog (NÃO repita esses assuntos, gere coisas diferentes):\n"
        f"{temas_formatados or '(nenhum registrado ainda)'}\n\n"
        "Responda somente com o JSON pedido."
    )


# --------------------------------------------------------------------------- #
# Temas já publicados (arquivo de config editável, não hardcoded no código)
# --------------------------------------------------------------------------- #

def carregar_temas_publicados() -> list[str]:
    if ARQUIVO_TEMAS_PUBLICADOS.exists():
        try:
            with ARQUIVO_TEMAS_PUBLICADOS.open("r", encoding="utf-8") as f:
                dados = json.load(f)
            temas = dados.get("temas", [])
            if isinstance(temas, list) and temas:
                return [str(t) for t in temas]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "Falha ao ler %s (%s) — nenhum tema anterior foi considerado.",
                ARQUIVO_TEMAS_PUBLICADOS, e,
            )
    return []


# --------------------------------------------------------------------------- #
# Chamada à Groq
# --------------------------------------------------------------------------- #

def gerar_ideias(quantidade: int, temas_publicados: list[str], config: ConfigBlog) -> ResultadoIdeias:
    bruto = chamar_groq_json(
        montar_system_prompt(config),
        montar_prompt_usuario(quantidade, temas_publicados, config),
        api_key=GROQ_API_KEY, modelo=GROQ_MODEL,
        temperatura=0.9, max_tokens=min(150 * quantidade, 8000),
    )
    return ResultadoIdeias.de_dict(bruto)


# --------------------------------------------------------------------------- #
# Saída (exibição e persistência)
# --------------------------------------------------------------------------- #

def exibir_ideias(resultado: ResultadoIdeias, config: ConfigBlog) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {len(resultado.ideias)} IDEIAS DE MATÉRIA — {config.nome.upper()}")
    print(f"{'=' * 70}\n")
    for ideia in resultado.ideias:
        print(f"[{ideia.numero}] ({ideia.vertente}) {ideia.titulo}")
        print(f"     Micro-nicho: {ideia.micro_nicho}")
        print(f"     Intenção de busca: {ideia.intencao_busca}\n")


def salvar_ideias(resultado: ResultadoIdeias, caminho_saida: Path | None = None) -> Path:
    if caminho_saida is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        caminho_saida = PASTA_SAIDAS / f"ideias_temas_{timestamp}.json"

    dados = {
        "ideias": [
            {
                "numero": i.numero,
                "vertente": i.vertente,
                "titulo": i.titulo,
                "microNicho": i.micro_nicho,
                "intencaoBusca": i.intencao_busca,
            }
            for i in resultado.ideias
        ]
    }
    escrever_arquivo_atomico(caminho_saida, json.dumps(dados, ensure_ascii=False, indent=2))
    return caminho_saida


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera ideias de matéria de cauda longa a partir de config/blog.yaml."
    )
    parser.add_argument(
        "--quantidade", type=int, default=QUANTIDADE_IDEIAS_PADRAO,
        help=f"Quantas ideias gerar (padrão: {QUANTIDADE_IDEIAS_PADRAO}).",
    )
    parser.add_argument(
        "--saida", type=str, default=None,
        help="Caminho do .json de saída. Se omitido, gera um nome com timestamp em saidas/.",
    )
    parser.add_argument(
        "--config", type=str, default=str(CAMINHO_CONFIG_BLOG_PADRAO),
        help=f"Caminho do config/blog.yaml (padrão: {CAMINHO_CONFIG_BLOG_PADRAO}).",
    )
    args = parser.parse_args(argv)

    if args.quantidade <= 0:
        parser.error("--quantidade precisa ser um número positivo.")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        validar_chave(GROQ_API_KEY, "GROQ_API_KEY", prefixo_esperado="gsk_")
        config = carregar_config_blog(Path(args.config))
        temas_publicados = carregar_temas_publicados()
        resultado = gerar_ideias(args.quantidade, temas_publicados, config)
    except ErroConfiguracao as e:
        logger.error(str(e))
        return 1
    except ErroGroq as e:
        logger.error(str(e))
        return 1

    if not resultado.ideias:
        logger.error("A Groq respondeu sem nenhuma ideia. Tente rodar de novo.")
        return 1

    exibir_ideias(resultado, config)
    caminho_saida = Path(args.saida) if args.saida else None
    caminho_salvo = salvar_ideias(resultado, caminho_saida)

    logger.info("Ideias salvas em: %s", caminho_salvo)
    logger.info("(esse .json vai ser usado depois pela próxima etapa, que escreve as matérias)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
