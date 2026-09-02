# Buscador de Artigos Científicos (PubMed, Embase, LILACS)

CLI em Python para buscar artigos científicos a partir de uma query, extrair seus respectivos DOIs e deduplicá-los.

## Instalação

1. Recomenda-se criar um ambiente virtual (Python 3.11+):
   ```bash
   python -m venv venv
   source venv/bin/activate  # ou venv\Scripts\activate no Windows
   ```

2. Instalar dependências:
   ```bash
   pip install -r requirements.txt
   ```

3. Configurar credenciais:
   Copie `.env.example` para `.env` e insira suas credenciais da Embase (e opcionalmente do PubMed).
   ```bash
   cp .env.example .env
   ```

## Execução

Edite o arquivo `quary.txt` com a sua query de busca, depois execute:
```bash
python src/main.py
```

## Fallback LILACS (Busca Manual)

Como o LILACS não possui uma API oficial REST, nosso conector consulta o Portal Regional da BVS passando parâmetros específicos via URL (`output=ris`).
Caso a estrutura do portal seja modificada e quebre o script, você pode exportar manualmente os resultados em RIS e processá-los:

1. Acesse https://pesquisa.bvsalud.org/portal/advanced/
2. Faça a busca com o filtro de banco de dados `db:"LILACS"`.
3. Na página de resultados, clique na opção de "Exportar" selecionando o formato **RIS** (Citation).
4. O arquivo gerado conterá a tag `DO  -` para DOIs, quando existirem.

