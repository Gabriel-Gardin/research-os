# Research OS

Sistema pessoal de pesquisa para **engenharia de vibrações**, **detecção de vazamentos** e temas correlatos, com foco em:

- **biblioteca técnica pesquisável**
- **busca semântica sobre artigos, livros e notas**
- **memória persistente entre chats**
- **armazenamento de hipóteses, conclusões e dúvidas abertas**
- **integração com LLMs via MCP**

A ideia central é construir um ambiente onde o conhecimento não fique preso em PDFs soltos nem em conversas isoladas com LLMs.

---

## Visão geral

Este projeto implementa um **Research OS pessoal** com três blocos principais:

1. **Biblioteca de conhecimento**  
   Repositório de artigos, livros, teses, normas, notas e demais materiais técnicos.

2. **Memória de pesquisa**  
   Base estruturada de hipóteses, conclusões, decisões, dúvidas e resumos de sessões.

3. **Camada MCP**  
   Interface para que uma LLM possa consultar a biblioteca, recuperar memória de projeto e salvar novos insights.

Além disso, existe um quarto bloco transversal:

4. **Ingestão e curadoria**  
   Pipeline responsável por importar PDFs, extrair texto, gerar chunks, embeddings e organizar metadados.

---

## Objetivo

Este sistema foi concebido para suportar workflows de brainstorming e pesquisa técnica/acadêmica em temas como:

- propagação de ruído em tubulações
- correlação de sinais para localização de vazamentos
- vibrações estruturais
- hidrofones e acelerômetros piezoelétricos
- instrumentação, DSP e modelagem física

O objetivo não é apenas “buscar documentos”, mas também **acumular raciocínio ao longo do tempo**.

---

## Princípios do projeto

- **Conhecimento no centro, MCP na borda**
- **Não confiar em memória implícita do chat**
- **Separar bibliografia de memória de pesquisa**
- **Salvar unidades semânticas curadas, não transcripts brutos**
- **Começar simples**
- **Evitar superarquitetura prematura**
- **Postgres + pgvector primeiro, grafo depois se necessário**

---

## Arquitetura

### Blocos principais

#### 1. Biblioteca de conhecimento
Responsável por armazenar e indexar:

- papers
- livros
- teses
- normas
- notas de leitura
- documentação técnica

Cada documento pode ser processado em:

- texto extraído
- chunks
- embeddings
- metadados
- referências de página/seção

#### 2. Memória de pesquisa
Responsável por armazenar:

- hipóteses
- conclusões
- perguntas em aberto
- decisões
- observações experimentais
- resumos de sessões
- relações entre memórias e evidências

Exemplo de item de memória:

- tipo: `hypothesis`
- texto: `o ruído correlacionado em atraso zero pode ser decorrente de acoplamento estrutural`
- status: `open`
- confiança: `medium`
- evidências: documentos, notas, resumos de chat

#### 3. MCP
Camada de acesso para a LLM.

Exemplos de tools planejadas:

- `search_library`
- `get_passage`
- `save_hypothesis`
- `save_conclusion`
- `get_project_memory`
- `list_open_questions`

#### 4. Ingestão e curadoria
Pipeline responsável por:

- importar arquivos
- extrair texto
- quebrar em chunks
- gerar embeddings
- popular o banco
- deduplicar documentos
- organizar metadados

---

## Stack

### Backend de dados
- **Supabase self-hosted**
- **Postgres**
- **pgvector**
- **Supabase Storage**

### Infra local
- **Docker**
- **Docker Compose**

### Serviços do projeto
- `research-worker`  
  Pipeline de ingestão e indexação

- `research-mcp`  
  Servidor MCP customizado

- `research-admin`  
  Ferramentas auxiliares de inspeção do banco

---

## Estrutura do repositório

```text
research-os/
├─ supabase/
│  └─ docker/
├─ docker-compose.research.yml
├─ .env.research
├─ sql/
│  └─ 001_init.sql
├─ imports/
├─ research-worker/
│  ├─ Dockerfile
│  └─ ...
├─ research-mcp/
│  ├─ Dockerfile
│  └─ ...
└─ README.md