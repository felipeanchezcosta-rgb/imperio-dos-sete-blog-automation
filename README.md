# Império dos Sete — Automação de blog com IA

![Testes](https://github.com/SEU_USUARIO/imperio-dos-sete/actions/workflows/tests.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Licença MIT](https://img.shields.io/badge/licença-MIT-green)

Pipeline em 4 etapas que gera pautas, escreve os artigos, cria a imagem de
destaque e publica tudo no WordPress — usando a API da [Groq](https://groq.com)
(gratuita) para o texto e [Pollinations.ai](https://pollinations.ai) ou
[Together.ai](https://together.ai) para a imagem.

```
1. gerador-temas          -> gera ideias de pauta (saidas/*.json)
2. escrever-materias      -> escreve os artigos completos (materias/*.md)
3. gerar-imagem           -> gera a imagem de destaque (imagens/<slug>.jpg)
4. publicar-wordpress     -> publica texto + imagem no WordPress
```

O projeto não sabe nada sobre nenhum nicho específico — a identidade do
blog (nome, domínio, tom de voz, autores de referência, CTA) vem de
`config/blog.yaml`. Editando esse arquivo, o mesmo código publica pra
qualquer outro blog, sem tocar em código-fonte.

## Estrutura do projeto

```
src/imperio_dos_sete/       <- código-fonte (pacote Python instalável)
  comum.py                   funções e classes compartilhadas pelas 4 etapas
  gerador_temas.py            etapa 1
  escritor_materias.py        etapa 2
  gerador_imagem.py           etapa 3
  publicador_wordpress.py     etapa 4
tests/                       <- suíte de testes (pytest)
config/
  blog.yaml.example           template de identidade do blog (copie -> blog.yaml)
  temas_publicados.json       lista de temas já publicados (editável)
.github/workflows/tests.yml  <- CI: roda lint + testes a cada push/PR
pyproject.toml                empacotamento e dependências
```

Pastas geradas em tempo de execução (não versionadas): `saidas/`,
`materias/`, `imagens/`.

## Instalação

Requer Python 3.11+.

```bash
git clone https://github.com/SEU_USUARIO/imperio-dos-sete.git
cd imperio-dos-sete
pip install -e ".[dev]"        # instala o pacote + ferramentas de dev (pytest, ruff, mypy)

cp .env.example .env                       # preencha com suas chaves (veja a tabela abaixo)
cp config/blog.yaml.example config/blog.yaml   # edite com a identidade do seu blog
```

| Variável | Onde conseguir |
|---|---|
| `GROQ_API_KEY` | https://console.groq.com/keys (grátis) |
| `TOGETHER_API_KEY` | https://api.together.ai/settings/api-keys (opcional, só se usar `--provedor together`) |
| `WP_URL`, `WP_USUARIO`, `WP_APP_PASSWORD` | Seu WordPress: `wp-admin -> Usuários -> Perfil -> Senhas de Aplicativo` |

**Nunca comite o `.env` real** nem o `config/blog.yaml` real se ele tiver
dado sensível — só os `.example` devem ir pro Git. O `.gitignore` já
bloqueia isso.

## Uso

```bash
# 1. Gerar 20 ideias de pauta
gerar-temas

# 2. Escrever os artigos escolhidos (ex: ideias 1 e 7)
escrever-materias --indices 1,7
# --paralelo 3 escreve várias ao mesmo tempo (mais rápido, cuidado com rate limit)

# 3. Gerar a imagem de destaque de uma matéria
gerar-imagem

# 4. Publicar no WordPress (texto + imagem destacada automaticamente)
publicar-wordpress --pasta materias
```

Por padrão tudo é publicado como **rascunho** (`WP_STATUS=draft` no `.env`)
pra você revisar antes de ir pro ar.

> Se preferir não instalar o pacote, dá pra rodar cada etapa como módulo:
> `python -m imperio_dos_sete.gerador_temas`, etc.

## Rodando os testes

```bash
pytest                          # roda a suíte completa
pytest --cov                    # com relatório de cobertura
ruff check src tests            # lint
mypy src                        # checagem de tipos
```

O CI (`.github/workflows/tests.yml`) roda lint + testes automaticamente em
todo push e pull request, em Python 3.11 e 3.12.

## Arquitetura

`comum.py` concentra o que é compartilhado entre as 4 etapas: cliente HTTP
com retry/backoff automático (`sessao_com_retry`), cliente da API da Groq
(`chamar_groq` / `chamar_groq_json`), parsing do frontmatter dos artigos,
`slugificar`, escrita atômica de arquivo (evita corromper arquivo se o
processo for interrompido no meio) e o carregamento tipado de
`config/blog.yaml` (`ConfigBlog`). Os outros 4 módulos importam daqui em
vez de duplicar essa lógica — cada um sabe fazer só a própria etapa do
pipeline, e nenhum deles conhece o nicho específico do blog: tudo isso é
injetado via `ConfigBlog`.

Erros de configuração (`ErroConfiguracao`), de chamada à Groq (`ErroGroq`)
e de chamada ao WordPress (`ErroWordPress`) são exceções específicas — em
vez de `sys.exit()` espalhado pelo meio das funções, cada uma delas fica
reutilizável (chamável de outro script, ou de um teste) sem derrubar o
processo inteiro à força.

## Adaptando pro seu blog

1. Copie `config/blog.yaml.example` para `config/blog.yaml`.
2. Preencha `nome`, `dominio`, `dominio_extendido` e `descricao_curta` — são
   obrigatórios, o script recusa rodar sem eles.
3. Ajuste `editorial.tom`, `editorial.voz` e `autores_referencia` pra bater
   com a identidade editorial do seu blog.
4. Ajuste `cta` com a chamada pra ação e o link que devem aparecer no fim
   de todo artigo.
5. `gerador_imagem.py` tem alguns elementos visuais (cena, marca) pensados
   pro nicho original deste projeto — para um nicho totalmente diferente,
   vale revisar `montar_prompt_imagem` e os prompts de sistema desse
   arquivo além do `blog.yaml`.

## Segurança

- O `.env` e o `config/blog.yaml` real nunca devem ser commitados — use os
  `.example` como referência.
- Se uma chave de API vazar (print, log, chat, commit por engano), trate-a
  como comprometida e gere uma nova imediatamente no provedor.
- Todas as chamadas HTTP usam timeout e retry com backoff; nenhuma chave é
  incluída em mensagens de erro ou logs.

## Licença

[MIT](LICENSE) — use, modifique e reaproveite como quiser.
