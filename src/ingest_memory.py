import os
import sys
import logging

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from memex_tools import MemexTools

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ingest_memory")

def main():
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(cwd, "config.yaml")
    
    tools = MemexTools(config_path)
    tools.load_config()
    
    raw_dir = tools.raw_dir
    logger.info(f"Сканирование папки источников (raw): {raw_dir}")
    
    if not os.path.exists(raw_dir):
        logger.error(f"Папка {raw_dir} не существует.")
        sys.exit(1)
        
    # Сканируем файлы
    files_to_ingest = []
    max_file_size_mb = 10.0  # Ограничение размера для предотвращения перегрузки локальной LLM
    
    for entry in os.scandir(raw_dir):
        if entry.is_file():
            name = entry.name
            # Игнорируем скрытые и исключенные файлы
            if name.startswith(".") or name == "Неудалять.txt" or name == "astra-spec.txt":
                continue
                
            # Проверяем расширение
            ext = os.path.splitext(name)[1].lower()
            if ext in [".txt", ".md", ".docx", ".pdf"]:
                size_mb = entry.stat().st_size / (1024 * 1024)
                if size_mb > max_file_size_mb:
                    logger.warning(f"Файл {name} пропущен (размер {size_mb:.2f} MB превышает лимит {max_file_size_mb} MB для локальных моделей).")
                    continue
                # Проверяем, был ли уже импортирован
                source_id = tools.normalize_concept_name(os.path.splitext(name)[0])
                if os.path.exists(os.path.join(tools.wiki_dir, f"{source_id}.md")):
                    logger.info(f"Файл {name} уже импортирован (выжимка существует). Пропускаем.")
                    continue
                files_to_ingest.append((name, size_mb))
                
    if not files_to_ingest:
        logger.info("Нет новых подходящих файлов для импорта.")
        return
        
    logger.info(f"Найдено файлов для импорта: {len(files_to_ingest)}")
    for name, size in files_to_ingest:
        logger.info(f" - {name} ({size:.2f} MB)")
        
    # Запуск импорта
    for filename, size in files_to_ingest:
        logger.info(f"Начинаем импорт файла: {filename}...")
        try:
            result = tools.ingest_source(filename)
            logger.info(f"Результат импорта {filename}: {result}")
        except Exception as e:
            logger.error(f"Не удалось импортировать {filename}: {e}")
            
    logger.info("Сканирование и импорт завершены!")

if __name__ == "__main__":
    main()
