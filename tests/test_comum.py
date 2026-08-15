"""Testes do módulo comum.py — utilidades compartilhadas pelos 4 scripts."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from imperio_dos_sete.comum import (
    ConfigBlog,
    ErroConfiguracao,
    ErroGroq,
    carregar_config_blog,
    carregar_frontmatter,
    chamar_groq,
    chamar_groq_json,
    escrever_arquivo_atomico,
    imagem_correspondente,
    slugificar,
    validar_chave,
)


# --------------------------------------------------------------------------- #
# slugificar
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("Banho de Atração", "banho-de-atracao"),
        ("  espaços   múltiplos  ", "espacos-multiplos"),
        ("Título com Pontuação!!!", "titulo-com-pontuacao"),
        ("já-com-hifens", "ja-com-hifens"),
        ("café com açúcar & canela", "cafe-com-acucar-canela"),
    ],
)
def test_slugificar(entrada: str, esperado: str) -> None:
    assert slugificar(entrada) == esperado


# --------------------------------------------------------------------------- #
# validar_chave
# --------------------------------------------------------------------------- #

def test_validar_chave_vazia_levanta_erro() -> None:
    with pytest.raises(ErroConfiguracao):
        validar_chave("", "GROQ_API_KEY")


def test_validar_chave_placeholder_levanta_erro() -> None:
    with pytest.raises(ErroConfiguracao):
        validar_chave("cole_sua_chave_aqui", "GROQ_API_KEY")


def test_validar_chave_valida_nao_levanta_erro() -> None:
    validar_chave("gsk_algumachavevalida", "GROQ_API_KEY", prefixo_esperado="gsk_")


def test_validar_chave_prefixo_errado_so_avisa(caplog: pytest.LogCaptureFixture) -> None:
    """Prefixo incorreto deve gerar um warning, não uma exceção."""
    with caplog.at_level("WARNING"):
        validar_chave("chave-sem-o-prefixo-certo", "GROQ_API_KEY", prefixo_esperado="gsk_")
    assert "não começa com o prefixo esperado" in caplog.text


# --------------------------------------------------------------------------- #
# escrever_arquivo_atomico
# --------------------------------------------------------------------------- #

def test_escrever_arquivo_atomico_cria_arquivo(tmp_path: Path) -> None:
    destino = tmp_path / "subpasta" / "arquivo.txt"
    escrever_arquivo_atomico(destino, "conteúdo de teste")

    assert destino.exists()
    assert destino.read_text(encoding="utf-8") == "conteúdo de teste"
    # o .tmp intermediário não pode sobrar
    assert not destino.with_suffix(destino.suffix + ".tmp").exists()


# --------------------------------------------------------------------------- #
# carregar_frontmatter
# --------------------------------------------------------------------------- #

def _escrever_materia_fake(caminho: Path) -> None:
    conteudo = (
        "---\n"
        'titulo_seo: "Título de Teste"\n'
        'meta_descricao: "Descrição de teste"\n'
        'palavra_chave_foco: "palavra-chave"\n'
        'slug: "titulo-de-teste"\n'
        'tags: ["tag1", "tag2"]\n'
        "---\n\n"
        "# Título de Teste\n\nCorpo do artigo aqui.\n"
    )
    caminho.write_text(conteudo, encoding="utf-8")


def test_carregar_frontmatter_le_campos_corretamente(tmp_path: Path) -> None:
    caminho = tmp_path / "materia.md"
    _escrever_materia_fake(caminho)

    materia = carregar_frontmatter(caminho)

    assert materia.titulo_seo == "Título de Teste"
    assert materia.slug == "titulo-de-teste"
    assert materia.tags == ["tag1", "tag2"]
    assert "Corpo do artigo aqui." in materia.corpo_markdown


def test_carregar_frontmatter_sem_bloco_levanta_erro(tmp_path: Path) -> None:
    caminho = tmp_path / "sem_frontmatter.md"
    caminho.write_text("Só um texto qualquer, sem frontmatter.", encoding="utf-8")

    with pytest.raises(ValueError):
        carregar_frontmatter(caminho)


# --------------------------------------------------------------------------- #
# imagem_correspondente
# --------------------------------------------------------------------------- #

def test_imagem_correspondente_encontra_quando_existe(tmp_path: Path) -> None:
    caminho_materia = tmp_path / "materia.md"
    _escrever_materia_fake(caminho_materia)
    materia = carregar_frontmatter(caminho_materia)

    pasta_imagens = tmp_path / "imagens"
    pasta_imagens.mkdir()
    (pasta_imagens / f"{materia.slug}.jpg").write_bytes(b"fake-jpg")

    resultado = imagem_correspondente(materia, pasta_imagens)
    assert resultado == pasta_imagens / f"{materia.slug}.jpg"


def test_imagem_correspondente_none_quando_nao_existe(tmp_path: Path) -> None:
    caminho_materia = tmp_path / "materia.md"
    _escrever_materia_fake(caminho_materia)
    materia = carregar_frontmatter(caminho_materia)

    pasta_imagens = tmp_path / "imagens"
    pasta_imagens.mkdir()

    assert imagem_correspondente(materia, pasta_imagens) is None


# --------------------------------------------------------------------------- #
# carregar_config_blog
# --------------------------------------------------------------------------- #

def test_carregar_config_blog_arquivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ErroConfiguracao):
        carregar_config_blog(tmp_path / "nao-existe.yaml")


def test_carregar_config_blog_campo_obrigatorio_faltando(tmp_path: Path) -> None:
    caminho = tmp_path / "blog.yaml"
    caminho.write_text("blog:\n  nome: Meu Blog\n", encoding="utf-8")

    with pytest.raises(ErroConfiguracao):
        carregar_config_blog(caminho)


def test_carregar_config_blog_completo(tmp_path: Path) -> None:
    caminho = tmp_path / "blog.yaml"
    caminho.write_text(
        "blog:\n"
        "  nome: Meu Blog\n"
        "  dominio: astrologia\n"
        "  dominio_extendido: astrologia e mapas astrais\n"
        "  descricao_curta: astrologia\n"
        "editorial:\n"
        "  min_palavras: 1500\n"
        "cta:\n"
        "  texto: fale comigo\n",
        encoding="utf-8",
    )

    config = carregar_config_blog(caminho)

    assert isinstance(config, ConfigBlog)
    assert config.nome == "Meu Blog"
    assert config.min_palavras == 1500
    assert config.cta_texto == "fale comigo"
    # campos opcionais devem cair pro default
    assert config.idioma == "português do Brasil"


# --------------------------------------------------------------------------- #
# chamar_groq / chamar_groq_json (HTTP mockado — nenhuma chamada de rede real)
# --------------------------------------------------------------------------- #

def _resposta_groq_ok(conteudo: str) -> Mock:
    resposta = Mock()
    resposta.status_code = 200
    resposta.json.return_value = {"choices": [{"message": {"content": conteudo}}]}
    return resposta


def test_chamar_groq_sucesso() -> None:
    with patch("requests.Session.post", return_value=_resposta_groq_ok("Olá!")) as post_mock:
        resultado = chamar_groq(
            "system", "user", api_key="gsk_teste", modelo="modelo-teste"
        )
    assert resultado == "Olá!"
    post_mock.assert_called_once()


def test_chamar_groq_http_erro_levanta_erro_groq() -> None:
    resposta = Mock()
    resposta.status_code = 500
    resposta.text = "erro interno"

    with patch("requests.Session.post", return_value=resposta):
        with pytest.raises(ErroGroq):
            chamar_groq("system", "user", api_key="gsk_teste", modelo="modelo-teste")


def test_chamar_groq_falha_de_conexao_levanta_erro_groq() -> None:
    with patch("requests.Session.post", side_effect=requests.exceptions.ConnectionError("boom")):
        with pytest.raises(ErroGroq):
            chamar_groq("system", "user", api_key="gsk_teste", modelo="modelo-teste")


def test_chamar_groq_json_sucesso() -> None:
    payload = {"campo": "valor"}
    with patch("requests.Session.post", return_value=_resposta_groq_ok(json.dumps(payload))):
        resultado = chamar_groq_json(
            "system", "user", api_key="gsk_teste", modelo="modelo-teste"
        )
    assert resultado == payload


def test_chamar_groq_json_resposta_invalida_levanta_erro() -> None:
    with patch("requests.Session.post", return_value=_resposta_groq_ok("isso não é json")):
        with pytest.raises(ErroGroq):
            chamar_groq_json("system", "user", api_key="gsk_teste", modelo="modelo-teste")
