# Memex-Wiki: Local Hybrid Memory & Knowledge Graph for AI Agents (v0.2)

[English](#english) | [Русский](#русский)

---

# English

Memex-Wiki is a secure, offline-first, and resilient long-term memory system designed for AI agents (specifically integrated with Google Antigravity 2.0, Claude Code, Cursor, Cline, Roo Code, and other MCP clients). It compiles project documents and codebase knowledge into a human-readable Obsidian Markdown vault, automatically building a 3D visual knowledge graph (Concept links). 

For queries, it uses the **RLM (Recursive Long-context Memory) Engine** to sequentially filter text chunks, avoiding context overload, VRAM OOM, and CPU overheating on local hardware.

## Why Memex-Wiki? Long-Term Memory for AI Agents & OS

AI agents and multi-agent frameworks (such as Claude Code, Cursor, Cline, Roo Code, OpenDevin, Devika, or Hermes) face critical challenges when dealing with long-term memory and knowledge retention:

*   **The VRAM Bottleneck**: Processing giant repositories or codebase histories locally frequently triggers Out-Of-Memory (OOM) errors and slows down consumer hardware.
*   **"Lost in the Middle"**: LLMs tend to forget details buried in the middle of extremely long context prompts.
*   **Context Inflation Costs**: Continually feeding raw, uncompressed source files to cloud models results in massive token consumption and high API bills.
*   **Volatile & Unreadable Memory (Vector DBs)**: Traditional Vector RAG databases are mathematical black boxes. Humans cannot inspect, edit, or curate what the agent remembers.
*   **Hallucinations & False Reflections**: If an agent starts hallucinating, there's no version control to trace and roll back incorrect deductions.

**Memex-Wiki solves this by marrying three principles:**
1.  **Knowledge Compilation (by Karpathy)**: The agent distills and atomizes raw project files *on write*, not on read. Concepts are written to Obsidian.
2.  **Recursive Language Models (RLM)**: A lightweight local Python search engine cuts text into tiny chunks and polls the model sequentially, keeping context windows compact and VRAM usage minimal.
3.  **Human-in-the-Loop & Git Rollbacks**: Memory is stored in readable Markdown files with YAML headers in your Obsidian Vault. Every memory update commits to Git, giving humans complete auditability and version control.

## Visual Interface

| Dark Theme | Light Theme |
| :---: | :---: |
| ![Dark Theme](Dark_theme.png) | ![Light Theme](White_theme.png) |

## Architecture & Data Flow

1. **Raw Sources (Read-Only)**: The system monitors your project folders (scripts, markdown, readmes).
2. **AI Atomization (Ingest)**: A local LLM (via Ollama) or Gemini API analyzes files, compresses facts, and creates concept pages in Obsidian.
3. **Obsidian Graph**: Links act as graph edges. You can visually explore connections in 3D.
4. **RLM Search**: The AI reads only relevant graph nodes, ensuring accurate responses without hallucinations.

---

## Installation & Setup

### Prerequisites
* **Python 3.10+** (macOS, Windows, Linux)
* **Git** installed and configured
* **Ollama** (optional, for fully local execution)

---

### 🍏 macOS Setup
1. **Clone & Open**:
   ```bash
   git clone https://github.com/your-username/Memex-Wiki.git
   cd Memex-Wiki
   ```
2. **Install Dependencies**:
   ```bash
   chmod +x setup_env.sh
   ./setup_env.sh
   ```
3. **Configure**:
   ```bash
   cp config.example.yaml config.yaml
   # Open config.yaml and edit paths
   ```
4. **Run Server**:
   ```bash
   source .venv/bin/activate
   python3 src/web_server.py
   ```
   Open `http://localhost:8000` in your browser.

---

### 🪟 Windows Setup
1. **Clone & Open**:
   Use Git Bash or Command Prompt:
   ```cmd
   git clone https://github.com/your-username/Memex-Wiki.git
   cd Memex-Wiki
   ```
2. **Create Virtual Environment**:
   ```cmd
   python -m venv .venv
   call .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Configure**:
   ```cmd
   copy config.example.yaml config.yaml
   :: Open config.yaml in Notepad and adjust paths (use forward slashes, e.g., C:/Users/name/Obsidian/Vault)
   ```
4. **Run Server**:
   ```cmd
   python src/web_server.py
   ```
   Open `http://localhost:8000` in your browser.

---

### 🐧 Linux Setup
1. **Clone & Open**:
   ```bash
   git clone https://github.com/your-username/Memex-Wiki.git
   cd Memex-Wiki
   ```
2. **Install Dependencies**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Configure**:
   ```bash
   cp config.example.yaml config.yaml
   # Open config.yaml and configure paths
   ```
4. **Run Server**:
   ```bash
   python3 src/web_server.py
   ```
   Open `http://localhost:8000` in your browser.

---

## Safety & Security Guidelines 🛡️

Memex-Wiki is designed with privacy in mind. Before hosting or pushing changes to public GitHub repositories, make sure:
1. **Local Paths & API Keys**: Always add `config.yaml` to `.gitignore`. Distribute configurations using `config.example.yaml`.
2. **Secret Redaction**: The backend (`src/security_filter.py`) automatically strips:
   * Google API Keys / Gemini Tokens
   * OpenAI API keys
   * Database connection strings (Postgres, Mongo, Redis, etc.)
   * Passwords and private keys from the ingested project files.
3. **No External Tracking**: When running on local Ollama, no data is sent outside your machine.

---

## MCP Integration for AI Agents & Clients (Claude Code, Cursor, Cline, etc.)

To hook up this memory system to your AI agents or development tools, add the following configuration depending on your client:

### 1. Claude Code
Add to your global Claude Code MCP config (usually located at `~/.support/claude/mcp.json` or by running `claude mcp add`):
```json
{
  "mcpServers": {
    "memex-wiki": {
      "command": "/absolute/path/to/Memex-Wiki/.venv/bin/python3",
      "args": ["/absolute/path/to/Memex-Wiki/src/mcp_server.py"]
    }
  }
}
```

### 2. Cursor / VS Code
Go to Settings -> Features -> MCP, click **+ Add New MCP Server**:
*   **Name**: Memex-Wiki
*   **Type**: command
*   **Command**: `/absolute/path/to/Memex-Wiki/.venv/bin/python3 /absolute/path/to/Memex-Wiki/src/mcp_server.py`

### 3. Cline / Roo Code / Roo Clinic
Add to your `cline_mcp_settings.json` (or `roo_mcp_settings.json`):
```json
{
  "mcpServers": {
    "memex-wiki": {
      "command": "/absolute/path/to/Memex-Wiki/.venv/bin/python3",
      "args": ["/absolute/path/to/Memex-Wiki/src/mcp_server.py"],
      "disabled": false,
      "alwaysAllow": []
    }
  }
}
```

### 4. Google Antigravity 2.0
Configure the stdio server block in your agent system configurations:
* **Command**: `/path/to/Memex-Wiki/.venv/bin/python` (or `python.exe` on Windows)
* **Arguments**: `/path/to/Memex-Wiki/src/mcp_server.py`

### Exposed Tools
* `ingest_source(filename)`: Scan, atomize, and commit a project file.
* `query_memory(query)`: RLM-powered query across the compiled graph.
* `lint_memory()`: Health audit of wiki connections and metadata.

---

## 🤖 Self-Deployment Prompt (For AI Agents)

If you want an AI coding assistant (like Claude, Cursor, Cline, or Antigravity) to deploy this repository and connect it to itself automatically, paste the following prompt:

```markdown
You are an autonomous AI deployment agent. Your goal is to install, configure, and connect the Memex-Wiki long-term memory system to yourself.

Please perform the following steps:
1. Check if git, python3, and pip are installed.
2. Clone the repository: `git clone https://github.com/your-username/Memex-Wiki.git` and enter the folder.
3. Run the setup script `./setup_env.sh` (or create a virtual environment manually and install requirements.txt).
4. Copy `config.example.yaml` to `config.yaml`.
5. Locate my Obsidian Vault path (if I have one) or create a local folder `my_memex_vault` to act as the vault, and update `config.yaml` with the correct absolute paths.
6. Check if Ollama is installed and running on this machine:
   - If not, download and install Ollama, start the service, and pull the `qwen2.5:3b` model.
   - If Ollama is running, pull the `qwen2.5:3b` model.
   - If I prefer a cloud backend, ask me for my Gemini API key and write it to `config.yaml`.
7. Run the test suite: `.venv/bin/python3 src/test_memex.py` to ensure everything works.
8. Automatically register this MCP server in your own configuration:
   - Command: `<path_to_venv_python>`
   - Args: `[<path_to_mcp_server>]`
   - (For example, write to my ~/.support/claude/mcp.json or cline_mcp_settings.json).
9. Report back once completed and test the memory by calling the `lint_memory` tool.
```

---

# Русский

**Memex-Wiki** — это безопасная, автономная и приватная система долгосрочной памяти для ИИ-агентов (в частности, для интеграции с Google Antigravity 2.0, Claude Code, Cursor, Cline, Roo Code и другими MCP-клиентами). Система аккумулирует знания в виде человекочитаемого графа связей (Markdown-файлов с YAML-метаданными) в вашем Obsidian Vault и автоматически строит 3D-визуализацию графа (связи концептов).

Для поиска используется движок **RLM (Recursive Long-context Memory)**, который осуществляет последовательную фильтрацию текстовых чанков, исключая перегрузку контекста, перегрев процессора и нехватку видеопамяти (VRAM OOM).

## Почему именно Memex-Wiki? Проблема долгосрочной памяти ИИ-агентов

ИИ-агенты и мультиагентные платформы (такие как Claude Code, Cursor, Cline, Roo Code, OpenDevin, Devika или Hermes) сталкиваются с критическими проблемами при работе с памятью и накоплением знаний:

*   **Ограничение VRAM (OOM)**: Попытка скормить локальной модели длинную историю кода или гигантские файлы приводит к вылетам по памяти и зависанию железа.
*   **Эффект «Утери в середине» (Lost in the Middle)**: Модели склонны забывать критические детали, если они погребены в середине огромного промпта.
*   **Высокие расходы на API**: Постоянная отправка сырых, не сжатых логов и файлов в облачные модели быстро расходует токены и увеличивает счета.
*   **Нечитаемая и нестабильная память (Векторные БД)**: Традиционные векторные RAG базы — это математический «черный ящик». Человек не может вручную проверить, отредактировать или упорядочить то, что помнит агент.
*   **Галлюцинации и ложная рефлексия**: Если агент делает ложные выводы в памяти, у вас нет контроля версий (Git), чтобы отследить и откатить ошибку мышления.

**Memex-Wiki решает эти боли на стыке трех подходов:**
1.  **Компиляция знаний (по Карпатому)**: Агент сжимает и структурирует информацию *в момент записи*, а не чтения. Концепты сразу раскладываются по Obsidian-файлам.
2.  **Вычислительный движок RLM**: Поиск нарезает текст на чанки и опрашивает модель последовательно, гарантируя минимальную нагрузку на VRAM.
3.  **Контроль человека и Git**: Все факты лежат в понятных Markdown-файлах с YAML-шапками. Любое обновление памяти коммитится в Git, что позволяет легко отслеживать изменения и делать откат (`git rollback`).

## Интерфейс панели управления

| Темная тема | Светлая тема |
| :---: | :---: |
| ![Темная тема](Dark_theme.png) | ![Светлая тема](White_theme.png) |

## Архитектура системы и потоки данных

1. **Исходные файлы (Raw Sources)**: Папка с кодом, заметками и файлами ваших проектов (доступна только на чтение).
2. **ИИ-Атомизация (Импорт)**: Локальная модель Ollama или облачный Gemini API анализируют файлы и создают карточки концептов в Obsidian.
3. **Граф связей (Obsidian)**: Связи выступают дорожной картой. Вы можете вращать и исследовать 3D-граф в реальном времени.
4. **RLM-Поиск**: При запросе ИИ читает только связанные узлы графа, генерируя точные ответы без галлюцинаций.

---

## Настройка и установка

### Системные требования
* **Python 3.10+** (macOS, Windows, Linux)
* **Git** установленный и настроенный в системе
* **Ollama** (опционально, для 100% локальной работы)

---

### 🍏 Инструкция для macOS
1. **Клонирование**:
   ```bash
   git clone https://github.com/your-username/Memex-Wiki.git
   cd Memex-Wiki
   ```
2. **Установка зависимостей**:
   ```bash
   chmod +x setup_env.sh
   ./setup_env.sh
   ```
3. **Настройка**:
   ```bash
   cp config.example.yaml config.yaml
   # Откройте config.yaml и настройте пути к вашим папкам
   ```
4. **Запуск**:
   ```bash
   source .venv/bin/activate
   python3 src/web_server.py
   ```
   Откройте `http://localhost:8000` в браузере.

---

### 🪟 Инструкция для Windows
1. **Клонирование**:
   Откройте Git Bash или командную строку (Cmd):
   ```cmd
   git clone https://github.com/your-username/Memex-Wiki.git
   cd Memex-Wiki
   ```
2. **Создание виртуального окружения**:
   ```cmd
   python -m venv .venv
   call .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Настройка**:
   ```cmd
   copy config.example.yaml config.yaml
   :: Откройте config.yaml в Блокноте и укажите пути. Используйте прямые слэши (например: C:/Users/name/Obsidian/Vault)
   ```
4. **Запуск**:
   ```cmd
   python src/web_server.py
   ```
   Откройте `http://localhost:8000` в браузере.

---

### 🐧 Инструкция для Linux
1. **Клонирование**:
   ```bash
   git clone https://github.com/your-username/Memex-Wiki.git
   cd Memex-Wiki
   ```
2. **Создание окружения**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Настройка**:
   ```bash
   cp config.example.yaml config.yaml
   # Отредактируйте config.yaml, указав пути
   ```
4. **Запуск**:
   ```bash
   python3 src/web_server.py
   ```
   Откройте `http://localhost:8000` в браузере.

---

## Безопасность и конфиденциальность 🛡️

Memex-Wiki спроектирован с упором на приватность:
1. **Локальные пути и ключи**: Файл `config.yaml` автоматически добавлен в `.gitignore`, чтобы ваши личные пути и ключи API не попали в публичный доступ на GitHub. Пользуйтесь шаблоном `config.example.yaml`.
2. **Очистка данных (Redaction)**: Встроенный модуль безопасности `src/security_filter.py` автоматически вырезает из загружаемых текстов:
   * Google / Gemini API ключи
   * OpenAI API ключи
   * Строки подключения баз данных (PostgreSQL, MongoDB, MySQL, Redis)
   * Пароли, приватные ключи и токены авторизации.
3. **Локальность**: При использовании бэкенда Ollama все операции ИИ происходят строго локально на вашем компьютере.

---

## Интеграция с MCP-клиентами для ИИ (Claude Code, Cursor, Cline и др.)

Чтобы подключить эту память к вашим ИИ-агентам или IDE, используйте конфигурации ниже в зависимости от клиента:

### 1. Claude Code
Добавьте в глобальный конфигурационный файл `~/.support/claude/mcp.json` (или выполните команду `claude mcp add`):
```json
{
  "mcpServers": {
    "memex-wiki": {
      "command": "/absolute/path/to/Memex-Wiki/.venv/bin/python3",
      "args": ["/absolute/path/to/Memex-Wiki/src/mcp_server.py"]
    }
  }
}
```

### 2. Cursor / VS Code
Перейдите в Settings -> Features -> MCP, нажмите **+ Add New MCP Server**:
*   **Name**: Memex-Wiki
*   **Type**: command
*   **Command**: `/absolute/path/to/Memex-Wiki/.venv/bin/python3 /absolute/path/to/Memex-Wiki/src/mcp_server.py`

### 3. Cline / Roo Code / Roo Clinic
Добавьте настройки в ваш файл `cline_mcp_settings.json` (или `roo_mcp_settings.json`):
```json
{
  "mcpServers": {
    "memex-wiki": {
      "command": "/absolute/path/to/Memex-Wiki/.venv/bin/python3",
      "args": ["/absolute/path/to/Memex-Wiki/src/mcp_server.py"],
      "disabled": false,
      "alwaysAllow": []
    }
  }
}
```

### 4. Google Antigravity 2.0
Укажите блок запуска stdio-сервера в конфигурации агента:
* **Команда запуска**: `/path/to/Memex-Wiki/.venv/bin/python` (or `python.exe` on Windows)
* **Аргументы**: `/path/to/Memex-Wiki/src/mcp_server.py`

### Доступные инструменты (Tools)
* `ingest_source(filename)`: Импортировать файл проекта, провести атомизацию и закоммитить в Git.
* `query_memory(query)`: Выполнить точечный семантический RLM-поиск по графу Obsidian.
* `lint_memory()`: Провести аудит целостности связей и YAML-метаданных.

---

## 🤖 Промпт для авто-развертывания (Для ИИ-агентов)

Если вы хотите, чтобы ваш ИИ-ассистент (Claude, Cursor, Cline или Antigravity) самостоятельно развернул эту память и подключил её к своей системе, отправьте ему следующий промпт:

```markdown
Ты — автономный ИИ-агент деплоя. Твоя задача — установить, настроить и подключить систему долгосрочной памяти Memex-Wiki к самому себе.

Пожалуйста, выполни следующие шаги:
1. Проверь наличие git, python3 и pip в системе.
2. Склонируй репозиторий: `git clone https://github.com/your-username/Memex-Wiki.git` и перейди в папку.
3. Запусти скрипт настройки `./setup_env.sh` (или создай виртуальное окружение вручную и установи зависимости из requirements.txt).
4. Скопируй `config.example.yaml` в `config.yaml`.
5. Найди путь к моему Obsidian Vault (если есть) или создай локальную папку `my_memex_vault` под хранилище, затем укажи абсолютные пути в `config.yaml`.
6. Проверь, запущена ли Ollama на этом устройстве:
   - Если нет, установи Ollama, запусти службу и скачай модель `qwen2.5:3b`.
   - Если запущена, скачай модель `qwen2.5:3b`.
   - Если я предпочитаю облако, спроси у меня API-ключ Gemini и запиши его в `config.yaml`.
7. Запусти тесты: `.venv/bin/python3 src/test_memex.py`, чтобы убедиться, что система работает.
8. Автоматически пропиши этот MCP-сервер в свой конфигурационный файл:
   - Команда: `<путь_к_python_в_venv>`
   - Аргументы: `[<путь_к_mcp_server.py>]`
   - (Например, внеси изменения в ~/.support/claude/mcp.json или cline_mcp_settings.json).
9. Отчитайся о выполнении и проверь работу памяти, вызвав инструмент `lint_memory`.
```
