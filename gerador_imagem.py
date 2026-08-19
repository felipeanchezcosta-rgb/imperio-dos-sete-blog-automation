"""
gerador_imagem.py
------------------
Parte 3 da automação do blog "Império dos Sete".

Responsabilidade deste arquivo (só isso, nada além):
  1. Ler o frontmatter e o corpo de uma matéria .md (gerada pelo escritor_materias.py) —
     se houver mais de uma matéria em materias/, pergunta interativamente qual delas usar.
  2. Gerar a imagem via Pollinations.ai (padrão, gratuito) ou Together.ai
     (FLUX.1-schnell-Free, qualidade melhor mas depende de crédito de conta),
     em tons de amarelo/dourado, preto e branco/creme, com estética simbólica
     e respeitosa (SEM tentar desenhar orixás/entidades — só elementos como
     velas, búzios, fumaça de defumador, folhas, luz dourada), incorporando
     no próprio prompt os elementos visuais da identidade do Império dos
     Sete (tridente, ramos de louro, coroa) descritos em detalhes.
  3. Gerar uma legenda curta (3-6 palavras) via Groq a partir do título da
     matéria, pra ficar elegante escrita por cima da imagem.
  4. Redimensionar/cortar pra proporção 16:9 (padrão de imagem destacada
     de post de blog) e escrever a legenda por cima.
  5. Salvar o resultado em imagens/<slug>.jpg, pronto pra ser usado pelo
     publicador_wordpress.py (que já procura a imagem pelo slug automaticamente).

Como rodar:
  python gerador_imagem.py
      -> se houver mais de uma matéria em materias/, pergunta qual delas usar
  python gerador_imagem.py --arquivo materias/20260803_160051_algum-slug.md
  python gerador_imagem.py --provedor together
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from .comum import (
    DadosMateria,
    ErroConfiguracao,
    ErroGroq,
    carregar_frontmatter,
    chamar_groq,
    configurar_logging,
    escrever_arquivo_atomico,
    sessao_com_retry,
    validar_chave,
)

load_dotenv()
logger = configurar_logging("gerador_imagem")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "")
# Endpoint LEGADO da Pollinations: continua gratuito, sem cadastro e sem chave
# pro modelo Flux (só com limite de ritmo pra quem não se cadastra).
# NÃO usar gen.pollinations.ai — esse é o gateway novo, que cobra em Pollen
# até pro Flux, mesmo sem chave.
POLLINATIONS_URL = "https://image.pollinations.ai/prompt"

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_URL = "https://api.together.xyz/v1/images/generations"
TOGETHER_MODELO_PADRAO = "black-forest-labs/FLUX.1-schnell-Free"

PASTA_MATERIAS = Path("materias")
PASTA_IMAGENS = Path("imagens")

LARGURA_PADRAO = 1200
ALTURA_PADRAO = 675  # 16:9, tamanho comum de imagem destacada de blog

# Fontes candidatas, em ordem de preferência. Cobre Windows, Linux e macOS
# pra funcionar em qualquer máquina de dev sem configuração extra — você
# também pode apontar uma fonte específica via FONTE_TITULO no .env.
FONTE_TITULO_ENV = os.getenv("FONTE_TITULO", "")
FONTES_CANDIDATAS = [
    FONTE_TITULO_ENV,
    r"C:\Windows\Fonts\georgiab.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

DESCRICAO_MARCA = (
    "a black trident emblem with a tall thin center prong and two hooked side "
    "prongs, small golden spark strokes beside it, a pair of symmetrical golden "
    "laurel branches curving outward below it, and a small golden three-point "
    "crown beneath the branches, heraldic and symmetrical, black and gold on cream"
)


class ErroImagem(Exception):
    """A geração/download da imagem falhou em algum provedor."""


class CreditoTogetherEsgotadoError(Exception):
    """Levantado quando a Together.ai recusa por falta de créditos (HTTP 402)."""


# --------------------------------------------------------------------------- #
# Escolha da matéria
# --------------------------------------------------------------------------- #

def encontrar_arquivos_materias() -> list[Path]:
    arquivos = sorted(glob.glob(str(PASTA_MATERIAS / "*.md")))
    if not arquivos:
        raise ErroConfiguracao(
            f"Nenhum .md encontrado em {PASTA_MATERIAS}/. Rode o escritor_materias.py primeiro."
        )
    return [Path(a) for a in arquivos]


def escolher_arquivo_materia() -> Path:
    """Se houver mais de uma matéria na pasta, pergunta interativamente qual
    delas gerar a imagem, em vez de escolher a mais recente sozinho."""
    caminhos = encontrar_arquivos_materias()

    if len(caminhos) == 1:
        return caminhos[0]

    print("\nMatérias disponíveis:")
    for i, caminho in enumerate(caminhos, start=1):
        materia = carregar_frontmatter(caminho)
        print(f"  [{i}] {materia.titulo_seo}  ({caminho.name})")

    while True:
        escolha = input(f"\nDigite o número da matéria (1 a {len(caminhos)}): ").strip()
        if escolha.isdigit() and 1 <= int(escolha) <= len(caminhos):
            return caminhos[int(escolha) - 1]
        print(f"Número inválido. Digite um valor entre 1 e {len(caminhos)}.")


# --------------------------------------------------------------------------- #
# Cena visual específica via Groq (lendo a matéria de verdade)
# --------------------------------------------------------------------------- #

CENA_SYSTEM_PROMPT = """You help create image-generation prompts for a blog about Umbanda, \
Candomblé and Quimbanda. Given an article's title, meta description and body, describe, \
in ONE short sentence (max 30 words), a specific visual scene that represents the exact \
situation/problem discussed in that article — not a generic candle scene.

Rules:
- Only symbolic objects allowed: candles, cowrie shells, incense smoke, ritual herbs, \
palm leaves, fabric, altar objects, tools, tarot-like cards, mirrors, water bowls, etc.
- NEVER include deities, entities, orixás, or any human figure, face, or body part.
- The scene must clearly relate to the article's specific topic (e.g. an article about a \
guide/entity not incorporating during a session should suggest stillness, an unlit candle, \
an empty space at the front of an altar — not just "candles burning").
- Write in English, as a single descriptive phrase ready to paste into an image prompt.
- Respond with ONLY the sentence, nothing else."""

ALT_TEXT_SYSTEM_PROMPT = """You write SEO alt text for featured images on a blog about \
Umbanda, Candomblé and Quimbanda. Given the article's title, focus keyword and meta \
description, write ONE alt text in Brazilian Portuguese, up to 125 characters, that: \
naturally includes the focus keyword, accurately and honestly describes what a viewer \
would see in the photo (candles, smoke, ritual objects — never claims to show deities \
or people that aren't there), and reads naturally, not like a keyword-stuffed list. \
Respond with ONLY the alt text, nothing else."""

LEGENDA_SYSTEM_PROMPT = """Você escreve legendas muito curtas para aparecerem por cima \
de fotos de capa de artigos de um blog sobre Umbanda, Candomblé e Quimbanda. Dado o \
título de um artigo, crie uma legenda de 3 a 6 palavras, elegante e evocativa, que \
capture o espírito do tema — nunca uma repetição literal do título inteiro. Responda \
APENAS com a legenda em português do Brasil, sem aspas, sem ponto final, sem explicação."""


def _groq_disponivel() -> bool:
    return bool(GROQ_API_KEY) and not GROQ_API_KEY.startswith("cole_")


def gerar_cena_visual(materia: DadosMateria) -> str:
    if not _groq_disponivel():
        logger.warning("GROQ_API_KEY não configurada — usando cena genérica na imagem.")
        return ""

    logger.info("Lendo a matéria e gerando a cena visual específica com a Groq ...")
    trecho_corpo = materia.corpo_markdown[:3000]  # suficiente pra pegar o contexto real
    conteudo_usuario = (
        f"Title: {materia.titulo_seo}\n"
        f"Meta description: {materia.meta_descricao}\n"
        f"Article body (excerpt):\n{trecho_corpo}"
    )

    try:
        return chamar_groq(
            CENA_SYSTEM_PROMPT, conteudo_usuario,
            api_key=GROQ_API_KEY, modelo=GROQ_MODEL,
            temperatura=0.7, max_tokens=100,
        ).strip('"')
    except ErroGroq as e:
        logger.warning("Falha ao gerar cena visual com a Groq (%s) — usando cena genérica.", e)
        return ""


def gerar_alt_text_seo(materia: DadosMateria, cena_especifica: str) -> str:
    if not _groq_disponivel():
        return materia.titulo_seo

    logger.info("Gerando alt text otimizado pra SEO com a Groq ...")
    conteudo_usuario = (
        f"Title: {materia.titulo_seo}\n"
        f"Focus keyword: {materia.palavra_chave_foco}\n"
        f"Meta description: {materia.meta_descricao}\n"
        f"What the photo actually shows: {cena_especifica or 'candles and incense smoke'}"
    )

    try:
        alt_text = chamar_groq(
            ALT_TEXT_SYSTEM_PROMPT, conteudo_usuario,
            api_key=GROQ_API_KEY, modelo=GROQ_MODEL,
            temperatura=0.6, max_tokens=60,
        ).strip('"')
        return alt_text or materia.titulo_seo
    except ErroGroq as e:
        logger.warning("Falha ao gerar alt text com a Groq (%s) — usando o título como alt text.", e)
        return materia.titulo_seo


def gerar_legenda_curta(materia: DadosMateria) -> str:
    if not _groq_disponivel():
        logger.warning("GROQ_API_KEY não configurada — usando o título completo na imagem.")
        return materia.titulo_seo

    logger.info("Gerando legenda curta com a Groq ...")
    try:
        legenda = chamar_groq(
            LEGENDA_SYSTEM_PROMPT, f"Título do artigo: {materia.titulo_seo}",
            api_key=GROQ_API_KEY, modelo=GROQ_MODEL,
            temperatura=0.8, max_tokens=30,
        ).strip('"')
        return legenda or materia.titulo_seo
    except ErroGroq as e:
        logger.warning("Falha ao gerar legenda com a Groq (%s) — usando o título completo.", e)
        return materia.titulo_seo


# --------------------------------------------------------------------------- #
# Montagem do prompt de imagem
# --------------------------------------------------------------------------- #

def montar_prompt_imagem(materia: DadosMateria, cena_especifica: str = "", incluir_marca: bool = True) -> str:
    tema = materia.palavra_chave_foco or materia.titulo_seo

    cena = cena_especifica or "candles and incense smoke in a quiet ritual setting"
    trecho_marca = (
        f"In the background or on a minor object, small and subtle, not the "
        f"focus of the image: {DESCRICAO_MARCA}. "
    ) if incluir_marca else ""

    return (
        f"Professional editorial photograph, magazine cover quality. "
        f"Main subject, filling most of the frame: {cena}. "
        f"Theme: '{tema}'. "
        "Afro-Brazilian spiritual atmosphere, symbolic objects only, "
        "no human figures, no deities, no faces, no legible text, "
        f"{trecho_marca}"
        "vibrant warm golden and white color palette, dramatic candlelight, "
        "wisps of smoke, rich realistic textures, shallow depth of field, "
        "shot on a full-frame camera with a prime lens, natural authentic "
        "lighting, balanced composition following the rule of thirds, "
        "polished color grading, no artificial or uncanny look, no distortion, "
        "highly detailed, sharp focus, trustworthy and premium editorial "
        "quality suitable for a respected publication, 16:9 composition"
    )


# --------------------------------------------------------------------------- #
# Geração da imagem
# --------------------------------------------------------------------------- #

def gerar_imagem_pollinations(prompt: str, largura: int, altura: int, modelo: str, seed: int | None) -> Image.Image:
    logger.info("Gerando imagem (modelo=%s, %dx%d) ...", modelo, largura, altura)

    url = f"{POLLINATIONS_URL}/{quote(prompt)}"
    params: dict[str, object] = {
        "model": modelo, "width": largura, "height": altura,
        "nologo": "true", "safe": "true",
    }
    if seed is not None:
        params["seed"] = seed
    if POLLINATIONS_API_KEY:
        params["token"] = POLLINATIONS_API_KEY

    sessao = sessao_com_retry()
    try:
        resp = sessao.get(url, params=params, timeout=120)
    except requests.exceptions.RequestException as e:
        raise ErroImagem(f"Falha de conexão com a Pollinations.ai: {e}") from e

    if resp.status_code != 200:
        dica = ""
        if resp.status_code in (401, 403):
            dica = (" Dica: crie uma chave grátis em https://enter.pollinations.ai "
                    "e coloque em POLLINATIONS_API_KEY no .env.")
        raise ErroImagem(f"Erro ao gerar imagem ({resp.status_code}): {resp.text[:300]}.{dica}")

    try:
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:  # imagem corrompida ou resposta que não é imagem
        raise ErroImagem(f"A resposta não veio como uma imagem válida: {e}") from e


def gerar_imagem_together(prompt: str, largura: int, altura: int, modelo: str, seed: int | None) -> Image.Image:
    validar_chave(TOGETHER_API_KEY, "TOGETHER_API_KEY")

    logger.info("Gerando imagem via Together.ai (modelo=%s) ...", modelo)

    proporcao = math.gcd(largura, altura) or 1
    aspect_ratio = f"{largura // proporcao}:{altura // proporcao}"

    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    payload: dict[str, object] = {
        "model": modelo, "prompt": prompt, "aspect_ratio": aspect_ratio,
        "steps": 4, "n": 1,
    }
    if seed is not None:
        payload["seed"] = seed

    sessao = sessao_com_retry()
    try:
        resp = sessao.post(TOGETHER_URL, headers=headers, json=payload, timeout=120)
    except requests.exceptions.RequestException as e:
        raise ErroImagem(f"Falha de conexão com a Together.ai: {e}") from e

    if resp.status_code != 200:
        if resp.status_code == 402:
            raise CreditoTogetherEsgotadoError(resp.text[:300])
        raise ErroImagem(f"Erro ao gerar imagem na Together.ai ({resp.status_code}): {resp.text[:300]}")

    try:
        url_imagem = resp.json()["data"][0]["url"]
    except (KeyError, IndexError, requests.exceptions.JSONDecodeError) as e:
        raise ErroImagem(f"Resposta inesperada da Together.ai: {e}") from e

    try:
        resp_imagem = sessao.get(url_imagem, timeout=60)
        resp_imagem.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise ErroImagem(f"Falha ao baixar a imagem gerada: {e}") from e

    return Image.open(BytesIO(resp_imagem.content)).convert("RGB")


def ajustar_para_proporcao(imagem: Image.Image, largura: int, altura: int) -> Image.Image:
    """Corta a imagem (crop central) pra bater exatamente com a proporção alvo,
    caso a API devolva um tamanho levemente diferente do pedido."""
    if altura <= 0:
        raise ValueError("altura precisa ser maior que zero.")

    proporcao_alvo = largura / altura
    w, h = imagem.size
    if h == 0:
        raise ErroImagem("A imagem recebida do provedor veio com altura zero.")
    proporcao_atual = w / h

    if proporcao_atual > proporcao_alvo:
        nova_largura = int(h * proporcao_alvo)
        offset = (w - nova_largura) // 2
        imagem = imagem.crop((offset, 0, offset + nova_largura, h))
    else:
        nova_altura = int(w / proporcao_alvo)
        offset = (h - nova_altura) // 2
        imagem = imagem.crop((0, offset, w, offset + nova_altura))

    return imagem.resize((largura, altura), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# Texto do título por cima da imagem
# --------------------------------------------------------------------------- #

def carregar_fonte(tamanho: int) -> ImageFont.FreeTypeFont:
    for caminho in FONTES_CANDIDATAS:
        if caminho and Path(caminho).exists():
            try:
                return ImageFont.truetype(caminho, tamanho)
            except OSError:
                continue
    logger.warning(
        "Nenhuma fonte .ttf encontrada, usando fonte padrão do Pillow (fica pequena/feia). "
        "Defina FONTE_TITULO no .env com o caminho de uma fonte .ttf pra melhorar isso."
    )
    return ImageFont.load_default()


def quebrar_linhas(draw: ImageDraw.ImageDraw, texto: str, fonte: ImageFont.FreeTypeFont,
                    largura_maxima: int) -> list[str]:
    palavras = texto.split()
    linhas: list[str] = []
    linha_atual = ""

    for palavra in palavras:
        candidata = f"{linha_atual} {palavra}".strip()
        if draw.textlength(candidata, font=fonte) <= largura_maxima:
            linha_atual = candidata
        else:
            if linha_atual:
                linhas.append(linha_atual)
            linha_atual = palavra

    if linha_atual:
        linhas.append(linha_atual)
    return linhas


def adicionar_texto_titulo(imagem: Image.Image, texto: str, posicao: str = "inferior") -> Image.Image:
    """Escreve o título por cima da imagem com uma faixa escura semi-transparente
    atrás pra garantir legibilidade, em vez de pedir pro modelo de IA desenhar
    o texto (que costuma sair embaralhado/ilegível)."""
    imagem = imagem.convert("RGBA")
    largura, altura = imagem.size

    margem_lateral = int(largura * 0.06)
    largura_util = largura - (2 * margem_lateral)

    tamanho_fonte = max(28, int(largura * 0.045))
    fonte = carregar_fonte(tamanho_fonte)

    camada_texto = Image.new("RGBA", imagem.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(camada_texto)

    linhas = quebrar_linhas(draw, texto.upper(), fonte, largura_util)
    altura_linha = int(tamanho_fonte * 1.25)
    altura_bloco_texto = altura_linha * len(linhas)

    padding = int(altura * 0.04)
    altura_faixa = altura_bloco_texto + (padding * 2)

    topo_faixa = altura - altura_faixa if posicao == "inferior" else 0

    draw.rectangle(
        [(0, topo_faixa), (largura, topo_faixa + altura_faixa)],
        fill=(10, 10, 10, 165),
    )

    y = topo_faixa + padding
    for linha in linhas:
        largura_linha = draw.textlength(linha, font=fonte)
        x = (largura - largura_linha) / 2
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
            draw.text((x + dx, y + dy), linha, font=fonte, fill=(0, 0, 0, 255))
        draw.text((x, y), linha, font=fonte, fill=(255, 215, 0, 255))
        y += altura_linha

    return Image.alpha_composite(imagem, camada_texto).convert("RGB")


def salvar_imagem_atomico(imagem: Image.Image, caminho: Path, formato: str = "JPEG", **kwargs) -> None:
    """Mesma lógica de escrever_arquivo_atomico (comum.py), mas pra imagens
    do Pillow: salva num .tmp e só troca de nome no final."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    imagem.save(tmp, formato, **kwargs)
    tmp.replace(caminho)


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera a imagem de destaque de uma matéria do Império dos Sete."
    )
    parser.add_argument("--arquivo", type=str, default=None,
                         help="Caminho do .md da matéria. Se omitido e houver mais de uma "
                              "em materias/, pergunta interativamente qual usar.")
    parser.add_argument("--provedor", type=str, default="pollinations", choices=["together", "pollinations"],
                         help="Provedor de geração de imagem (padrão: pollinations).")
    parser.add_argument("--modelo", type=str, default=None,
                         help="Modelo a usar. Together: black-forest-labs/FLUX.1-schnell-Free (padrão). "
                              "Pollinations: gptimage-large (padrão), flux, turbo.")
    parser.add_argument("--largura", type=int, default=LARGURA_PADRAO)
    parser.add_argument("--altura", type=int, default=ALTURA_PADRAO)
    parser.add_argument("--seed", type=int, default=None, help="Seed fixa, pra repetir o mesmo resultado.")
    parser.add_argument("--posicao-texto", type=str, default="inferior",
                         choices=["inferior", "superior"], help="Onde colocar a faixa com a legenda.")
    parser.add_argument("--sem-texto", action="store_true",
                         help="Gera a imagem sem escrever legenda por cima.")
    parser.add_argument("--sem-marca", action="store_true",
                         help="Gera a imagem sem tentar incluir o emblema da marca no fundo.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Valida a credencial do provedor ANTES de gastar chamadas na Groq —
    # no código original, se a TOGETHER_API_KEY estivesse faltando, o
    # script só descobria isso depois de já ter gerado cena/prompt via Groq.
    try:
        if args.provedor == "together":
            validar_chave(TOGETHER_API_KEY, "TOGETHER_API_KEY")
    except ErroConfiguracao as e:
        logger.error(str(e))
        logger.error(
            "Crie uma conta grátis em https://api.together.ai e gere uma chave em "
            "https://api.together.ai/settings/api-keys"
        )
        return 1

    try:
        caminho_materia = Path(args.arquivo) if args.arquivo else escolher_arquivo_materia()
    except ErroConfiguracao as e:
        logger.error(str(e))
        return 1

    if not caminho_materia.exists():
        logger.error("Arquivo não encontrado: %s", caminho_materia)
        return 1

    try:
        materia = carregar_frontmatter(caminho_materia)
    except ValueError as e:
        logger.error(str(e))
        return 1

    logger.info("Matéria: %s", materia.titulo_seo)

    cena_especifica = gerar_cena_visual(materia)
    prompt = montar_prompt_imagem(materia, cena_especifica, incluir_marca=not args.sem_marca)

    try:
        if args.provedor == "together":
            modelo = args.modelo or TOGETHER_MODELO_PADRAO
            try:
                imagem = gerar_imagem_together(prompt, args.largura, args.altura, modelo, args.seed)
            except CreditoTogetherEsgotadoError:
                logger.warning(
                    "A Together.ai recusou por falta de créditos (a promoção gratuita do "
                    "FLUX.1-schnell-Free pode ter acabado). Caindo pra Pollinations.ai ..."
                )
                imagem = gerar_imagem_pollinations(prompt, args.largura, args.altura, "gptimage-large", args.seed)
        else:
            modelo = args.modelo or "gptimage-large"
            imagem = gerar_imagem_pollinations(prompt, args.largura, args.altura, modelo, args.seed)
    except ErroImagem as e:
        logger.error(str(e))
        return 1

    try:
        imagem_final = ajustar_para_proporcao(imagem, args.largura, args.altura)
    except (ValueError, ErroImagem) as e:
        logger.error(str(e))
        return 1

    if not args.sem_texto:
        legenda = gerar_legenda_curta(materia)
        imagem_final = adicionar_texto_titulo(imagem_final, legenda, args.posicao_texto)

    # Nome do arquivo baseado no SLUG (não no timestamp do .md) — assim
    # publicador_wordpress.py acha a imagem certa de forma previsível,
    # e rodar de novo pra mesma matéria substitui a imagem em vez de
    # acumular arquivos soltos.
    caminho_saida = PASTA_IMAGENS / f"{materia.slug}.jpg"
    salvar_imagem_atomico(imagem_final, caminho_saida, "JPEG", quality=90)

    alt_text = gerar_alt_text_seo(materia, cena_especifica)
    caminho_alt = caminho_saida.with_suffix(".alt.txt")
    escrever_arquivo_atomico(caminho_alt, alt_text)

    logger.info("Imagem salva em: %s", caminho_saida)
    logger.info("Alt text (SEO) salvo em: %s", caminho_alt)
    logger.info('  -> "%s"', alt_text)
    logger.info("  (o publicador_wordpress.py já usa essa imagem e esse alt text automaticamente)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
