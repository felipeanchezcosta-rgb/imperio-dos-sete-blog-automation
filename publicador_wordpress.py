"""
publicador_wordpress.py
------------------------
Parte 4 (última etapa) da automação do blog "Império dos Sete".

Responsabilidade deste arquivo:
  1. Ler um .md (ou todos os .md da pasta materias/) gerado pelo escritor_materias.py.
  2. Separar o frontmatter (título SEO, meta description, slug, tags) do corpo do artigo.
  3. Converter o corpo de markdown pra HTML.
  4. Se existir uma imagem correspondente em imagens/<slug>.jpg (gerada pelo
     gerador_imagem.py), sobe ela como mídia no WordPress e usa como imagem
     destacada do post — aplicando o alt text gerado junto, se existir.
  5. Publicar no WordPress via REST API (autenticação por Application Password),
     criando as tags que ainda não existem no site (reaproveitando um cache
     de tags carregado uma única vez por execução).
  6. Por padrão publica como RASCUNHO (WP_STATUS=draft no .env), pra você revisar
     antes de ir pro ar. Quando estiver confiante, muda pra publish no .env.

Pré-requisito no WordPress: gerar uma Senha de Aplicativo em
wp-admin -> Usuários -> seu perfil -> Senhas de Aplicativo.
(Isso é nativo do WordPress 5.6+, não precisa de plugin.)

Como rodar:
  python publicador_wordpress.py --arquivo materias/20260803_160051_algum-slug.md
  python publicador_wordpress.py --pasta materias
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import markdown as md
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

from .comum import (
    DadosMateria,
    ErroConfiguracao,
    ErroWordPress,
    carregar_frontmatter,
    configurar_logging,
    imagem_correspondente,
    sessao_com_retry,
)

load_dotenv()
logger = configurar_logging("publicador_wordpress")

WP_URL = (os.getenv("WP_URL") or "").rstrip("/")
WP_USUARIO = os.getenv("WP_USUARIO", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
WP_STATUS = os.getenv("WP_STATUS", "draft")
STATUS_VALIDOS = {"draft", "publish", "pending", "private", "future"}

PASTA_MATERIAS = Path("materias")
PASTA_IMAGENS = Path("imagens")
TIMEOUT_PADRAO = 60
TIMEOUT_UPLOAD_IMAGEM = 120


# --------------------------------------------------------------------------- #
# Configuração / validação
# --------------------------------------------------------------------------- #

def validar_configuracao() -> None:
    faltando = [
        nome for nome, valor in [
            ("WP_URL", WP_URL),
            ("WP_USUARIO", WP_USUARIO),
            ("WP_APP_PASSWORD", WP_APP_PASSWORD),
        ] if not valor or "cole_" in valor
    ]
    if faltando:
        raise ErroConfiguracao(f"Preencha no .env as variáveis: {', '.join(faltando)}")

    if WP_STATUS not in STATUS_VALIDOS:
        raise ErroConfiguracao(
            f"WP_STATUS='{WP_STATUS}' inválido. Use um de: {', '.join(sorted(STATUS_VALIDOS))}"
        )


def autenticacao() -> HTTPBasicAuth:
    return HTTPBasicAuth(WP_USUARIO, WP_APP_PASSWORD)


# --------------------------------------------------------------------------- #
# Conversão markdown -> HTML
# --------------------------------------------------------------------------- #

def converter_corpo_html(corpo_markdown: str) -> str:
    return md.markdown(corpo_markdown, extensions=["extra", "sane_lists", "nl2br"])


# --------------------------------------------------------------------------- #
# Tags — cache em memória pra não repetir a mesma busca a cada matéria.
#
# Antes: cada tag de cada matéria disparava uma busca HTTP própria
# (GET /tags?search=...). Publicando 20 matérias com tags repetidas
# (muito comum aqui: "Umbanda", "Pombagira" etc. aparecem toda hora),
# isso significava dezenas de requisições evitáveis. Agora: carrega
# todas as tags do site UMA vez (paginado) e reusa esse cache pro lote
# inteiro, só criando (POST) as que realmente não existem ainda.
# --------------------------------------------------------------------------- #

class CacheTags:
    def __init__(self, sessao: requests.Session):
        self._sessao = sessao
        self._por_nome: dict[str, int] = {}
        self._carregado = False

    def _carregar_todas(self) -> None:
        pagina = 1
        while True:
            resp = self._sessao.get(
                f"{WP_URL}/wp-json/wp/v2/tags",
                params={"per_page": 100, "page": pagina},
                auth=autenticacao(),
                timeout=TIMEOUT_PADRAO,
            )
            if resp.status_code == 400:
                # WP devolve 400 quando a página pedida passa do total — só
                # significa que acabaram as tags, não é um erro de verdade.
                break
            resp.raise_for_status()
            lote = resp.json()
            if not lote:
                break
            for tag in lote:
                self._por_nome[tag["name"].strip().lower()] = tag["id"]
            if len(lote) < 100:
                break
            pagina += 1
        self._carregado = True

    def obter_ou_criar(self, nome_tag: str) -> int:
        if not self._carregado:
            self._carregar_todas()

        chave = nome_tag.strip().lower()
        if chave in self._por_nome:
            return self._por_nome[chave]

        resp = self._sessao.post(
            f"{WP_URL}/wp-json/wp/v2/tags",
            json={"name": nome_tag},
            auth=autenticacao(),
            timeout=TIMEOUT_PADRAO,
        )
        resp.raise_for_status()
        tag_id = resp.json()["id"]
        self._por_nome[chave] = tag_id
        return tag_id

    def montar_ids(self, nomes_tags: list[str]) -> list[int]:
        return [self.obter_ou_criar(nome) for nome in nomes_tags]


# --------------------------------------------------------------------------- #
# Imagem destacada
# --------------------------------------------------------------------------- #

def enviar_imagem_destacada(caminho_imagem: Path, alt_text: str, sessao: requests.Session) -> int | None:
    """Sobe a imagem como mídia no WordPress e devolve o ID pra usar como
    featured_media. Se algo falhar, loga um aviso e devolve None — a
    publicação continua sem imagem destacada em vez de abortar tudo."""
    try:
        dados_imagem = caminho_imagem.read_bytes()
    except OSError as e:
        logger.warning("Não consegui ler %s (%s) — publicando sem imagem destacada.", caminho_imagem, e)
        return None

    headers = {
        "Content-Disposition": f'attachment; filename="{caminho_imagem.name}"',
        "Content-Type": "image/jpeg",
    }

    try:
        resp = sessao.post(
            f"{WP_URL}/wp-json/wp/v2/media",
            headers=headers,
            data=dados_imagem,
            auth=autenticacao(),
            timeout=TIMEOUT_UPLOAD_IMAGEM,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning("Falha ao subir a imagem destacada (%s) — publicando sem ela.", e)
        return None

    media_id = resp.json().get("id")
    if not media_id:
        logger.warning("WordPress não devolveu um ID de mídia — publicando sem imagem destacada.")
        return None

    if alt_text:
        try:
            resp_alt = sessao.post(
                f"{WP_URL}/wp-json/wp/v2/media/{media_id}",
                json={"alt_text": alt_text},
                auth=autenticacao(),
                timeout=TIMEOUT_PADRAO,
            )
            resp_alt.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.warning("Imagem enviada, mas falhei ao gravar o alt text (%s).", e)

    return media_id


def _carregar_alt_text(caminho_imagem: Path) -> str:
    caminho_alt = caminho_imagem.with_suffix(".alt.txt")
    if caminho_alt.exists():
        return caminho_alt.read_text(encoding="utf-8").strip()
    return ""


# --------------------------------------------------------------------------- #
# Publicação
# --------------------------------------------------------------------------- #

def publicar_materia(
    materia: DadosMateria, cache_tags: CacheTags, sessao: requests.Session,
) -> dict:
    logger.info("Publicando: %s ...", materia.titulo_seo)

    ids_tags = cache_tags.montar_ids(materia.tags)

    payload: dict[str, object] = {
        "title": materia.titulo_seo,
        "slug": materia.slug,
        "content": converter_corpo_html(materia.corpo_markdown),
        "status": WP_STATUS,
        "tags": ids_tags,
        # Meta description do Yoast SEO, se o plugin estiver ativo no site.
        # Se não estiver, o WordPress simplesmente ignora esse campo.
        "meta": {
            "_yoast_wpseo_metadesc": materia.meta_descricao,
            "_yoast_wpseo_focuskw": materia.palavra_chave_foco,
        },
    }

    caminho_imagem = imagem_correspondente(materia, PASTA_IMAGENS)
    if caminho_imagem:
        alt_text = _carregar_alt_text(caminho_imagem)
        media_id = enviar_imagem_destacada(caminho_imagem, alt_text, sessao)
        if media_id:
            payload["featured_media"] = media_id
    else:
        logger.info(
            "  (nenhuma imagem em %s/%s.jpg — rode o gerador_imagem.py antes se "
            "quiser imagem destacada nesse post)", PASTA_IMAGENS, materia.slug,
        )

    resp = sessao.post(
        f"{WP_URL}/wp-json/wp/v2/posts",
        json=payload,
        auth=autenticacao(),
        timeout=TIMEOUT_PADRAO,
    )

    if resp.status_code not in (200, 201):
        raise ErroWordPress(f"Erro ao publicar ({resp.status_code}): {resp.text[:500]}")

    dados = resp.json()
    logger.info("  -> status: %s | link: %s", dados.get("status"), dados.get("link"))
    return dados


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #

def coletar_arquivos(args: argparse.Namespace) -> list[Path]:
    if args.arquivo:
        caminho = Path(args.arquivo)
        if not caminho.exists():
            raise ErroConfiguracao(f"Arquivo não encontrado: {caminho}")
        return [caminho]

    pasta = Path(args.pasta) if args.pasta else PASTA_MATERIAS
    arquivos = sorted(glob.glob(str(pasta / "*.md")))
    if not arquivos:
        raise ErroConfiguracao(f"Nenhum .md encontrado em {pasta}/.")
    return [Path(a) for a in arquivos]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publica matérias .md do Império dos Sete no WordPress via REST API."
    )
    parser.add_argument("--arquivo", type=str, default=None, help="Caminho de um .md específico.")
    parser.add_argument("--pasta", type=str, default=None,
                         help="Pasta com vários .md a publicar (padrão: materias/).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        validar_configuracao()
        arquivos = coletar_arquivos(args)
    except ErroConfiguracao as e:
        logger.error(str(e))
        return 1

    logger.info("%d matéria(s) para publicar como '%s'.", len(arquivos), WP_STATUS)

    sessao = sessao_com_retry()
    cache_tags = CacheTags(sessao)

    resultados: list[tuple[Path, str | None]] = []
    falhas = 0
    for caminho in arquivos:
        try:
            materia = carregar_frontmatter(caminho)
            resultado = publicar_materia(materia, cache_tags, sessao)
            resultados.append((caminho, resultado.get("link")))
        except (ValueError, ErroWordPress, requests.exceptions.RequestException) as e:
            logger.error("Falha ao publicar %s: %s", caminho.name, e)
            falhas += 1

    if resultados:
        logger.info("%d matéria(s) publicada(s):", len(resultados))
        for caminho, link in resultados:
            logger.info("  - %s -> %s", caminho.name, link)

    if falhas:
        logger.warning("%d de %d matéria(s) falharam — confira os erros acima.", falhas, len(arquivos))
        return 1

    return 0 if resultados else 1


if __name__ == "__main__":
    sys.exit(main())
