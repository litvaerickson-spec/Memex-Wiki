import os
import sys
import logging
from mcp.server.fastmcp import FastMCP

# Добавляем текущую папку в пути поиска модулей
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from memex_tools import MemexTools
from graph_linter import GraphLinter

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")

# Находим путь к файлу конфигурации в родительской папке относительно src/
cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(cwd, "config.yaml")

if not os.path.exists(config_path):
    logger.error(f"Не найден файл конфигурации config.yaml по пути: {config_path}")
    sys.exit(1)

# Создаем инстанс FastMCP
mcp = FastMCP("Memex_Wiki_Memory")

# Ленивая инициализация инструментов
_memex_tools = None
_graph_linter = None

def get_memex_tools() -> MemexTools:
    global _memex_tools
    if _memex_tools is None:
        _memex_tools = MemexTools(config_path)
    return _memex_tools

def get_graph_linter() -> GraphLinter:
    global _graph_linter
    if _graph_linter is None:
        _graph_linter = GraphLinter(config_path)
    return _graph_linter

@mcp.tool()
def ingest_source(filename: str) -> str:
    """
    Импортирует и обрабатывает новый файл из вашей папки проектов (эквивалент raw-папки).
    Модель выделяет сущности, строит связи и создает/обновляет страницы в wiki/ с авто-коммитом в Git.
    Укажите относительный путь к файлу (например, 'astra-spec.txt' или 'project-x/readme.txt').
    """
    try:
        tools = get_memex_tools()
        # Перезагружаем конфиг на случай его изменения пользователем
        tools.load_config()
        return tools.ingest_source(filename)
    except Exception as e:
        logger.error(f"Ошибка при импорте источника {filename}: {e}")
        return f"Критическая ошибка при импорте источника: {e}"

@mcp.tool()
def query_memory(query: str) -> str:
    """
    Выполняет точечный поиск по базе знаний Memex-Wiki с последовательной RLM-фильтрацией чанков.
    Сжимает длинные тексты и возвращает только концентрированный контекст с цитированием источников (source: файл.md).
    Предотвращает перегрузку памяти и VRAM OOM на локальном железе.
    """
    try:
        tools = get_memex_tools()
        tools.load_config()
        return tools.query_memory(query)
    except Exception as e:
        logger.error(f"Ошибка при поиске памяти: {e}")
        return f"Критическая ошибка при выполнении поиска: {e}"

@mcp.tool()
def lint_memory() -> str:
    """
    Запускает проверку целостности базы знаний Memex-Wiki (Graph Lint).
    Проверяет валидность YAML, битые вики-ссылки [[link]], сиротские страницы и потенциально устаревшие данные.
    """
    try:
        linter = get_graph_linter()
        linter.load_config()
        return linter.lint_memory()
    except Exception as e:
        logger.error(f"Ошибка при линтинге памяти: {e}")
        return f"Критическая ошибка при запуске линтера: {e}"

if __name__ == "__main__":
    # Запускаем MCP сервер по протоколу stdio
    mcp.run()
