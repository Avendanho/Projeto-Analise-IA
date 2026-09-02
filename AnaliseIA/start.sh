#!/bin/bash

# start.sh - Helper script to initialize and run the AnaliseIA system via Docker

show_help() {
    echo "Uso: ./start.sh [comando]"
    echo ""
    echo "Comandos disponíveis:"
    echo "  build    - Constrói a imagem do Docker (necessário na primeira vez)"
    echo "  scan     - Executa o comando 'scan' para processar os PDFs na pasta pdfs/"
    echo "  analyze  - Executa o comando 'analyze' para realizar a análise dos PDFs processados"
    echo "  status   - Mostra o status do banco de dados"
    echo "  report   - Gera relatórios com os resultados"
    echo "  all      - Constrói a imagem e executa scan, analyze e report em sequência"
    echo "  shell    - Abre um terminal bash dentro do container para debug"
}

if ! command -v docker-compose &> /dev/null && ! command -v docker compose &> /dev/null; then
    echo "Erro: docker-compose não encontrado. Por favor, instale o Docker e o Docker Compose."
    exit 1
fi

COMPOSE_CMD="docker compose"

case "$1" in
    build)
        echo "Construindo a imagem Docker..."
        $COMPOSE_CMD build
        ;;
    scan)
        echo "Iniciando Grobid (Leitor de PDF)..."
        $COMPOSE_CMD up -d grobid
        echo "Executando o scan..."
        $COMPOSE_CMD run --rm analiseia scan
        ;;
    analyze)
        WORKERS=${2:-4}
        echo "Executando a análise com Qwen2.5 usando $WORKERS threads..."
        $COMPOSE_CMD run --rm -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" -e LLM_MODEL="qwen2.5" analiseia analyze --workers "$WORKERS"
        ;;
    status)
        echo "Verificando o status..."
        $COMPOSE_CMD run --rm analiseia status
        ;;
    report)
        echo "Gerando relatórios..."
        $COMPOSE_CMD run --rm analiseia report
        ;;
    all)
        WORKERS=${2:-4}
        echo "Iniciando processo completo..."
        $COMPOSE_CMD build
        $COMPOSE_CMD up -d grobid
        $COMPOSE_CMD run --rm analiseia scan
        $COMPOSE_CMD run --rm -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" -e LLM_MODEL="qwen2.5" analiseia analyze --workers "$WORKERS"
        $COMPOSE_CMD run --rm analiseia report
        echo "Processo concluído com sucesso!"
        ;;
    shell)
        $COMPOSE_CMD run --rm --entrypoint /bin/bash analiseia
        ;;
    *)
        show_help
        ;;
esac
