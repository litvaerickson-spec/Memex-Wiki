import os
import sys
import subprocess
import logging

# Добавляем src в пути поиска
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from memex_tools import MemexTools
from graph_linter import GraphLinter

# Цвета для вывода в консоль
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("test_memex")

def run_tests():
    print(f"{BLUE}=== Запуск автоматических тестов Memex-Wiki ==={NC}")
    
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(cwd, "config.yaml")
    
    tools = MemexTools(config_path)
    # Принудительно проверяем загрузку конфига
    tools.load_config()
    
    raw_dir = tools.raw_dir
    wiki_dir = tools.wiki_dir
    
    print(f"Папка проектов (raw): {raw_dir}")
    print(f"Папка базы знаний (wiki): {wiki_dir}")
    
    # 1. Готовим тестовый файл astra-spec.txt в папке raw
    test_filename = "astra-spec.txt"
    test_filepath = os.path.join(raw_dir, test_filename)
    
    print(f"\n1. Создание тестового файла {test_filename}...")
    try:
        with open(test_filepath, "w", encoding="utf-8") as f:
            f.write("Почтовый клиент для Astra Linux должен работать без административных прав")
        print(f"{GREEN}✓ Тестовый файл успешно создан по пути: {test_filepath}{NC}")
    except Exception as e:
        print(f"{RED}✗ Ошибка создания тестового файла: {e}{NC}")
        return False

    # 2. Вызываем метод Ingestion
    print(f"\n2. Запуск импорта (ingest_source) для {test_filename}...")
    try:
        ingest_result = tools.ingest_source(test_filename)
        print(f"Результат импорта:\n{ingest_result}")
        print(f"{GREEN}✓ Функция ingest_source выполнена без исключений.{NC}")
    except Exception as e:
        print(f"{RED}✗ Исключение во время импорта: {e}{NC}")
        return False
        
    # Проверяем созданные файлы
    summary_file = os.path.join(wiki_dir, "astra-spec.md")
    concept_file = os.path.join(wiki_dir, "astra-linux.md")
    
    print("\nПроверка созданных страниц в wiki/...")
    if os.path.exists(summary_file):
        print(f"{GREEN}✓ Создан файл выжимки: {summary_file}{NC}")
    else:
        print(f"{RED}✗ Файл выжимки astra-spec.md НЕ создан!{NC}")
        return False
        
    if os.path.exists(concept_file):
        print(f"{GREEN}✓ Создан файл концепта: {concept_file}{NC}")
    else:
        print(f"{RED}✗ Файл концепта astra-linux.md НЕ создан!{NC}")
        return False

    # 3. Вызываем метод RLM-поиска
    print(f"\n3. Запуск RLM-поиска (query_memory) по базе знаний...")
    query = "Какие права нужны для почтового клиента в Astra Linux?"
    print(f"Запрос: {query}")
    try:
        search_result = tools.query_memory(query)
        print(f"\nРезультат поиска:\n{BLUE}{search_result}{NC}\n")
        
        # Проверяем, что в ответе упоминается источник и суть
        if "astra-spec.md" in search_result.lower() or "astra-spec" in search_result.lower():
            print(f"{GREEN}✓ В ответе корректно процитирован источник (astra-spec).{NC}")
        else:
            print(f"{YELLOW}⚠ Предупреждение: В ответе нет явного упоминания astra-spec.md как источника.{NC}")
            
        if "администратор" in search_result.lower() or "прав" in search_result.lower():
            print(f"{GREEN}✓ Ответ содержит верную фактологическую суть.{NC}")
        else:
            print(f"{RED}✗ Ответ не содержит фактов об административных правах!{NC}")
            return False
            
    except Exception as e:
        print(f"{RED}✗ Исключение во время поиска: {e}{NC}")
        return False

    # 4. Проверяем автокоммит Git
    print(f"\n4. Проверка автоматического Git-коммита...")
    try:
        res = subprocess.run(
            ["git", "log", "-n", "3", "--oneline"],
            cwd=wiki_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
        git_log = res.stdout.decode("utf-8")
        print(f"Последние коммиты в wiki/:\n{git_log}")
        if "auto-commit" in git_log:
            print(f"{GREEN}✓ Автоматический коммит Git найден в логах.{NC}")
        else:
            print(f"{RED}✗ Автоматический коммит с тегом auto-commit отсутствует.{NC}")
            return False
    except Exception as e:
        print(f"{RED}✗ Не удалось прочитать историю коммитов Git: {e}{NC}")
        return False

    # 5. Запуск линтера
    print(f"\n5. Запуск линтера (lint_memory) для проверки графа...")
    try:
        linter = GraphLinter(config_path)
        lint_report = linter.lint_memory()
        print(f"Отчет линтера:\n{lint_report}")
        if "❌" in lint_report:
            print(f"{RED}✗ Линтер обнаружил критические ошибки в базе знаний!{NC}")
            return False
        else:
            print(f"{GREEN}✓ База знаний прошла структурный аудит линтера без ошибок.{NC}")
    except Exception as e:
        print(f"{RED}✗ Исключение во время работы линтера: {e}{NC}")
        return False

    print(f"\n{GREEN}=== Все тесты Memex-Wiki успешно пройдены! ==={NC}")
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
