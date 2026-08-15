"""Testes do módulo gerador_imagem.py — geração da imagem de destaque (etapa 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from imperio_dos_sete import gerador_imagem as gi
from imperio_dos_sete.comum import DadosMateria, ErroConfiguracao


def _materia_exemplo() -> DadosMateria:
    return DadosMateria(
        titulo_seo="Como fazer banho de atração em apartamento",
        meta_descricao="Descrição de teste",
        palavra_chave_foco="banho de atração",
        slug="banho-de-atracao-apartamento",
        tags=["banhos"],
        corpo_markdown="Corpo de teste.",
    )


# --------------------------------------------------------------------------- #
# escolher_arquivo_materia / encontrar_arquivos_materias
# --------------------------------------------------------------------------- #

def test_encontrar_arquivos_materias_sem_arquivos_levanta_erro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(gi, "PASTA_MATERIAS", tmp_path / "materias-vazia")
    with pytest.raises(ErroConfiguracao):
        gi.encontrar_arquivos_materias()


def test_escolher_arquivo_materia_unico_nao_pergunta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pasta = tmp_path / "materias"
    pasta.mkdir()
    caminho_md = pasta / "unica.md"
    caminho_md.write_text(
        '---\ntitulo_seo: "T"\nslug: "s"\n---\n\nCorpo.',
        encoding="utf-8",
    )
    monkeypatch.setattr(gi, "PASTA_MATERIAS", pasta)

    # Se tentasse chamar input(), o teste travaria/falharia — isso garante
    # que o caminho de "arquivo único" não pergunta nada ao usuário.
    resultado = gi.escolher_arquivo_materia()
    assert resultado == caminho_md


# --------------------------------------------------------------------------- #
# montar_prompt_imagem
# --------------------------------------------------------------------------- #

def test_montar_prompt_imagem_inclui_tema_e_cena() -> None:
    materia = _materia_exemplo()
    prompt = gi.montar_prompt_imagem(materia, cena_especifica="velas apagadas sobre um altar")

    assert "velas apagadas sobre um altar" in prompt
    assert materia.palavra_chave_foco in prompt
    assert "no human figures" in prompt


def test_montar_prompt_imagem_usa_cena_generica_por_padrao() -> None:
    materia = _materia_exemplo()
    prompt = gi.montar_prompt_imagem(materia, cena_especifica="")
    assert "candles and incense smoke" in prompt


def test_montar_prompt_imagem_sem_marca_nao_inclui_descricao_marca() -> None:
    materia = _materia_exemplo()
    prompt_com_marca = gi.montar_prompt_imagem(materia, incluir_marca=True)
    prompt_sem_marca = gi.montar_prompt_imagem(materia, incluir_marca=False)

    assert gi.DESCRICAO_MARCA in prompt_com_marca
    assert gi.DESCRICAO_MARCA not in prompt_sem_marca


# --------------------------------------------------------------------------- #
# quebrar_linhas
# --------------------------------------------------------------------------- #

def _draw_de_teste() -> ImageDraw.ImageDraw:
    imagem = Image.new("RGB", (10, 10))
    return ImageDraw.Draw(imagem)


def test_quebrar_linhas_texto_curto_fica_em_uma_linha() -> None:
    draw = _draw_de_teste()
    fonte = ImageFont.load_default()
    linhas = gi.quebrar_linhas(draw, "TÍTULO CURTO", fonte, largura_maxima=1000)
    assert linhas == ["TÍTULO CURTO"]


def test_quebrar_linhas_texto_longo_quebra_em_varias_linhas() -> None:
    draw = _draw_de_teste()
    fonte = ImageFont.load_default()
    texto = "UM TÍTULO BEM LONGO QUE PRECISA QUEBRAR EM VÁRIAS LINHAS DIFERENTES AQUI"
    linhas = gi.quebrar_linhas(draw, texto, fonte, largura_maxima=80)
    assert len(linhas) > 1
    # nenhuma palavra pode ter sido perdida na quebra
    assert " ".join(linhas).split() == texto.split()


# --------------------------------------------------------------------------- #
# ajustar_para_proporcao
# --------------------------------------------------------------------------- #

def test_ajustar_para_proporcao_imagem_mais_larga_corta_laterais() -> None:
    imagem = Image.new("RGB", (2000, 1000))  # bem mais larga que 16:9
    resultado = gi.ajustar_para_proporcao(imagem, 1200, 675)
    assert resultado.size == (1200, 675)


def test_ajustar_para_proporcao_imagem_mais_alta_corta_topo_base() -> None:
    imagem = Image.new("RGB", (800, 1600))  # bem mais alta que 16:9
    resultado = gi.ajustar_para_proporcao(imagem, 1200, 675)
    assert resultado.size == (1200, 675)


def test_ajustar_para_proporcao_altura_zero_levanta_erro() -> None:
    imagem = Image.new("RGB", (100, 100))
    with pytest.raises(ValueError):
        gi.ajustar_para_proporcao(imagem, 100, 0)


# --------------------------------------------------------------------------- #
# parse_args
# --------------------------------------------------------------------------- #

def test_parse_args_valores_padrao() -> None:
    args = gi.parse_args([])
    assert args.provedor == "pollinations"
    assert args.largura == gi.LARGURA_PADRAO
    assert args.altura == gi.ALTURA_PADRAO


def test_parse_args_provedor_invalido_falha() -> None:
    with pytest.raises(SystemExit):
        gi.parse_args(["--provedor", "provedor-que-nao-existe"])
