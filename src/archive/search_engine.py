import os
import json
import yaml
import logging
from vector_store import VectorStore
from security_filter import SecurityFilter

logger = logging.getLogger("search_engine")

class SearchEngine:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.vault_path = ""
        self.graph_file = ""
        self.graph_data = {"nodes": {}, "edges": []}
        
        self.vector_store = VectorStore(config_path)
        self.security_filter = SecurityFilter(config_path)
        
        self.load_config()
        self.load_graph()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    self.vault_path = os.path.normpath(config.get("obsidian_vault_path", ""))
                    index_dir = os.path.join(self.vault_path, ".antigravity_index")
                    self.graph_file = os.path.join(index_dir, "graph.json")
            except Exception as e:
                logger.error(f"Не удалось загрузить config.yaml для SearchEngine: {e}")

    def load_graph(self):
        if os.path.exists(self.graph_file):
            try:
                with open(self.graph_file, "r", encoding="utf-8") as f:
                    self.graph_data = json.load(f)
                logger.info(f"Граф связей загружен: {len(self.graph_data.get('nodes', {}))} узлов.")
            except Exception as e:
                logger.error(f"Ошибка загрузки графа из {self.graph_file}: {e}")
                self.graph_data = {"nodes": {}, "edges": []}
        else:
            self.graph_data = {"nodes": {}, "edges": []}

    def reload(self):
        """Перезагружает векторный индекс и граф с диска."""
        self.vector_store._load_db()
        self.load_graph()

    def _get_node_neighbors(self, node_name: str) -> dict:
        """
        Находит всех соседей узла (входящие, исходящие связи, теги) в графе.
        """
        incoming = []
        outgoing = []
        tags = []
        
        for edge in self.graph_data.get("edges", []):
            source = edge["source"]
            target = edge["target"]
            edge_type = edge.get("type", "wikilink")
            
            if source == node_name:
                if edge_type == "tag_link":
                    tags.append(target.replace("#", ""))
                else:
                    outgoing.append(target)
            elif target == node_name:
                if edge_type != "tag_link":
                    incoming.append(source)
                    
        return {
            "incoming": list(set(incoming)),
            "outgoing": list(set(outgoing)),
            "tags": list(set(tags))
        }

    def _read_file_content(self, node_name: str, node_info: dict) -> str:
        """
        Считывает полное содержимое файла. Если это теневая заметка (заглушка),
        то считывает оригинальный файл по пути из frontmatter.
        """
        path = node_info.get("path", "")
        if not path or not os.path.exists(path):
            return "*Содержимое файла недоступно (файл не существует на диске).*"
            
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
            # Проверяем, является ли файл теневой заметкой внешнего файла
            if node_info.get("type") == "external_file" and "original_path:" in content:
                # Извлекаем оригинальный путь из frontmatter теневой заметки
                match = re.search(r'original_path:\s*"(.*?)"', content)
                if match:
                    orig_path = match.group(1)
                    if os.path.exists(orig_path):
                        # Читаем оригинальный файл
                        with open(orig_path, "r", encoding="utf-8", errors="ignore") as orig_f:
                            content = orig_f.read()
                            
            # Очищаем от секретов перед выдачей
            return self.security_filter.redact_text(content)
        except Exception as e:
            return f"*Ошибка при чтении файла {node_name}: {e}*"

    def local_search(self, query: str, limit: int = 5) -> str:
        """
        Выполняет локальный поиск по графу (Entity-centric GraphRAG):
        1. Находит семантически похожие файлы через векторный поиск.
        2. Для каждого файла извлекает соседей по графу и теги.
        3. Формирует разметку контекста.
        """
        self.reload()
        
        # Векторный поиск
        vector_results = self.vector_store.search(query, limit=limit)
        if not vector_results:
            return "Локальный поиск GraphRAG: Совпадений не найдено."
            
        formatted_context = []
        formatted_context.append(f"# Контекст GraphRAG по запросу: '{query}'\n")
        
        referenced_notes = set()
        
        for idx, res in enumerate(vector_results):
            node_name = res["id"]
            score = res["score"]
            
            node_info = self.graph_data.get("nodes", {}).get(node_name, {})
            if not node_info:
                continue
                
            referenced_notes.add(node_name)
            neighbors = self._get_node_neighbors(node_name)
            
            # Читаем содержимое файла
            file_content = self._read_file_content(node_name, node_info)
            
            formatted_context.append(f"## [{idx+1}] Документ: [[{node_name}]] (Семантическое сходство: {score:.4f})")
            formatted_context.append(f"* **Тип**: {node_info.get('type', 'note')}")
            if node_info.get("path"):
                formatted_context.append(f"* **Путь на диске**: `{node_info['path']}`")
                
            # Добавляем информацию о связях
            if neighbors["outgoing"]:
                formatted_context.append(f"* **Ссылки из этой заметки**: " + ", ".join([f"[[{n}]]" for n in neighbors["outgoing"]]))
            if neighbors["incoming"]:
                formatted_context.append(f"* **Упоминается в заметках**: " + ", ".join([f"[[{n}]]" for n in neighbors["incoming"]]))
            if neighbors["tags"]:
                formatted_context.append(f"* **Теги**: " + ", ".join([f"#{t}" for t in neighbors["tags"]]))
                
            formatted_context.append("\n### Содержимое:")
            # Ограничиваем длину содержимого в контексте для экономии токенов
            lines = file_content.split("\n")
            if len(lines) > 200:
                short_content = "\n".join(lines[:200]) + f"\n\n... [Вырезано {len(lines)-200} строк для экономии контекста] ..."
            else:
                short_content = file_content
                
            formatted_context.append(short_content)
            formatted_context.append("\n" + "="*50 + "\n")
            
        # Добавляем список смежных заметок верхнего уровня
        related_stubs = []
        for note in referenced_notes:
            neighbors = self._get_node_neighbors(note)
            for out in neighbors["outgoing"] + neighbors["incoming"]:
                if out not in referenced_notes:
                    related_stubs.append(out)
                    
        related_stubs = list(set(related_stubs))[:15]
        if related_stubs:
            formatted_context.append("## Связанные понятия, которые могут быть полезны:")
            formatted_context.append(", ".join([f"[[{r}]]" for r in related_stubs]))
            
        return "\n".join(formatted_context)

    def global_search(self, query: str) -> str:
        """
        Выполняет глобальный поиск (Concept-centric GraphRAG):
        Находит концепты с наибольшим числом связей (MOC/хабы), которые семантически соответствуют запросу.
        """
        self.reload()
        
        # Получаем векторный поиск с большим лимитом, чтобы проанализировать больше узлов
        vector_results = self.vector_store.search(query, limit=20)
        if not vector_results:
            return "Глобальный поиск GraphRAG: Совпадений не найдено."
            
        formatted_context = []
        formatted_context.append(f"# Глобальный анализ базы знаний по запросу: '{query}'\n")
        formatted_context.append("Ниже представлены ключевые концепты, проекты и их связи, имеющие отношение к запросу:\n")
        
        # Собираем узлы и считаем их степень (количество связей)
        hubs = []
        for res in vector_results:
            node_name = res["id"]
            node_info = self.graph_data.get("nodes", {}).get(node_name, {})
            if not node_info or node_info.get("type") == "tag":
                continue
                
            neighbors = self._get_node_neighbors(node_name)
            degree = len(neighbors["incoming"]) + len(neighbors["outgoing"])
            
            hubs.append({
                "name": node_name,
                "type": node_info.get("type", "note"),
                "summary": node_info.get("summary", ""),
                "degree": degree,
                "score": res["score"],
                "neighbors": neighbors
            })
            
        # Сортируем узлы: сначала наиболее важные хабы (по степени связи), затем по семантическому сходству
        hubs.sort(key=lambda x: (x["degree"], x["score"]), reverse=True)
        
        for idx, hub in enumerate(hubs[:8]):  # Выдаем топ-8 глобальных концептов
            formatted_context.append(f"### {idx+1}. [[{hub['name']}]] (Важность в графе: {hub['degree']} связей)")
            formatted_context.append(f"* **Тип концепта**: {hub['type']}")
            formatted_context.append(f"* **Краткое описание**: {hub['summary']}")
            
            # Указываем с чем связан
            rel_list = []
            if hub["neighbors"]["outgoing"]:
                rel_list.extend([f"ссылается на [[{n}]]" for n in hub["neighbors"]["outgoing"][:5]])
            if hub["neighbors"]["incoming"]:
                rel_list.extend([f"упоминается в [[{n}]]" for n in hub["neighbors"]["incoming"][:5]])
                
            if rel_list:
                formatted_context.append(f"* **Связи**: " + ", ".join(rel_list))
                
            formatted_context.append("")
            
        return "\n".join(formatted_context)

# Тестовый запуск поиска
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cwd = os.getcwd()
    config_file = os.path.join(cwd, "config.yaml")
    
    if os.path.exists(config_file):
        engine = SearchEngine(config_file)
        # Проверим локальный поиск
        res = engine.local_search("база данных", limit=2)
        print("=== ТЕСТ ЛОКАЛЬНОГО ПОИСКА ===")
        print(res[:1000] + "\n...")
