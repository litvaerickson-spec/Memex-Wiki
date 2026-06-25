import os
import re
import ast
import yaml
import json
import logging
import urllib.parse
from security_filter import SecurityFilter

logger = logging.getLogger("graph_indexer")

class GraphIndexer:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.vault_path = ""
        self.external_folders = []
        self.shadow_notes_dir = ""
        self.exclude_patterns = []
        self.mtime_cache_file = ""
        self.mtime_cache = {}
        
        self.security_filter = SecurityFilter(config_path)
        self.graph_data = {"nodes": {}, "edges": []}
        
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    self.vault_path = os.path.normpath(config.get("obsidian_vault_path", ""))
                    self.external_folders = [os.path.normpath(p) for p in config.get("external_folders", [])]
                    
                    shadow_folder = config.get("shadow_notes_folder_name", "External_Links")
                    self.shadow_notes_dir = os.path.join(self.vault_path, shadow_folder)
                    
                    self.exclude_patterns = config.get("exclude_patterns", [])
                    
                    index_dir = os.path.join(self.vault_path, ".antigravity_index")
                    os.makedirs(index_dir, exist_ok=True)
                    self.mtime_cache_file = os.path.join(index_dir, "mtime_cache.json")
                    self.graph_file = os.path.join(index_dir, "graph.json")
                    
                    self._load_mtime_cache()
            except Exception as e:
                logger.error(f"Ошибка загрузки конфигурации в GraphIndexer: {e}")
        else:
            logger.error(f"Файл конфигурации не найден: {self.config_path}")

    def _load_mtime_cache(self):
        if os.path.exists(self.mtime_cache_file):
            try:
                with open(self.mtime_cache_file, "r", encoding="utf-8") as f:
                    self.mtime_cache = json.load(f)
            except Exception:
                self.mtime_cache = {}

    def _save_mtime_cache(self):
        try:
            with open(self.mtime_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.mtime_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Не удалось сохранить кэш mtime: {e}")

    def should_exclude(self, path: str) -> bool:
        """
        Проверяет, должен ли путь быть исключен на основе паттернов из config.yaml и общих правил безопасности.
        """
        normalized_path = os.path.normpath(path)
        parts = normalized_path.split(os.sep)
        
        # 1. Автоматически исключаем любые скрытые папки (начинающиеся с точки), кроме текущей директории.
        # Это отсекает .git, .github, .venv, .next, .obsidian, .antigravity_index и т.д.
        for part in parts:
            if part.startswith(".") and part not in [".", ".."]:
                return True
                
        # 2. Интеллектуальное исключение по ключевым словам (отсекаем venv_new, node_modules и т.д.)
        exclude_substrings = ["venv", "node_modules", "dist", "build", "__pycache__"]
        for part in parts:
            part_lower = part.lower()
            for sub in exclude_substrings:
                if sub in part_lower:
                    return True
        
        # 3. Пользовательские правила исключения из config.yaml
        for pattern in self.exclude_patterns:
            if pattern in parts:
                return True
                
            # Простой матчинг расширений и масок
            if "*" in pattern:
                regex_pattern = re.escape(pattern).replace(r"\*", ".*")
                if re.match(regex_pattern, os.path.basename(normalized_path)):
                    return True
            else:
                if os.path.basename(normalized_path) == pattern:
                    return True
                    
        return False

    def _parse_python_file(self, file_path: str) -> str:
        """
        С помощью AST парсит Python-файл и извлекает классы, функции и импорты.
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
            # Очищаем от секретов перед парсингом
            content = self.security_filter.redact_text(content)
            
            tree = ast.parse(content, filename=file_path)
            
            classes = []
            functions = []
            imports = []
            
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    classes.append(node.name)
                elif isinstance(node, ast.FunctionDef):
                    # Игнорируем приватные методы в кратком списке
                    if not node.name.startswith("_") or node.name.startswith("__"):
                        functions.append(node.name)
                elif isinstance(node, ast.Import):
                    for name in node.names:
                        imports.append(name.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)
                        
            summary = []
            if imports:
                summary.append(f"**Импорты:** " + ", ".join([f"`{imp}`" for imp in set(imports)[:15]]))
            if classes:
                summary.append(f"**Классы:** " + ", ".join([f"[[{cls}]]" for cls in classes]))
            if functions:
                summary.append(f"**Функции:** " + ", ".join([f"`{func}()`" for func in functions[:20]]))
                
            if not summary:
                # Если ничего структурного не нашлось, берем первые 10 строк
                lines = [line.strip() for line in content.split("\n") if line.strip()][:10]
                summary.append("```python\n" + "\n".join(lines) + "\n```")
                
            return "\n\n".join(summary)
        except Exception as e:
            return f"*Ошибка парсинга структуры Python: {e}*"

    def _get_generic_file_summary(self, file_path: str, ext: str) -> str:
        """
        Считывает начало файла и возвращает его в безопасном (от секретов) виде.
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = []
                for _ in range(30):  # Читаем максимум 30 строк для превью
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)
            
            content = "".join(lines)
            content = self.security_filter.redact_text(content)
            
            # Форматируем в зависимости от расширения
            lang = ext[1:] if ext.startswith(".") else ""
            if lang in ["js", "ts", "jsx", "tsx", "sh", "bash", "yaml", "yml", "json", "html", "css"]:
                return f"```{lang}\n{content.strip()}\n```"
            else:
                return content.strip()
        except Exception as e:
            return f"*Не удалось прочесть превью файла: {e}*"

    def generate_shadow_notes(self):
        """
        Сканирует внешние папки и создает файлы-заглушки (теневые заметки) в Obsidian.
        """
        logger.info("Запуск сканирования внешних папок и генерации теневых заметок...")
        os.makedirs(self.shadow_notes_dir, exist_ok=True)
        
        scanned_count = 0
        created_count = 0
        updated_count = 0
        
        valid_extensions = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
            ".json", ".yaml", ".yml", ".sh", ".bash", ".md", ".txt",
            ".cfg", ".conf", ".ini", ".sql"
        }
        
        active_shadow_paths = set()
        
        for ext_folder in self.external_folders:
            if not os.path.exists(ext_folder):
                logger.warning(f"Внешняя папка не существует: {ext_folder}")
                continue
                
            # Важно: если это папка Obsidian, мы ее пропускаем, так как она индексируется отдельно
            if os.path.normpath(ext_folder) == self.vault_path:
                continue
                
            for root, dirs, files in os.walk(ext_folder):
                # Фильтруем папки на месте, чтобы os.walk не шел туда
                # Исключаем те, что подходят под паттерны, а также саму директорию Obsidian
                dirs[:] = [d for d in dirs if not self.should_exclude(os.path.join(root, d)) and os.path.normpath(os.path.join(root, d)) != self.vault_path]
                
                # Исключаем также, если текущий root находится внутри папки Obsidian
                if os.path.normpath(root).startswith(self.vault_path):
                    continue
                
                for file in files:
                    file_path = os.path.join(root, file)
                    if self.should_exclude(file_path):
                        continue
                        
                    _, ext = os.path.splitext(file)
                    if ext.lower() not in valid_extensions:
                        continue
                        
                    scanned_count += 1
                    
                    # Получаем время изменения оригинального файла
                    mtime = os.path.getmtime(file_path)
                    
                    # Вычисляем путь к теневой заметке в Obsidian
                    # Сохраняем структуру папок относительно внешней папки
                    rel_dir = os.path.relpath(root, ext_folder)
                    shadow_folder_path = os.path.join(self.shadow_notes_dir, rel_dir)
                    
                    # Имя заметки: ИмяФайла_Расширение.md (чтобы избежать конфликтов)
                    shadow_file_name = f"{file.replace('.', '_')}.md"
                    shadow_file_path = os.path.join(shadow_folder_path, shadow_file_name)
                    
                    active_shadow_paths.add(os.path.normpath(shadow_file_path))
                    
                    # Проверяем, нужно ли обновлять заглушку
                    cached_mtime = self.mtime_cache.get(file_path)
                    if cached_mtime == mtime and os.path.exists(shadow_file_path):
                        continue  # Файл не изменился, пропускаем
                        
                    # Создаем директорию для заглушки
                    os.makedirs(shadow_folder_path, exist_ok=True)
                    
                    # Название проекта (имя корневой папки проекта во внешней директории)
                    project_name = os.path.basename(ext_folder)
                    if rel_dir != ".":
                        project_name = rel_dir.split(os.sep)[0]
                        
                    # Генерируем структуру содержимого теневой заметки
                    file_url = f"file://{urllib.parse.quote(file_path)}"
                    
                    # Получаем превью/структуру файла в зависимости от типа
                    if ext.lower() == ".py":
                        structure = self._parse_python_file(file_path)
                    else:
                        structure = self._get_generic_file_summary(file_path, ext)
                        
                    # Записываем Markdown теневой заметки
                    frontmatter = {
                        "type": "external_file",
                        "original_path": file_path,
                        "file_type": ext[1:] if ext else "txt",
                        "project": f"[[{project_name}]]",
                        "tags": ["external", ext[1:] if ext else "txt", f"project-{project_name}"],
                    }
                    
                    md_content = f"""---
type: {frontmatter['type']}
original_path: "{frontmatter['original_path']}"
file_type: {frontmatter['file_type']}
project: {frontmatter['project']}
tags: {json.dumps(frontmatter['tags'])}
---
# {file} (Внешний файл)

**Оригинальный путь:** `{file_path}`  
**Проект:** {frontmatter['project']}  
**Ссылка:** [🔗 Открыть файл на диске]({file_url})

---

### Структура и превью файла:
{structure}
"""
                    
                    # Записываем заглушку
                    with open(shadow_file_path, "w", encoding="utf-8") as sf:
                        sf.write(md_content)
                        
                    if cached_mtime:
                        updated_count += 1
                    else:
                        created_count += 1
                        
                    # Обновляем кэш mtime
                    self.mtime_cache[file_path] = mtime
                    
        # Удаляем устаревшие теневые заметки (оригиналы которых были удалены)
        deleted_count = 0
        if os.path.exists(self.shadow_notes_dir):
            for root, _, files in os.walk(self.shadow_notes_dir):
                for file in files:
                    if file.endswith(".md"):
                        path = os.path.normpath(os.path.join(root, file))
                        if path not in active_shadow_paths:
                            try:
                                os.remove(path)
                                deleted_count += 1
                            except Exception:
                                pass
                                
            # Удаляем пустые папки
            for root, dirs, _ in os.walk(self.shadow_notes_dir, topdown=False):
                for d in dirs:
                    d_path = os.path.join(root, d)
                    if not os.listdir(d_path):
                        try:
                            os.rmdir(d_path)
                        except Exception:
                            pass
                            
        self._save_mtime_cache()
        logger.info(f"Сканирование внешних файлов завершено. Просканировано: {scanned_count}. "
                    f"Создано заглушек: {created_count}. Обновлено: {updated_count}. "
                    f"Удалено устаревших: {deleted_count}.")

    def parse_markdown_vault(self) -> dict:
        """
        Рекурсивно сканирует все markdown файлы в Obsidian Vault, парсит вики-ссылки,
        теги и frontmatter, и строит структуру графа связей.
        """
        logger.info("Построение графа связей базы знаний Obsidian...")
        nodes = {}  # {note_name: {"path": str, "type": str, "tags": list, "summary": str}}
        edges = []  # [{"source": str, "target": str, "type": str}]
        
        # Находим вики-ссылки: [[ИмяЗаметки]] или [[ИмяЗаметки|Псевдоним]]
        wikilink_pattern = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')
        # Находим теги в тексте: #тег (буквы, цифры, дефис, слеш для подтегов)
        tag_pattern = re.compile(r'(?<!\S)#([A-Za-zА-Яа-я0-9_-]+(?:/[A-Za-zА-Яа-я0-9_-]+)*)')
        
        for root, dirs, files in os.walk(self.vault_path):
            dirs[:] = [d for d in dirs if not self.should_exclude(os.path.join(root, d))]
            
            for file in files:
                if not file.endswith(".md"):
                    continue
                    
                file_path = os.path.join(root, file)
                note_name = file[:-3]  # Имя заметки без .md
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        
                    # Очищаем от секретов перед индексацией
                    content = self.security_filter.redact_text(content)
                    
                    # Парсим Frontmatter (YAML)
                    frontmatter = {}
                    body = content
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            try:
                                frontmatter = yaml.safe_load(parts[1]) or {}
                                body = parts[2]
                            except Exception:
                                pass
                                
                    note_type = frontmatter.get("type", "note")
                    tags = frontmatter.get("tags", [])
                    if isinstance(tags, str):
                        tags = [tags]
                    elif not isinstance(tags, list):
                        tags = []
                        
                    # Извлекаем теги из тела текста
                    text_tags = tag_pattern.findall(body)
                    tags = list(set(tags + text_tags))
                    
                    # Извлекаем вики-ссылки из тела текста
                    links = wikilink_pattern.findall(body)
                    
                    # Также смотрим связи в frontmatter (например, project: [[MyProject]])
                    for val in frontmatter.values():
                        if isinstance(val, str) and val.startswith("[[") and val.endswith("]]"):
                            link_target = val[2:-2].split("|")[0].strip()
                            links.append(link_target)
                            
                    # Очищаем ссылки от пробелов и дубликатов
                    links = list(set([l.strip() for l in links if l.strip()]))
                    
                    # Извлекаем краткое описание заметки (первые 3 строки тела)
                    body_lines = [l.strip() for l in body.split("\n") if l.strip()][:3]
                    summary = " ".join(body_lines)[:200]
                    
                    nodes[note_name] = {
                        "path": file_path,
                        "type": note_type,
                        "tags": tags,
                        "summary": summary
                    }
                    
                    # Добавляем ребра в граф
                    for link in links:
                        edges.append({
                            "source": note_name,
                            "target": link,
                            "type": "wikilink"
                        })
                        
                    # Добавляем связь заметки с её тегами (чтобы теги работали как связующие узлы)
                    for tag in tags:
                        tag_node_name = f"#{tag}"
                        if tag_node_name not in nodes:
                            nodes[tag_node_name] = {
                                "path": "",
                                "type": "tag",
                                "tags": [],
                                "summary": f"Тег базы знаний: #{tag}"
                            }
                        edges.append({
                            "source": note_name,
                            "target": tag_node_name,
                            "type": "tag_link"
                        })
                        
                except Exception as e:
                    logger.error(f"Не удалось обработать заметку {file}: {e}")
                    
        self.graph_data = {"nodes": nodes, "edges": edges}
        
        # Сохраняем граф на диск
        try:
            with open(self.graph_file, "w", encoding="utf-8") as f:
                json.dump(self.graph_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Граф связей успешно построен. Узлов: {len(nodes)}, Связей: {len(edges)}.")
        except Exception as e:
            logger.error(f"Не удалось сохранить файл графа: {e}")
            
        return self.graph_data

# Тестовый запуск индексатора
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    
    # Путь к рабочей папке
    cwd = os.getcwd()
    config_file = os.path.join(cwd, "config.yaml")
    
    if not os.path.exists(config_file):
        print("config.yaml не найден в текущей папке. Тест отменен.")
        sys.exit(0)
        
    indexer = GraphIndexer(config_file)
    # Генерируем теневые заметки
    indexer.generate_shadow_notes()
    # Строим граф
    indexer.parse_markdown_vault()
