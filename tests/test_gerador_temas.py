"""Testes do módulo gerador_temas.py — geração de pautas (etapa 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from imperio_dos_sete import gerador_temas as gt


# --------------------------------------------------------------------------- #
# Ideia / ResultadoIdeias
# --------------------------------------------------------------------------- #

def test_ideia_de_dict_le_campos_em_camel_case() -> None:
    bruto = {
        "numero": 3,
        "vertente": "Banhos",
        "titulo": "Como fazer banho de atração em apartamento",
        "microNicho": "quem não tem quintal",
        "intencaoBusca": "informacional",
    }
    ideia = gt.Ideia.de_dict(bruto)

    assert ideia.numero == 3
    assert ideia.micro_nicho == "quem não tem quintal"
    assert ideia.intencao_busca == "informacional"


def test_ideia_de_dict_com_campos_faltando_usa_default() -> None:
    ideia = gt.Ideia.de_dict({})
    assert ideia.numero == 0
    assert ideia.titulo == ""


def test_resultado_ideias_de_dict() -> None:
    bruto = {"ideias": [{"numero": 1, "titulo": "A"}, {"numero": 2, "titulo": "B"}]}
    resultado = gt.ResultadoIdeias.de_dict(bruto)
    assert len(resultado.ideias) == 2
    assert resultado.ideias[0].titulo == "A"


# --------------------------------------------------------------------------- #
# carregar_temas_publicados
# --------------------------------------------------------------------------- #

def test_carregar_temas_publicados_arquivo_ausente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gt, "ARQUIVO_TEMAS_PUBLICADOS", tmp_path / "nao-existe.json")
    assert gt.carregar_temas_publicados() == []


def test_carregar_temas_publicados_le_lista(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    caminho = tmp_path / "temas_publicados.json"
    caminho.write_text(json.dumps({"temas": ["tema a", "tema b"]}), encoding="utf-8")
    monkeypatch.setattr(gt, "ARQUIVO_TEMAS_PUBLICADOS", caminho)

    assert gt.carregar_temas_publicados() == ["tema a", "tema b"]


def test_carregar_temas_publicados_json_invalido_nao_quebra(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    caminho = tmp_path / "temas_publicados.json"
    caminho.write_text("{ isso não é json válido", encoding="utf-8")
    monkeypatch.setattr(gt, "ARQUIVO_TEMAS_PUBLICADOS", caminho)

    assert gt.carregar_temas_publicados() == []


# --------------------------------------------------------------------------- #
# montar_prompt_usuario
# --------------------------------------------------------------------------- #

def test_montar_prompt_usuario_inclui_temas_e_quantidade() -> None:
    config = gt.ConfigBlog(
        nome="Meu Blog", dominio="astrologia", dominio_extendido="astrologia",
        descricao_curta="astrologia",
    )
    prompt = gt.montar_prompt_usuario(5, ["tema já publicado"], config)

    assert "5 ideias novas" in prompt
    assert "tema já publicado" in prompt
    assert "Meu Blog" in prompt


def test_montar_prompt_usuario_sem_temas_publicados() -> None:
    config = gt.ConfigBlog(
        nome="Meu Blog", dominio="astrologia", dominio_extendido="astrologia",
        descricao_curta="astrologia",
    )
    prompt = gt.montar_prompt_usuario(5, [], config)
    assert "nenhum registrado ainda" in prompt


# --------------------------------------------------------------------------- #
# salvar_ideias
# --------------------------------------------------------------------------- #

def test_salvar_ideias_escreve_json_no_formato_esperado(tmp_path: Path) -> None:
    resultado = gt.ResultadoIdeias(
        ideias=[
            gt.Ideia(numero=1, vertente="Banhos", titulo="Título 1",
                     micro_nicho="nicho 1", intencao_busca="informacional"),
        ]
    )
    caminho_saida = tmp_path / "ideias.json"

    caminho_salvo = gt.salvar_ideias(resultado, caminho_saida)

    assert caminho_salvo == caminho_saida
    dados = json.loads(caminho_saida.read_text(encoding="utf-8"))
    assert dados["ideias"][0]["titulo"] == "Título 1"
    assert dados["ideias"][0]["microNicho"] == "nicho 1"


# --------------------------------------------------------------------------- #
# parse_args
# --------------------------------------------------------------------------- #

def test_parse_args_valores_padrao() -> None:
    args = gt.parse_args([])
    assert args.quantidade == gt.QUANTIDADE_IDEIAS_PADRAO
    assert args.saida is None


def test_parse_args_quantidade_zero_ou_negativa_falha() -> None:
    with pytest.raises(SystemExit):
        gt.parse_args(["--quantidade", "0"])
