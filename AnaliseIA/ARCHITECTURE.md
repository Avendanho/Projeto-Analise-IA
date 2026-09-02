# Arquitetura do AnaliseIA

O sistema foi desenhado para processar os PDFs de maneira isolada e determinística, preparando o terreno para injeção de IA.

1. **PDF Processor (PyMuPDF):** Extrai o texto, página por página, e levanta flag de qualidade (`HIGH`/`LOW`) para sugerir a aplicação de OCR.
2. **Metadata Extractor:** Busca informações bibliográficas nas primeiras páginas.
3. **Database (SQLite FTS5):** Guarda os metadados, cria índice textual das páginas para o `EvidenceRetriever`.
4. **Evidence Retriever:** Faz a busca inteligente (semântica ou léxica) usando chaves pré-determinadas (TEA, GENÉTICA, INFLAMAÇÃO) restritas a certas seções.
5. **Decision Engine:** Toma as 15 respostas brutas extraídas e decide a inclusão/exclusão (E01 a E12) deterministicamente.
6. **Reporting:** Emite relatórios estruturados (CSV, Markdown).
