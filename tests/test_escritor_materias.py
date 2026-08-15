"""Testes do módulo escritor_materias.py — escrita das matérias (etapa 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from imperio_dos_sete import escritor_materias as em
from imperio_dos_sete.comum import ErroConfiguracao


def _ideias_exemplo() -> list[em.Ideia]:
    return [
        em.Ideia(numero=1, vertente="Banhos", titulo="Título 1",
                 micro_nicho="nicho 1", intencao_busca="informacional"),
        em.Ideia(numero=2, vertente="Guias", titulo="Título 2",
                 micro_nicho="nicho 2", intencao_busca="comparativa"),
        em.Ideia(numero=7, vertente="Firmezas", titulo="Título 7",
                 micro_nicho="nicho 7", intencao_busca="transacional"),
    ]


# --------------------------------------------------------------------------- #
# escolher_indices
# --------------------------------------------------------------------------- #

def test_escolher_indices_com_argumento_explicito() -> None:
    assert em.escolher_indices("1,7", _ideias_exemplo()) == [1, 7]


def test_escolher_indices_com_espacos() -> None:
    assert em.escolher_indices(" 1 , 2 ", _ideias_exemplo()) == [1, 2]


def test_escolher_indices_valor_nao_numerico_levanta_erro_claro() -> None:
    with pytest.raises(ErroConfiguracao, match="não numérico"):
        em.escolher_indices("1,abc", _ideias_exemplo())


def test_escolher_indices_vazio_levanta_erro() -> None:
    # string truthy mas sem nenhum número real (evita cair no modo
    # interativo, que só é acionado quando o argumento é None/vazio)
    with pytest.raises(ErroConfiguracao, match="Nenhum número"):
        em.escolher_indices(",  ,", _ideias_exemplo())


def test_escolher_indices_numero_invalido_levanta_erro() -> None:
    with pytest.raises(ErroConfiguracao, match="inválido"):
        em.escolher_indices("99", _ideias_exemplo())


# --------------------------------------------------------------------------- #
# remover_placeholders_link_interno
# --------------------------------------------------------------------------- #

def test_remove_placeholder_de_link_interno() -> None:
    texto = "Antes [[LINK_INTERNO: banho-de-atracao]] depois."
    assert em.remover_placeholders_link_interno(texto) == "Antes  depois."


def test_remove_multiplos_placeholders() -> None:
    texto = "[[LINK_INTERNO: a]] meio [[LINK_INTERNO: b]] fim"
    resultado = em.remover_placeholders_link_interno(texto)
    assert "LINK_INTERNO" not in resultado


def test_texto_sem_placeholder_fica_igual() -> None:
    texto = "Nada pra remover aqui."
    assert em.remover_placeholders_link_interno(texto) == texto


# --------------------------------------------------------------------------- #
# contar_palavras
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "texto, esperado",
    [("uma duas três", 3), ("", 0), ("   ", 0), ("única", 1)],
)
def test_contar_palavras(texto: str, esperado: int) -> None:
    assert em.contar_palavras(texto) == esperado


# --------------------------------------------------------------------------- #
# montar_markdown_final
# --------------------------------------------------------------------------- #

def test_montar_markdown_final_gera_frontmatter_correto() -> None:
    artigo = {
        "meta_titulo_seo": "Título SEO",
        "meta_descricao": "Descrição",
        "palavra_chave_foco": "palavra-chave",
        "tags": ["a", "b"],
        "corpo_markdown": "# Título\n\nCorpo do artigo.",
    }

    resultado = em.montar_markdown_final(artigo, "slug-final")

    assert resultado.startswith("---\n")
    assert 'slug: "slug-final"' in resultado
    assert '"tags": ' not in resultado  # tags vem em formato de lista JSON, não string
    assert "Corpo do artigo." in resultado


def test_montar_markdown_final_remove_link_interno_do_corpo() -> None:
    artigo = {"corpo_markdown": "Texto [[LINK_INTERNO: x]] final.", "tags": []}
    resultado = em.montar_markdown_final(artigo, "slug")
    assert "LINK_INTERNO" not in resultado


# --------------------------------------------------------------------------- #
# _reservar_slug
# --------------------------------------------------------------------------- #

def test_reservar_slug_sem_conflito() -> None:
    usados: set[str] = set()
    assert em._reservar_slug("banho-de-atracao", usados) == "banho-de-atracao"


def test_reservar_slug_com_conflito_adiciona_sufixo() -> None:
    usados = {"banho-de-atracao"}
    resultado = em._reservar_slug("banho-de-atracao", usados)
    assert resultado == "banho-de-atracao-2"
    assert resultado in usados


def test_reservar_slug_com_multiplos_conflitos() -> None:
    usados = {"tema", "tema-2", "tema-3"}
    assert em._reservar_slug("tema", usados) == "tema-4"


# --------------------------------------------------------------------------- #
# carregar_ideias / encontrar_arquivo_ideias_mais_recente
# --------------------------------------------------------------------------- #

def test_carregar_ideias_le_arquivo_json(tmp_path: Path) -> None:
    caminho = tmp_path / "ideias.json"
    caminho.write_text(
        '{"ideias": [{"numero": 1, "titulo": "Teste", "vertente": "V", '
        '"microNicho": "M", "intencaoBusca": "informacional"}]}',
        encoding="utf-8",
    )

    ideias = em.carregar_ideias(caminho)

    assert len(ideias) == 1
    assert ideias[0].titulo == "Teste"


def test_encontrar_arquivo_ideias_mais_recente_sem_arquivos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(em, "PASTA_IDEIAS", tmp_path / "saidas-vazia")
    with pytest.raises(ErroConfiguracao):
        em.encontrar_arquivo_ideias_mais_recente()


# --------------------------------------------------------------------------- #
# parse_args
# --------------------------------------------------------------------------- #

def test_parse_args_paralelo_padrao_e_minimo() -> None:
    args = em.parse_args([])
    assert args.paralelo == 1


def test_parse_args_paralelo_invalido_falha() -> None:
    with pytest.raises(SystemExit):
        em.parse_args(["--paralelo", "0"])
