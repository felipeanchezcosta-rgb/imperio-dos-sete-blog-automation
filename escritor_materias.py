"""
escritor_materias.py
---------------------
Parte 2 da automação de blog com IA.

Responsabilidade deste arquivo (só isso, nada além):
  1. Ler o .json de ideias gerado pelo gerador_temas.py (pega automaticamente
     o mais recente da pasta saidas/, ou você aponta um específico).
  2. Você escolhe quais números da lista quer escrever (ex: 1 e 7).
  3. Pra cada ideia escolhida, monta um prompt seguindo o padrão editorial
     definido em config/blog.yaml e manda pra Groq escrever a matéria completa.
  4. Salva cada matéria como um .md separado na pasta materias/, com bloco
     de metadados (título, meta description, slug, palavra-chave foco, tags)
     no topo, pronta pra geração de imagem e publicação.

Este script não sabe nada sobre nenhum nicho específico — o nome do blog,
tom de voz, autores de referência e o CTA final vêm de config/blog.yaml.

Este arquivo NÃO publica nada no WordPress e NÃO gera imagem — só o texto.

Como rodar:
  python escritor_materias.py
  python escritor_materias.py --arquivo saidas/ideias_temas_20260803_120000.json
  python escritor_materias.py --indices 1,7
  python escritor_materias.py --indices 1,2,3,4 --paralelo 3   # mais rápido
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

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
    slugificar,
    validar_chave,
)

load_dotenv()
logger = configurar_logging("escritor_materias")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

PASTA_IDEIAS = Path("saidas")
PASTA_MATERIAS = Path("materias")

# Margem de segurança abaixo do mínimo de palavras pedido no prompt
# editorial (config.min_palavras) — serve só pra avisar se a Groq entregou
# um artigo bem mais curto que o combinado, não pra rejeitar automaticamente.
FATOR_PALAVRAS_MINIMAS = 0.9

_trava_slugs = threading.Lock()  # protege slugs_usados no modo --paralelo


# --------------------------------------------------------------------------- #
# Prompt editorial — montado a partir de config/blog.yaml
# --------------------------------------------------------------------------- #

def montar_system_prompt(config: ConfigBlog) -> str:
    if config.autores_referencia:
        autores = ", ".join(config.autores_referencia)
        linha_autores = (
            f"Cite, quando fizer sentido no contexto, autores de referência como {autores}, "
            "sempre de forma natural e correta, nunca inventando obra ou dado que não seja "
            "de conhecimento amplamente aceito. Se não tiver certeza de um dado específico "
            "(ano, título exato de livro), fale de forma mais genérica em vez de inventar."
        )
    else:
        linha_autores = (
            "Cite, quando fizer sentido, fontes ou referências reconhecidas do tema, sem "
            "inventar dados que você não tenha certeza."
        )

    return f"""Você é um escritor experiente de {config.dominio}, que escreve para o blog \
"{config.nome}". Você escreve sempre em primeira pessoa, como {config.voz_primeira_pessoa}.

Siga RIGOROSAMENTE este padrão editorial em todo artigo que escrever:

1. ESTRUTURA
   - H1 com o título fornecido (ou uma variação natural dele).
   - Introdução em primeira pessoa que contextualiza a vivência antes de entrar no tema.
   - Subtítulos (H2/H3) no formato de pergunta de cauda longa sempre que fizer sentido,
     pensando em como as pessoas buscam isso no Google.
   - Conclusão com reflexão pessoal, sem ser genérica.
   - O artigo completo (corpo_markdown) deve ter NO MÍNIMO {config.min_palavras} palavras. \
Desenvolva cada seção com profundidade real (exemplos práticos, contexto, nuances), nunca \
enchendo linguiça só pra bater a contagem.

2. E-E-A-T (credibilidade)
   - {linha_autores}

3. BLOCKQUOTES — use os três tipos abaixo ao longo do texto (pelo menos um de cada),
   no formato markdown de citação:
   > **Fundamento:** (uma explicação de fundamento/tradição ligada ao trecho)
   > **Ponto de Atenção:** (um alerta prático ou cuidado que o leitor precisa ter)
   > **Dica Espiritual:** (uma orientação prática aplicável no dia a dia)

4. FAQ
   - Ao final do corpo do artigo, antes da conclusão, inclua uma seção "## Perguntas Frequentes"
     com 4 a 6 perguntas no formato de cauda longa (do jeito que as pessoas buscam no Google),
     cada uma com resposta objetiva de 2 a 4 frases, pensada para aparecer em featured snippet.

5. CTA
   - Termine o artigo com uma chamada para ação {config.cta_texto or "convidando o leitor a entrar em contato"}.
   - Logo abaixo do texto do CTA, em uma linha própria, inclua exatamente este link markdown,
     sem alterar nada nele:
     {config.cta_link_markdown or "(nenhum link de contato configurado em config/blog.yaml)"}
   - NUNCA inclua ou sugira uma seção de comentários.
   - NUNCA use marcações de link interno como [[LINK_INTERNO: ...]] em nenhum lugar do texto.

6. SEO
   - Use a palavra-chave foco (o próprio título, de forma natural) no primeiro parágrafo,
     em pelo menos um H2, e na conclusão.
   - Hierarquia de headings correta (um único H1, H2 para seções principais, H3 quando
     precisar de subdivisão).

7. TOM
   - Escreva em {config.idioma}. {config.tom_voz}

FORMATO DE SAÍDA — responda APENAS com um JSON válido, sem texto antes ou depois, neste formato:

{{
  "meta_titulo_seo": "título otimizado pra SEO, até 60 caracteres",
  "meta_descricao": "meta description otimizada, até 155 caracteres, com a palavra-chave foco",
  "palavra_chave_foco": "palavra-chave principal do artigo",
  "slug": "slug-em-kebab-case-sem-acento",
  "tags": ["tag1", "tag2", "tag3"],
  "corpo_markdown": "o artigo inteiro em markdown, seguindo todas as regras acima"
}}
"""


USER_PROMPT_TEMPLATE = """Escreva o artigo completo com base nesta ideia:

Vertente: {vertente}
Título: {titulo}
Micro-nicho / dúvida específica a resolver: {micro_nicho}
Intenção de busca: {intencao_busca}

Responda somente com o JSON pedido no formato especificado."""


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
    def de_dict(cls, d: dict) -> "Ideia":
        return cls(
            numero=d.get("numero", 0),
            vertente=d.get("vertente", ""),
            titulo=d.get("titulo", ""),
            micro_nicho=d.get("microNicho", ""),
            intencao_busca=d.get("intencaoBusca", ""),
        )


# --------------------------------------------------------------------------- #
# Leitura das ideias
# --------------------------------------------------------------------------- #

def encontrar_arquivo_ideias_mais_recente() -> Path:
    arquivos = sorted(glob.glob(str(PASTA_IDEIAS / "ideias_temas_*.json")))
    if not arquivos:
        raise ErroConfiguracao(
            f"Nenhum arquivo de ideias encontrado em {PASTA_IDEIAS}/. Rode o gerador_temas.py primeiro."
        )
    return Path(arquivos[-1])


def carregar_ideias(caminho: Path) -> list[Ideia]:
    with open(caminho, "r", encoding="utf-8") as f:
        dados = json.load(f)
    return [Ideia.de_dict(d) for d in dados.get("ideias", [])]


def escolher_indices(argumento: Optional[str], ideias: list[Ideia]) -> list[int]:
    numeros_validos = {ideia.numero for ideia in ideias}

    if argumento:
        bruto = argumento
    else:
        print("\nIdeias disponíveis:")
        for ideia in ideias:
            print(f"  [{ideia.numero}] ({ideia.vertente}) {ideia.titulo}")
        bruto = input("\nDigite os números das matérias que quer escrever (separados por vírgula): ")

    try:
        indices = [int(x.strip()) for x in bruto.split(",") if x.strip()]
    except ValueError:
        # Antes isso derrubava o script com um traceback cru. Agora vira um
        # erro claro, tratável, que o usuário entende de primeira.
        raise ErroConfiguracao(
            f"'{bruto}' contém valor não numérico. Use números separados por vírgula, ex: 1,7"
        )

    if not indices:
        raise ErroConfiguracao("Nenhum número informado.")

    invalidos = [i for i in indices if i not in numeros_validos]
    if invalidos:
        raise ErroConfiguracao(f"Número(s) inválido(s): {invalidos}")

    return indices


# --------------------------------------------------------------------------- #
# Geração da matéria
# --------------------------------------------------------------------------- #

def remover_placeholders_link_interno(texto: str) -> str:
    """Rede de segurança: remove qualquer [[LINK_INTERNO: ...]] que o modelo
    ainda tenha gerado, mesmo com a instrução pra não usar mais isso."""
    return re.sub(r"\[\[LINK_INTERNO:.*?\]\]", "", texto)


def contar_palavras(texto: str) -> int:
    return len(texto.split())


def montar_markdown_final(artigo: dict, slug_final: str) -> str:
    tags = artigo.get("tags", [])
    frontmatter = (
        "---\n"
        f"titulo_seo: \"{artigo.get('meta_titulo_seo', '')}\"\n"
        f"meta_descricao: \"{artigo.get('meta_descricao', '')}\"\n"
        f"palavra_chave_foco: \"{artigo.get('palavra_chave_foco', '')}\"\n"
        f"slug: \"{slug_final}\"\n"
        f"tags: {json.dumps(tags, ensure_ascii=False)}\n"
        "---\n\n"
    )
    corpo = remover_placeholders_link_interno(artigo.get("corpo_markdown", ""))
    return frontmatter + corpo


def _reservar_slug(slug_base: str, slugs_usados: set[str]) -> str:
    """Garante que duas ideias não gerem o mesmo slug na mesma execução
    (ex: dois títulos parecidos que a Groq slugifica igual). Protegido por
    lock porque no modo --paralelo várias threads chamam isso ao mesmo tempo."""
    with _trava_slugs:
        slug_final = slug_base
        contador = 2
        while slug_final in slugs_usados:
            slug_final = f"{slug_base}-{contador}"
            contador += 1
        slugs_usados.add(slug_final)
        return slug_final


def gerar_materia(ideia: Ideia, slugs_usados: set[str], config: ConfigBlog) -> Path:
    logger.info("Escrevendo matéria [%s] %s ...", ideia.numero, ideia.titulo)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        vertente=ideia.vertente,
        titulo=ideia.titulo,
        micro_nicho=ideia.micro_nicho,
        intencao_busca=ideia.intencao_busca,
    )
    artigo = chamar_groq_json(
        montar_system_prompt(config), user_prompt,
        api_key=GROQ_API_KEY, modelo=GROQ_MODEL,
        temperatura=0.85, max_tokens=8000,
    )

    corpo = artigo.get("corpo_markdown", "")
    n_palavras = contar_palavras(corpo)
    minimo_esperado = int(config.min_palavras * FATOR_PALAVRAS_MINIMAS)
    if n_palavras < minimo_esperado:
        logger.warning(
            "Matéria '%s' saiu com só %d palavras (pedido: %d+). Vale conferir o resultado.",
            ideia.titulo, n_palavras, config.min_palavras,
        )

    slug_base = slugificar(artigo.get("slug") or ideia.titulo)
    slug_final = _reservar_slug(slug_base, slugs_usados)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = PASTA_MATERIAS / f"{timestamp}_{slug_final}.md"
    escrever_arquivo_atomico(caminho, montar_markdown_final(artigo, slug_final))

    logger.info("  -> salvo em %s (%d palavras)", caminho, n_palavras)
    return caminho


def gerar_materias(ideias_escolhidas: list[Ideia], paralelo: int, config: ConfigBlog) -> list[Path]:
    slugs_usados: set[str] = set()
    caminhos: list[Path] = []

    if paralelo <= 1 or len(ideias_escolhidas) == 1:
        for ideia in ideias_escolhidas:
            try:
                caminhos.append(gerar_materia(ideia, slugs_usados, config))
            except ErroGroq as e:
                logger.error("Falha ao gerar matéria [%s] %s: %s", ideia.numero, ideia.titulo, e)
        return caminhos

    # Modo paralelo: bem mais rápido em lotes grandes, mas cuidado — se sua
    # conta Groq tiver um limite baixo de requisições/minuto, prefira rodar
    # sequencial (--paralelo 1, o padrão) pra não ficar só levando 429.
    logger.info("Gerando %d matérias em paralelo (%d por vez) ...", len(ideias_escolhidas), paralelo)
    with ThreadPoolExecutor(max_workers=paralelo) as executor:
        futuros = {
            executor.submit(gerar_materia, ideia, slugs_usados, config): ideia
            for ideia in ideias_escolhidas
        }
        for futuro in as_completed(futuros):
            ideia = futuros[futuro]
            try:
                caminhos.append(futuro.result())
            except ErroGroq as e:
                logger.error("Falha ao gerar matéria [%s] %s: %s", ideia.numero, ideia.titulo, e)

    return caminhos


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera matérias completas a partir das ideias do gerador_temas.py"
    )
    parser.add_argument(
        "--arquivo", type=str, default=None,
        help="Caminho do .json de ideias. Se omitido, pega o mais recente em saidas/.",
    )
    parser.add_argument(
        "--indices", type=str, default=None,
        help="Números das ideias a escrever, separados por vírgula (ex: 1,7). "
             "Se omitido, pergunta interativamente.",
    )
    parser.add_argument(
        "--paralelo", type=int, default=1,
        help="Quantas matérias escrever ao mesmo tempo (padrão: 1, sequencial). "
             "Valores maiores geram mais rápido, mas podem esbarrar em rate limit da Groq.",
    )
    parser.add_argument(
        "--config", type=str, default=str(CAMINHO_CONFIG_BLOG_PADRAO),
        help=f"Caminho do config/blog.yaml (padrão: {CAMINHO_CONFIG_BLOG_PADRAO}).",
    )
    args = parser.parse_args(argv)

    if args.paralelo < 1:
        parser.error("--paralelo precisa ser >= 1")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        validar_chave(GROQ_API_KEY, "GROQ_API_KEY", prefixo_esperado="gsk_")
        config = carregar_config_blog(Path(args.config))
        caminho_ideias = Path(args.arquivo) if args.arquivo else encontrar_arquivo_ideias_mais_recente()
        ideias = carregar_ideias(caminho_ideias)
        indices_escolhidos = escolher_indices(args.indices, ideias)
    except ErroConfiguracao as e:
        logger.error(str(e))
        return 1
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Falha ao ler o arquivo de ideias: %s", e)
        return 1

    ideias_por_numero = {ideia.numero: ideia for ideia in ideias}
    ideias_escolhidas = [ideias_por_numero[n] for n in indices_escolhidos]

    PASTA_MATERIAS.mkdir(exist_ok=True)
    caminhos_gerados = gerar_materias(ideias_escolhidas, args.paralelo, config)

    if not caminhos_gerados:
        logger.error("Nenhuma matéria foi gerada com sucesso.")
        return 1

    logger.info("%d matéria(s) gerada(s) com sucesso:", len(caminhos_gerados))
    for c in caminhos_gerados:
        logger.info("  - %s", c)

    if len(caminhos_gerados) < len(ideias_escolhidas):
        logger.warning(
            "%d de %d matérias falharam — confira os erros acima.",
            len(ideias_escolhidas) - len(caminhos_gerados), len(ideias_escolhidas),
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
