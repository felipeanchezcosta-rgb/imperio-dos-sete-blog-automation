"""
comum.py
--------
Funções e classes compartilhadas pelos 4 scripts da automação de blog com IA
(gerador_temas, escritor_materias, gerador_imagem, publicador_wordpress).

Antes, cada script tinha sua própria cópia de: parsing de frontmatter,
chamada HTTP pra Groq, slugify, validação de chave de API... Centralizar
aqui evita que uma correção de bug precise ser replicada em 4 lugares.

Este módulo é o "motor": não sabe nada sobre nenhum nicho de blog
específico. Tudo que é identidade de blog (nome, domínio, tom de voz,
autores de referência, CTA, estilo visual das imagens) vem de
config/blog.yaml — é editando esse arquivo que você reaproveita esse
projeto pra qualquer outro blog, sem tocar em código.

Este arquivo não faz nada sozinho — ele é importado pelos outros scripts.
Precisa estar na mesma pasta que eles (ou em algum lugar do PYTHONPATH).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT_PADRAO = 60
MAX_TENTATIVAS = 3
GROQ_URL_PADRAO = "https://api.groq.com/openai/v1/chat/completions"


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

def configurar_logging(nome: str) -> logging.Logger:
    """Configura o formato de log uma vez só (chamadas repetidas não
    duplicam handlers) e devolve um logger com o nome do script chamador."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(nome)


# --------------------------------------------------------------------------- #
# Erros específicos — em vez de sys.exit() espalhado no meio das funções.
# Isso deixa cada função reutilizável (ex: chamável de outro script ou de
# um teste) sem derrubar o processo inteiro à força.
# --------------------------------------------------------------------------- #

class ErroConfiguracao(Exception):
    """Falta configuração obrigatória (API key, variável de ambiente etc)."""


class ErroGroq(Exception):
    """A chamada à API da Groq falhou ou devolveu algo inesperado."""


class ErroWordPress(Exception):
    """A chamada à REST API do WordPress falhou ou devolveu algo inesperado."""


# --------------------------------------------------------------------------- #
# HTTP com retry/backoff — evita falhar só por instabilidade momentânea de
# rede ou por rate limit (429) passageiro.
# --------------------------------------------------------------------------- #

def sessao_com_retry(max_tentativas: int = MAX_TENTATIVAS) -> requests.Session:
    sessao = requests.Session()
    retry = Retry(
        total=max_tentativas,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT", "PATCH"],
        raise_on_status=False,
    )
    adaptador = HTTPAdapter(max_retries=retry)
    sessao.mount("https://", adaptador)
    sessao.mount("http://", adaptador)
    return sessao


# --------------------------------------------------------------------------- #
# Validação de chaves/segredos — nunca loga o valor, só existência/prefixo.
# --------------------------------------------------------------------------- #

def validar_chave(valor: str, nome_variavel: str, prefixo_esperado: str | None = None) -> None:
    if not valor or valor.startswith("cole_"):
        raise ErroConfiguracao(
            f"{nome_variavel} não configurada. Preencha o .env "
            f"(copie .env.example se ainda não tiver um)."
        )
    if prefixo_esperado and not valor.startswith(prefixo_esperado):
        logging.getLogger(__name__).warning(
            "%s não começa com o prefixo esperado ('%s...'). Confira se copiou a chave certa.",
            nome_variavel, prefixo_esperado,
        )


# --------------------------------------------------------------------------- #
# Cliente Groq — um único lugar que sabe montar o payload, tratar erro de
# rede, erro de HTTP e resposta em formato inesperado.
# --------------------------------------------------------------------------- #

def chamar_groq(
    system_prompt: str,
    user_prompt: str,
    *,
    api_key: str,
    modelo: str,
    url: str = GROQ_URL_PADRAO,
    temperatura: float = 0.7,
    max_tokens: int = 1000,
    json_mode: bool = False,
    timeout: int = TIMEOUT_PADRAO,
) -> str:
    """Chama a Groq e devolve o conteúdo (texto) da resposta.
    Levanta ErroGroq em qualquer falha; nunca inclui a chave na exceção."""
    validar_chave(api_key, "GROQ_API_KEY", prefixo_esperado="gsk_")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperatura,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    sessao = sessao_com_retry()
    try:
        resp = sessao.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise ErroGroq(f"Falha de conexão com a Groq: {e}") from e

    if resp.status_code != 200:
        raise ErroGroq(f"Erro na API Groq ({resp.status_code}): {resp.text[:500]}")

    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise ErroGroq(f"Resposta da Groq em formato inesperado: {e}") from e


def chamar_groq_json(
    system_prompt: str,
    user_prompt: str,
    *,
    api_key: str,
    modelo: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Igual chamar_groq(), mas já espera e faz parse de um JSON de resposta."""
    conteudo = chamar_groq(
        system_prompt, user_prompt, api_key=api_key, modelo=modelo, json_mode=True, **kwargs
    ).strip()
    try:
        return json.loads(conteudo)
    except json.JSONDecodeError as e:
        raise ErroGroq(
            f"A resposta da Groq não veio em JSON válido: {e}\nConteúdo: {conteudo[:500]}"
        ) from e


# --------------------------------------------------------------------------- #
# Texto / arquivos
# --------------------------------------------------------------------------- #

def slugificar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9\s-]", "", texto).strip().lower()
    return re.sub(r"[\s_-]+", "-", texto)


def escrever_arquivo_atomico(caminho: Path, conteudo: str, encoding: str = "utf-8") -> None:
    """Escreve em um .tmp e só troca de nome no final — evita deixar um
    arquivo pela metade no disco se o processo for interrompido no meio."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    tmp.write_text(conteudo, encoding=encoding)
    tmp.replace(caminho)


# --------------------------------------------------------------------------- #
# Frontmatter das matérias (.md gerado pelo escritor_materias.py)
# --------------------------------------------------------------------------- #

@dataclass
class DadosMateria:
    titulo_seo: str
    meta_descricao: str
    palavra_chave_foco: str
    slug: str
    tags: list[str] = field(default_factory=list)
    corpo_markdown: str = ""


def carregar_frontmatter(caminho: Path) -> DadosMateria:
    texto = caminho.read_text(encoding="utf-8")

    if not texto.startswith("---"):
        raise ValueError(f"{caminho} não tem frontmatter (bloco --- no topo).")

    partes = texto.split("---", 2)
    if len(partes) < 3:
        raise ValueError(f"Frontmatter malformado em {caminho}.")

    frontmatter = yaml.safe_load(partes[1]) or {}
    corpo_markdown = partes[2].strip()
    slug = frontmatter.get("slug") or slugificar(caminho.stem)

    return DadosMateria(
        titulo_seo=frontmatter.get("titulo_seo", caminho.stem),
        meta_descricao=frontmatter.get("meta_descricao", ""),
        palavra_chave_foco=frontmatter.get("palavra_chave_foco", ""),
        slug=slug,
        tags=frontmatter.get("tags", []) or [],
        corpo_markdown=corpo_markdown,
    )


def imagem_correspondente(materia: DadosMateria, pasta_imagens: Path) -> Path | None:
    """A gerador_imagem.py salva a imagem usando o slug da matéria
    (ex: imagens/banho-de-atracao.jpg). Essa função acha essa imagem, se
    existir, pra publicador_wordpress.py poder subi-la como destaque."""
    candidato = pasta_imagens / f"{materia.slug}.jpg"
    return candidato if candidato.exists() else None


# --------------------------------------------------------------------------- #
# Configuração do blog — é isto que torna o projeto reaproveitável pra
# qualquer nicho: nenhum script sabe o nome do blog, o domínio de assunto,
# o tom de voz ou a identidade visual "no código". Tudo vem daqui.
# --------------------------------------------------------------------------- #

CAMINHO_CONFIG_BLOG_PADRAO = Path("config/blog.yaml")


@dataclass
class ConfigBlog:
    nome: str
    dominio: str
    dominio_extendido: str
    descricao_curta: str
    idioma: str = "português do Brasil"
    termos_especificos: str = ""
    min_palavras: int = 2000
    autores_referencia: list[str] = field(default_factory=list)
    tom_voz: str = ""
    voz_primeira_pessoa: str = "um praticante experiente"
    cta_texto: str = ""
    cta_link_markdown: str = ""
    imagem_paleta_cores: str = "cores vibrantes e harmônicas"
    imagem_atmosfera_extra: str = ""
    imagem_elementos_permitidos: str = "objetos simbólicos relacionados ao tema"
    imagem_elementos_proibidos: str = "human figures, faces, or body parts"
    imagem_descricao_marca: str = ""
    imagem_incluir_marca_padrao: bool = True


def carregar_config_blog(caminho: Path = CAMINHO_CONFIG_BLOG_PADRAO) -> ConfigBlog:
    """Lê config/blog.yaml e devolve a identidade do blog usada pelos prompts
    de todos os scripts. Levanta ErroConfiguracao com uma mensagem clara se
    o arquivo não existir ou estiver faltando algum campo obrigatório."""
    if not caminho.exists():
        raise ErroConfiguracao(
            f"Arquivo de configuração do blog não encontrado: {caminho}. "
            "Copie/edite config/blog.yaml com os dados do seu blog (veja o "
            "README, seção 'Adaptando pro seu blog')."
        )

    with caminho.open("r", encoding="utf-8") as f:
        dados = yaml.safe_load(f) or {}

    blog = dados.get("blog", {}) or {}
    editorial = dados.get("editorial", {}) or {}
    cta = dados.get("cta", {}) or {}
    imagem = dados.get("imagem", {}) or {}

    obrigatorios = ("nome", "dominio", "dominio_extendido", "descricao_curta")
    faltando = [c for c in obrigatorios if not blog.get(c)]
    if faltando:
        raise ErroConfiguracao(
            f"{caminho} está sem os campos obrigatórios em 'blog:': {', '.join(faltando)}"
        )

    return ConfigBlog(
        nome=blog["nome"],
        dominio=blog["dominio"],
        dominio_extendido=blog.get("dominio_extendido", blog["dominio"]),
        descricao_curta=blog["descricao_curta"],
        idioma=blog.get("idioma", "português do Brasil"),
        termos_especificos=editorial.get("termos_especificos", ""),
        min_palavras=int(editorial.get("min_palavras", 2000)),
        autores_referencia=list(dados.get("autores_referencia", []) or []),
        tom_voz=editorial.get("tom", ""),
        voz_primeira_pessoa=editorial.get("voz", "um praticante experiente"),
        cta_texto=cta.get("texto", ""),
        cta_link_markdown=cta.get("link_markdown", ""),
        imagem_paleta_cores=imagem.get("paleta_cores", "cores vibrantes e harmônicas"),
        imagem_atmosfera_extra=imagem.get("atmosfera_extra", ""),
        imagem_elementos_permitidos=imagem.get(
            "elementos_permitidos", "objetos simbólicos relacionados ao tema"
        ),
        imagem_elementos_proibidos=imagem.get(
            "elementos_proibidos", "human figures, faces, or body parts"
        ),
        imagem_descricao_marca=imagem.get("descricao_marca", ""),
        imagem_incluir_marca_padrao=bool(imagem.get("incluir_marca_padrao", True)),
    )
