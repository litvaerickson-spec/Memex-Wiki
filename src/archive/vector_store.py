import os
import json
import yaml
import logging
import pickle

logger = logging.getLogger("vector_store")

class VectorStore:
    def __init__(self, config_path: str = None):
        self.config_path = config_path
        self.model_name = "BAAI/bge-m3"
        self.use_mps = True
        self.vault_path = ""
        self.index_dir = ""
        self.index_file = ""
        
        # Загружаем настройки
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    self.model_name = config.get("model_name", "BAAI/bge-m3")
                    self.use_mps = config.get("use_mps", True)
                    self.vault_path = config.get("obsidian_vault_path", "")
            except Exception as e:
                logger.error(f"Не удалось загрузить config.yaml для VectorStore: {e}")

        # Настраиваем путь для сохранения индекса
        if self.vault_path:
            self.index_dir = os.path.join(self.vault_path, ".antigravity_index")
            self.index_file = os.path.join(self.index_dir, "vector_index.pkl")
        else:
            # Дефолтный путь в текущей папке, если путь к Vault не задан
            self.index_dir = "./.antigravity_index"
            self.index_file = os.path.join(self.index_dir, "vector_index.pkl")
            
        self.model = None
        self.embeddings_db = {}  # {doc_id: {"text": str, "vector": list[float], "metadata": dict}}
        self._load_db()

    def _init_model(self):
        """
        Ленивая инициализация модели, чтобы импорт библиотек происходил только при необходимости.
        """
        if self.model is not None:
            return
            
        logger.info(f"Загрузка локальной модели эмбеддингов {self.model_name}...")
        try:
            import torch
            from sentence_transformers import SentenceTransformer
            
            # Определяем устройство для ускорения (MPS для M1/M2/M3 на Mac, CUDA для Nvidia, CPU как резерв)
            device = "cpu"
            if self.use_mps:
                if torch.backends.mps.is_available():
                    device = "mps"
                    logger.info("Используется GPU Apple Silicon (Metal/MPS) для ускорения модели.")
                elif torch.cuda.is_available():
                    device = "cuda"
                    logger.info("Используется GPU Nvidia (CUDA) для ускорения модели.")
            
            self.model = SentenceTransformer(self.model_name, device=device)
            if device == "cpu":
                import torch
                # Ограничиваем до 2 потоков, чтобы индексация шла в фоне тихо и не фризила систему
                threads = 2
                torch.set_num_threads(threads)
                logger.info(f"Настроено {threads} потоков CPU для PyTorch.")
            logger.info("Модель эмбеддингов успешно загружена.")
        except ImportError:
            logger.error("Ошибка: Библиотеки PyTorch или SentenceTransformers не установлены. "
                         "Пожалуйста, запустите setup_env.sh для установки зависимостей.")
            raise

    def _load_db(self):
        """
        Загружает векторную базу данных из файла.
        """
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, "rb") as f:
                    self.embeddings_db = pickle.load(f)
                logger.info(f"Векторная база данных загружена: {len(self.embeddings_db)} векторов.")
            except Exception as e:
                logger.error(f"Не удалось прочитать векторный индекс {self.index_file}: {e}. Создаем новый.")
                self.embeddings_db = {}
        else:
            self.embeddings_db = {}

    def save(self):
        """
        Сохраняет векторную базу данных в файл.
        """
        if not os.path.exists(self.index_dir):
            os.makedirs(self.index_dir, exist_ok=True)
        try:
            with open(self.index_file, "wb") as f:
                pickle.dump(self.embeddings_db, f)
            logger.info(f"Векторная база данных успешно сохранена: {len(self.embeddings_db)} векторов.")
        except Exception as e:
            logger.error(f"Ошибка при сохранении векторного индекса в {self.index_file}: {e}")

    def add_document(self, doc_id: str, text: str, metadata: dict = None):
        """
        Добавляет один документ (заметку или часть файла) в индекс.
        """
        self._init_model()
        # Вычисляем вектор
        embedding = self.model.encode(text, convert_to_numpy=True).tolist()
        self.embeddings_db[doc_id] = {
            "text": text,
            "vector": embedding,
            "metadata": metadata or {}
        }

    def add_documents_batch(self, docs: list[dict]):
        """
        Пакетное добавление документов (гораздо быстрее за счет параллелизации на GPU).
        docs - список словарей вида: [{"id": str, "text": str, "metadata": dict}]
        """
        if not docs:
            return
            
        self._init_model()
        texts = [doc["text"] for doc in docs]
        
        logger.info(f"Вычисление эмбеддингов для пакета из {len(texts)} текстов...")
        embeddings = self.model.encode(texts, batch_size=32, show_progress_bar=True, convert_to_numpy=True)
        
        for doc, emb in zip(docs, embeddings):
            self.embeddings_db[doc["id"]] = {
                "text": doc["text"],
                "vector": emb.tolist(),
                "metadata": doc.get("metadata", {})
            }

    def remove_document(self, doc_id: str):
        """
        Удаляет документ из индекса.
        """
        if doc_id in self.embeddings_db:
            del self.embeddings_db[doc_id]

    def clear(self):
        """
        Очищает базу данных.
        """
        self.embeddings_db = {}
        if os.path.exists(self.index_file):
            try:
                os.remove(self.index_file)
            except Exception as e:
                logger.error(f"Не удалось удалить файл индекса: {e}")

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """
        Выполняет семантический поиск по косинусному сходству.
        Возвращает список результатов с метрикой схожести (score).
        """
        if not self.embeddings_db:
            return []
            
        self._init_model()
        import numpy as np
        
        # Получаем вектор запроса
        query_vector = np.array(self.model.encode(query, convert_to_numpy=True))
        
        # Собираем все вектора из базы данных
        doc_ids = []
        vectors = []
        for doc_id, data in self.embeddings_db.items():
            doc_ids.append(doc_id)
            vectors.append(data["vector"])
            
        vectors = np.array(vectors)
        
        # Вычисляем косинусное сходство: dot(A, B) / (norm(A) * norm(B))
        # Нормализуем вектора
        vectors_norm = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        query_norm = query_vector / np.linalg.norm(query_vector)
        
        # Перемножаем матрицы (получаем косинусное сходство)
        similarities = np.dot(vectors_norm, query_norm)
        
        # Сортируем по убыванию сходства
        top_indices = np.argsort(similarities)[::-1][:limit]
        
        results = []
        for idx in top_indices:
            doc_id = doc_ids[idx]
            score = float(similarities[idx])
            
            # Возвращаем результаты с порогом схожести (например, > 0.15 для bge-m3)
            results.append({
                "id": doc_id,
                "text": self.embeddings_db[doc_id]["text"],
                "metadata": self.embeddings_db[doc_id]["metadata"],
                "score": score
            })
            
        return results

# Скрипт тестирования векторного поиска
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    # Для теста создадим локальную папку
    store = VectorStore()
    
    try:
        store._init_model()
    except Exception:
        print("Библиотеки не установлены. Тест прерван (это нормально до запуска setup_env.sh).")
        sys.exit(0)
        
    test_docs = [
        {"id": "doc1", "text": "Мы используем базу данных PostgreSQL 16 для хранения транзакций.", "metadata": {"file": "postgres.md"}},
        {"id": "doc2", "text": "Агенты Antigravity общаются с внешними сервисами по протоколу Model Context Protocol (MCP).", "metadata": {"file": "mcp.md"}},
        {"id": "doc3", "text": "Для авторизации в API используется переменная окружения GEMINI_API_KEY.", "metadata": {"file": "auth.md"}}
    ]
    
    store.add_documents_batch(test_docs)
    
    query = "Какое хранилище транзакций у нас настроено?"
    print(f"\nПоиск по запросу: '{query}'")
    for res in store.search(query, limit=2):
        print(f"- [{res['metadata']['file']}] (Сходство: {res['score']:.4f}): {res['text']}")
        
    query = "Как подключить инструменты к агентам нейросети?"
    print(f"\nПоиск по запросу: '{query}'")
    for res in store.search(query, limit=2):
        print(f"- [{res['metadata']['file']}] (Сходство: {res['score']:.4f}): {res['text']}")
