"""Testes do módulo publicador_wordpress.py — publicação no WordPress (etapa 4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from imperio_dos_sete import publicador_wordpress as pw
from imperio_dos_sete.comum import ErroConfiguracao


# --------------------------------------------------------------------------- #
# converter_corpo_html
# --------------------------------------------------------------------------- #

def test_converter_corpo_html_titulo_e_paragrafo() -> None:
    html = pw.converter_corpo_html("# Título\n\nUm parágrafo simples.")
    assert "<h1>Título</h1>" in html
    assert "<p>Um parágrafo simples.</p>" in html


def test_converter_corpo_html_lista() -> None:
    html = pw.converter_corpo_html("- item um\n- item dois")
    assert "<li>item um</li>" in html
    assert "<li>item dois</li>" in html


def test_converter_corpo_html_quebra_de_linha_simples_vira_br() -> None:
    # extensão nl2br: uma quebra de linha simples dentro do parágrafo vira <br>
    html = pw.converter_corpo_html("linha um\nlinha dois")
    assert "<br" in html


# --------------------------------------------------------------------------- #
# CacheTags — usa uma sessão HTTP mockada, nenhuma chamada de rede real
# --------------------------------------------------------------------------- #

def _sessao_mock_com_tags(tags_existentes: list[dict]) -> Mock:
    sessao = Mock()

    resposta_get = Mock()
    resposta_get.status_code = 200
    resposta_get.json.return_value = tags_existentes
    sessao.get.return_value = resposta_get

    return sessao


def test_cache_tags_reaproveita_tag_existente() -> None:
    sessao = _sessao_mock_com_tags([{"id": 10, "name": "Pombagira"}])
    cache = pw.CacheTags(sessao)

    tag_id = cache.obter_ou_criar("pombagira")  # case-insensitive

    assert tag_id == 10
    sessao.post.assert_not_called()  # não deveria criar, já existe


def test_cache_tags_cria_tag_inexistente() -> None:
    sessao = _sessao_mock_com_tags([])  # nenhuma tag cadastrada ainda

    resposta_post = Mock()
    resposta_post.json.return_value = {"id": 99}
    sessao.post.return_value = resposta_post

    cache = pw.CacheTags(sessao)
    tag_id = cache.obter_ou_criar("Nova Tag")

    assert tag_id == 99
    sessao.post.assert_called_once()


def test_cache_tags_carrega_apenas_uma_vez(monkeypatch: pytest.MonkeyPatch) -> None:
    sessao = _sessao_mock_com_tags([{"id": 1, "name": "tag-a"}])
    monkeypatch.setattr(pw, "autenticacao", lambda: None)

    cache = pw.CacheTags(sessao)
    cache.obter_ou_criar("tag-a")
    cache.obter_ou_criar("tag-a")

    assert sessao.get.call_count == 1  # cache evita repetir a busca


def test_cache_tags_montar_ids_varias_tags() -> None:
    sessao = _sessao_mock_com_tags([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    cache = pw.CacheTags(sessao)

    assert cache.montar_ids(["a", "b"]) == [1, 2]


# --------------------------------------------------------------------------- #
# coletar_arquivos
# --------------------------------------------------------------------------- #

def test_coletar_arquivos_arquivo_especifico_inexistente() -> None:
    args = pw.parse_args(["--arquivo", "/caminho/que/nao/existe.md"])
    with pytest.raises(ErroConfiguracao):
        pw.coletar_arquivos(args)


def test_coletar_arquivos_pasta_vazia_levanta_erro(tmp_path: Path) -> None:
    args = pw.parse_args(["--pasta", str(tmp_path)])
    with pytest.raises(ErroConfiguracao):
        pw.coletar_arquivos(args)


def test_coletar_arquivos_pasta_com_arquivos(tmp_path: Path) -> None:
    (tmp_path / "materia1.md").write_text("conteúdo", encoding="utf-8")
    (tmp_path / "materia2.md").write_text("conteúdo", encoding="utf-8")

    args = pw.parse_args(["--pasta", str(tmp_path)])
    arquivos = pw.coletar_arquivos(args)

    assert len(arquivos) == 2
