import os
import sys
import time
import logging

# Добавляем текущую папку в пути поиска модулей
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from security_filter import SecurityFilter
from graph_indexer import GraphIndexer
from vector_store import VectorStore
from search_engine import SearchEngine

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_graphrag")

def run_security_test():
    logger.info("--- [ТЕСТ 1] Проверка фильтра безопасности ---")
    sf = SecurityFilter()
    
    test_cases = [
        ("API-ключ Gemini: AIzaSyA5VY99OIEvQwa2Dwyp8-fGjMrtKR-gfvQ", 
         "API-ключ Gemini: [REDACTED_GOOGLE_API_KEY_/_GEMINI]"),
        ("Строка подключения: postgres://user:my-secret-pass@localhost:5432/db", 
         "Строка подключения: [REDACTED_DATABASE_CONNECTION_STRING]"),
        ("Переменная: password = \"1234567\"", 
         "Переменная: password=\"[REDACTED_PASSWORD]\""),
        ("Обычный текст без секретов", 
         "Обычный текст без секретов")
    ]
    
    success = True
    for original, expected in test_cases:
        redacted = sf.redact_text(original)
        if redacted.strip() == expected.strip():
            logger.info(f"Успешно: '{original}' -> '{redacted}'")
        else:
            logger.error(f"ОШИБКА: '{original}' -> '{redacted}' (ожидалось '{expected}')")
            success = False
            
    return success

def run_indexing_pipeline(config_path: str):
    logger.info("--- [ТЕСТ 2] Запуск полного процесса индексации ---")
    start_time = time.time()
    
    # 1. Запускаем сканирование и генерацию теневых заметок
    indexer = GraphIndexer(config_path)
    
    logger.info("Этап 1: Генерация теневых заметок...")
    indexer.generate_shadow_notes()
    
    logger.info("Этап 2: Построение графа связей...")
    graph = indexer.parse_markdown_vault()
    
    # 2. Инициализируем векторное хранилище и индексируем изменившиеся заметки
    logger.info("Этап 3: Анализ изменившихся файлов и генерация эмбеддингов...")
    vector_store = VectorStore(config_path)
    
    # Сначала удалим из векторной базы те заметки, которые были удалены из графа
    active_notes = set(graph["nodes"].keys())
    existing_chunks_by_note = {}
    
    for doc_id, data in list(vector_store.embeddings_db.items()):
        note_name = data.get("metadata", {}).get("note_name")
        if not note_name:
            note_name = doc_id.split("#chunk")[0]
            
        if note_name not in active_notes:
            logger.info(f"Удаление устаревшей заметки из векторного индекса: {note_name}")
            vector_store.remove_document(doc_id)
        else:
            if note_name not in existing_chunks_by_note:
                existing_chunks_by_note[note_name] = []
            existing_chunks_by_note[note_name].append(doc_id)
            
    # Собираем документы для пакетного эмбеддинга (только новые или измененные)
    documents_to_index = []
    skipped_count = 0
    
    for note_name, node_info in graph["nodes"].items():
        if node_info.get("type") == "tag":
            continue
            
        file_path = node_info.get("path")
        if not file_path or not os.path.exists(file_path):
            continue
            
        try:
            # Получаем mtime файла
            current_mtime = os.path.getmtime(file_path)
            
            # Проверяем, есть ли уже чанки для этой заметки и совпадает ли mtime
            existing_chunks = existing_chunks_by_note.get(note_name, [])
            
            needs_reindex = True
            if existing_chunks:
                first_chunk_id = existing_chunks[0]
                first_chunk = vector_store.embeddings_db.get(first_chunk_id, {})
                cached_mtime = first_chunk.get("metadata", {}).get("mtime")
                if cached_mtime == current_mtime:
                    needs_reindex = False
                    skipped_count += 1
                    
            if not needs_reindex:
                continue
                
            # Если требуется переиндексация, удаляем старые чанки
            for doc_id in existing_chunks:
                vector_store.remove_document(doc_id)
                
            # Читаем содержимое заметки
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
            # Если это теневая заметка, считываем оригинальный файл (для индексации его исходного кода)
            if node_info.get("type") == "external_file" and "original_path:" in content:
                import re
                match = re.search(r'original_path:\s*"(.*?)"', content)
                if match:
                    orig_path = match.group(1)
                    if os.path.exists(orig_path):
                        with open(orig_path, "r", encoding="utf-8", errors="ignore") as orig_f:
                            content = orig_f.read()
                            # Для внешнего файла берем mtime оригинального файла, а не заглушки
                            current_mtime = os.path.getmtime(orig_path)
                            
            # Очищаем текст от секретов
            content = indexer.security_filter.redact_text(content)
            
            # Разрезаем на фрагменты
            chunk_size = 1500
            overlap = 200
            
            if len(content) > chunk_size:
                chunks = []
                start = 0
                while start < len(content):
                    end = start + chunk_size
                    chunks.append(content[start:end])
                    start += chunk_size - overlap
            else:
                chunks = [content]
                
            for idx, chunk in enumerate(chunks):
                chunk_id = f"{note_name}#chunk{idx}"
                documents_to_index.append({
                    "id": chunk_id,
                    "text": chunk,
                    "metadata": {
                        "note_name": note_name,
                        "file_path": file_path,
                        "chunk_idx": idx,
                        "total_chunks": len(chunks),
                        "type": node_info.get("type"),
                        "mtime": current_mtime  # сохраняем mtime для инкрементальной проверки
                    }
                })
        except Exception as e:
            logger.error(f"Не удалось подготовить к индексации файл {file_path}: {e}")
            
    logger.info(f"Пропущено неизмененных заметок: {skipped_count}")
    
    if documents_to_index:
        logger.info(f"Подготовлено {len(documents_to_index)} измененных фрагментов для генерации эмбеддингов.")
        try:
            vector_store.add_documents_batch(documents_to_index)
            vector_store.save()
        except Exception as e:
            logger.error(f"Критическая ошибка при вычислении эмбеддингов: {e}")
            return False
    else:
        logger.info("Все файлы уже проиндексированы. Изменений не найдено.")
        vector_store.save()
        
    duration = time.time() - start_time
    logger.info(f"Индексация успешно завершена за {duration:.2f} сек.")
    logger.info(f"Итого в графе: {len(graph['nodes'])} узлов.")
    logger.info(f"Итого в векторной базе: {len(vector_store.embeddings_db)} фрагментов.")
    return True

def run_search_test(config_path: str):
    logger.info("--- [ТЕСТ 3] Проверка работы поискового движка ---")
    engine = SearchEngine(config_path)
    
    # Попробуем сделать тестовые запросы по структуре
    test_queries = [
        "база данных",
        "mcp",
        "api key"
    ]
    
    for q in test_queries:
        logger.info(f"\nЗапрос: '{q}' (Локальный GraphRAG поиск)")
        results = engine.local_search(q, limit=2)
        print(results[:1500])  # Печатаем первые 1500 символов результата
        print("-" * 60)
        
    logger.info("\nЗапрос: 'интеграция' (Глобальный поиск)")
    global_results = engine.global_search("интеграция")
    print(global_results)
    print("-" * 60)

if __name__ == "__main__":
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(cwd, "config.yaml")
    
    if not os.path.exists(config_path):
        logger.error(f"Не найден config.yaml по пути {config_path}")
        sys.exit(1)
        
    # Запускаем тесты
    sec_ok = run_security_test()
    if not sec_ok:
        logger.error("Тест безопасности провален! Остановка.")
        sys.exit(1)
        
    try:
        # Проверяем библиотеки
        import torch
        import sentence_transformers
    except ImportError:
        logger.error("PyTorch или SentenceTransformers отсутствуют. "
                     "Запустите setup_env.sh для установки окружения, затем повторите тест.")
        sys.exit(1)
        
    index_ok = run_indexing_pipeline(config_path)
    if index_ok:
        run_search_test(config_path)
        logger.info("Все тесты успешно пройдены!")
    else:
        logger.error("Процесс индексации завершился с ошибкой.")
        sys.exit(1)
