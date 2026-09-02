# AnaliseIA

Sistema de triagem científica automatizada para revisão sistemática de literatura, desenvolvido para analisar e triar artigos científicos massivamente usando Inteligência Artificial e LLMs rodando localmente (via GPU).

Este sistema inclui suporte **modular** para protocolos de triagem: você pode facilmente ajustar as regras de decisão, escopo e algoritmos que a IA utilizará modificando um arquivo de texto.

---

## 🚀 Arquitetura e Fluxo

1. **Scanner de PDFs** (`scan`): Extrai e converte todos os PDFs encontrados no diretório (mesmo em subpastas) utilizando a biblioteca *MarkItDown*. O texto é convertido e sumarizado em arquivos Markdown.
2. **Triagem por IA** (`analyze`): Um orquestrador multi-thread lê os textos extraídos e injeta no modelo (LLM) seguindo estritamente as regras definidas no protocolo (prompt modular).
3. **Consolidação** (`report`): As respostas da IA são capturadas, estruturadas e salvas em um banco de dados local (`sqlite3`), gerando na saída relatórios prontos para revisão humana.

---

## 🛠️ Requisitos e Configuração

### 1. Docker e Ollama
Para garantir o uso de aceleração por GPU (Placa de Vídeo) e evitar problemas de compatibilidade (especialmente no Linux), utilizamos o Docker para rodar a aplicação, mas o LLM é executado através de uma instalação nativa do Ollama.

**Instalação do Ollama (Linux host):**
```bash
# 1. Instale o Ollama diretamente no sistema hospedeiro
curl -fsSL https://ollama.com/install.sh | sh

# 2. Faça o download do modelo (recomendado: Qwen2.5 7B)
ollama run qwen2.5
# (Após o download concluir, você pode sair do chat digitando /bye)
```

### 2. Acesso ao Docker
A aplicação em si utiliza Docker para isolar o ambiente Python, sem necessidade de instalar as dependências de código na sua máquina. O Docker precisa estar rodando no contexto nativo.

Certifique-se de usar o contexto nativo e ter permissão:
```bash
docker context use default
sudo chmod 666 /var/run/docker.sock
```

---

## ⚙️ Diretrizes de Triagem (O Protocolo Modular)

A IA não toma decisões arbitrárias. Ela segue cegamente um algoritmo de avaliação definido pelo arquivo:
📄 **`protocolo_triagem.txt`**

Este arquivo é lido a cada execução e dita exatamente as regras. O protocolo atual obriga a IA a responder a um questionário de 15 etapas e a gerar a saída final em formato JSON estrito.
- Para modificar os critérios (por exemplo, alterar doenças, condições, marcadores), basta editar o arquivo `protocolo_triagem.txt`. Não é necessário alterar código Python.
- Há também um fluxograma interativo e documentação visual da lógica de decisão localizado em `docs/fluxograma_triagem.html`.

---

## 🏃 Como Utilizar o Sistema

O script `start.sh` encapsula a complexidade do Docker e lida com toda a automação. 

**Passo 1: Construir a Imagem (Primeira Vez)**
```bash
./start.sh build
```

**Passo 2: Digitalização dos Arquivos**
Coloque seus artigos na pasta `pdfs/` (ou onde estiver configurado no sistema).
```bash
./start.sh scan
```

**Passo 3: Iniciar a Análise com a IA**
O comando aceita o número de *threads* como parâmetro. Use quantas a sua máquina aguentar (o padrão é 10). Se estiver usando GPU, 10 ou mais threads farão o sistema voar!
```bash
./start.sh analyze 10
```
> *Nota: O processo irá se comunicar com o Ollama rodando no seu localhost.*

**Passo 4: Monitorar o Status (Opcional)**
Se quiser verificar o andamento pelo banco de dados em outra janela:
```bash
./start.sh status
```

**Passo 5: Gerar os Relatórios Finais**
Ao término, gere as planilhas CSV e logs consolidados.
```bash
./start.sh report
```

### Quer fazer tudo de uma vez?
O comando `all` constrói, faz scan, analisa e gera os relatórios automaticamente:
```bash
./start.sh all 10
```

---

## 📂 Saídas Geradas

Após rodar o relatório, a pasta `data/reports/` (ou similar) conterá:
- `resultados.csv` / `resultados.json`: Dados brutos em formato estruturado.
- `INCLUIDOS.md`: Lista amigável apenas dos artigos aprovados pela IA, com a justificativa de suporte.
- `EXCLUIDOS.md`: Lista dos reprovados com os respectivos códigos de erro (ex: `E04`, `E07`).
- `REVISAO_MANUAL.md`: Arquivos que a IA não teve confiança total, com base na regra de segurança (Prevenção de falsas exclusões).
