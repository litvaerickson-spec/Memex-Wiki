import os
import re
import json
import yaml
import urllib.request
import urllib.error
import subprocess
import logging
from datetime import datetime
from typing import Dict, Any, List, Tuple

# Настраиваем логирование
logger = logging.getLogger("memex_tools")

# Загружаем фильтр безопасности
try:
    from security_filter import SecurityFilter
except ImportError:
    class SecurityFilter:
        def __init__(self, *args, **kwargs): pass
        def redact_text(self, text: str) -> str: return text

class MemexTools:
    def __init__(self, config_path: str = None):
        if not config_path:
            cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(cwd, "config.yaml")
        
        self.config_path = config_path
        self.config = {}
        self.security_filter = SecurityFilter(config_path)
        self.load_config()

    def load_config(self):
        """Загружает файл конфигурации config.yaml."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Не удалось загрузить config.yaml: {e}")
        
        self.obsidian_vault = self.config.get("obsidian_vault_path", "")
        self.wiki_dir = self.config.get("wiki_folder_path", os.path.join(self.obsidian_vault, "wiki/") if self.obsidian_vault else "")
        self.raw_dir = self.config.get("raw_folder_path", "")
        
        # Настройки LLM
        llm_cfg = self.config.get("llm", {})
        self.llm_backend = llm_cfg.get("backend", "gemini")
        self.llm_model = llm_cfg.get("model", "gemini-2.5-flash")
        self.ollama_url = llm_cfg.get("ollama_url", "http://localhost:11434/api/generate")
        self.gemini_key = llm_cfg.get("gemini_api_key", "")
        
        # Настройки RLM
        rlm_cfg = self.config.get("rlm", {})
        self.max_chunk_tokens = rlm_cfg.get("max_chunk_tokens", 3000)
        self.chunk_overlap = rlm_cfg.get("chunk_overlap", 300)
        
        self.exclude_patterns = self.config.get("exclude_patterns", [])

    def get_gemini_api_key(self) -> str:
        """Ищет ключ Gemini API в конфиге и переменных окружения."""
        if self.gemini_key:
            return self.gemini_key
        for key in ["GEMINI_API_KEY", "ANTIGRAVITY_KEY_1"]:
            val = os.environ.get(key)
            if val:
                return val
        return ""

    def query_llm(self, prompt: str, system_instruction: str = "") -> str:
        """Отправляет запрос к LLM (Gemini API или Ollama) с очисткой безопасности."""
        safe_prompt = self.security_filter.redact_text(prompt)
        
        if self.llm_backend == "gemini":
            key = self.get_gemini_api_key()
            if not key:
                raise ValueError("Не найден API-ключ Gemini (GEMINI_API_KEY или ANTIGRAVITY_KEY_1)")
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.llm_model}:generateContent?key={key}"
            
            body = {
                "contents": [{"parts": [{"text": safe_prompt}]}]
            }
            if system_instruction:
                body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
            
            req_data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
            
            import time
            max_retries = 5
            retry_delay = 5
            for attempt in range(max_retries):
                try:
                    with urllib.request.urlopen(req, timeout=40) as response:
                        res_data = json.loads(response.read().decode("utf-8"))
                        text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                        return self.security_filter.redact_text(text)
                except urllib.error.HTTPError as e:
                    err_content = e.read().decode("utf-8")
                    if e.code == 429 and attempt < max_retries - 1:
                        logger.warning(f"Превышен лимит запросов Gemini (429). Попытка {attempt+1}/{max_retries}. Повтор через {retry_delay} сек...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    logger.error(f"Gemini API Error: {e.code} - {err_content}")
                    raise RuntimeError(f"Gemini API returned error {e.code}: {err_content}")
                except Exception as e:
                    logger.error(f"Ошибка при вызове Gemini API: {e}")
                    raise
        
        elif self.llm_backend == "ollama":
            body = {
                "model": self.llm_model,
                "prompt": safe_prompt,
                "stream": False
            }
            if system_instruction:
                body["system"] = system_instruction
                
            req_data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(self.ollama_url, data=req_data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    res_data = json.loads(response.read().decode("utf-8"))
                    text = res_data.get("response", "")
                    return self.security_filter.redact_text(text)
            except Exception as e:
                logger.error(f"Ошибка при вызове Ollama: {e}")
                raise
        else:
            raise ValueError(f"Неизвестный backend LLM: {self.llm_backend}")

    # --- Парсинг и форматирование файлов ---

    @staticmethod
    def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
        """Парсит YAML frontmatter в начале Markdown-файла."""
        if not content:
            return {}, ""
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if m:
            frontmatter_text = m.group(1)
            body = content[m.end():]
            try:
                metadata = yaml.safe_load(frontmatter_text) or {}
                return metadata, body
            except Exception as e:
                logger.error(f"Ошибка парсинга YAML во frontmatter: {e}")
                return {}, body
        return {}, content

    @staticmethod
    def format_frontmatter(metadata: Dict[str, Any]) -> str:
        """Форматирует словарь метаданных в YAML frontmatter."""
        yaml_text = yaml.safe_dump(metadata, allow_unicode=True, default_flow_style=False).strip()
        return f"---\n{yaml_text}\n---\n"

    @staticmethod
    def extract_wikilinks(text: str) -> List[str]:
        """Находит все ссылки вида [[wiki-link]] в тексте, исключая блоки кода."""
        # Очищаем от блоков кода
        clean_text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        clean_text = re.sub(r"`.*?`", "", clean_text)
        
        links = re.findall(r"\[\[(.*?)\]\]", clean_text)
        cleaned = []
        for l in links:
            name = l.split("|")[0].strip()
            if name:
                cleaned.append(name)
        return list(set(cleaned))

    @staticmethod
    def normalize_concept_name(name: str) -> str:
        """Приводит имя концепта к нижнему регистру и заменяет пробелы/подчеркивания на дефисы."""
        if name.lower().endswith(".md"):
            name = name[:-3]
        normalized = name.lower().strip()
        # Заменяем все небуквенные и нецифровые символы на дефисы
        normalized = re.sub(r"[^a-zа-я0-9\-_]", "-", normalized)
        normalized = re.sub(r"-+", "-", normalized)
        return normalized.strip("-")

    def chunk_text(self, text: str, max_tokens: int = 3000, overlap: int = 300) -> List[str]:
        """Нарезает длинный текст на чанки с перекрытием (1 токен ≈ 3 символа)."""
        max_chars = max_tokens * 3
        overlap_chars = overlap * 3
        
        if len(text) <= max_chars:
            return [text]
            
        lines = text.split("\n")
        chunks = []
        current_chunk = []
        current_len = 0
        
        for line in lines:
            line_len = len(line) + 1
            if current_len + line_len > max_chars:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                overlap_size = 0
                overlap_chunk = []
                for prev_line in reversed(current_chunk):
                    if overlap_size + len(prev_line) + 1 <= overlap_chars:
                        overlap_chunk.insert(0, prev_line)
                        overlap_size += len(prev_line) + 1
                    else:
                        break
                current_chunk = overlap_chunk
                current_len = overlap_size
            current_chunk.append(line)
            current_len += line_len
            
        if current_chunk:
            chunks.append("\n".join(current_chunk))
            
        return chunks

    def git_commit(self, commit_message: str) -> bool:
        """Выполняет git add . && git commit в папке wiki_dir."""
        if not os.path.exists(os.path.join(self.wiki_dir, ".git")):
            return False
        try:
            subprocess.run(["git", "add", "."], cwd=self.wiki_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            res = subprocess.run(["git", "commit", "-m", commit_message], cwd=self.wiki_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return res.returncode == 0 or "nothing to commit" in res.stdout.decode("utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"Исключение при Git коммите: {e}")
            return False

    # --- Бизнес-логика: Ingest Source ---

    def ingest_source(self, filename: str) -> str:
        """
        Импортирует исходный документ из raw-папки проектов,
        производит CoT-выделение сущностей и создает/обновляет страницы wiki.
        """
        # Безопасное разрешение путей
        raw_abs = os.path.abspath(self.raw_dir)
        filepath = os.path.abspath(os.path.join(self.raw_dir, filename))
        
        # Проверяем на выход за границы raw_dir
        if not filepath.startswith(raw_abs):
            return f"Ошибка безопасности: файл {filename} находится вне разрешенной папки {self.raw_dir}"
            
        # Проверяем, что файл не находится внутри папки wiki или Antigravity во избежание коллизий
        wiki_abs = os.path.abspath(self.wiki_dir)
        workspace_abs = os.path.abspath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if filepath.startswith(wiki_abs) or filepath.startswith(workspace_abs):
            return f"Ошибка коллизии: запрещено индексировать служебные папки wiki/ и Antigravity/"
            
        if not os.path.exists(filepath):
            return f"Ошибка: файл {filepath} не найден."
            
        # Читаем исходный файл (поддержка разных форматов)
        try:
            if filepath.lower().endswith(".pdf"):
                import pypdf
                reader = pypdf.PdfReader(filepath)
                content = ""
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        content += text + "\n"
            elif filepath.lower().endswith(".docx"):
                import docx
                doc = docx.Document(filepath)
                content = "\n".join([p.text for p in doc.paragraphs])
            else:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
        except Exception as e:
            return f"Ошибка при чтении файла {filename}: {e}"
            
        # Ограничиваем размер контента до 15 000 символов во избежание перегрева LLM
        max_chars = 15000
        if len(content) > max_chars:
            content = content[:10000] + f"\n\n... [Текст обрезан во избежание перегрева, исходный размер {len(content)} символов] ...\n\n" + content[-5000:]
            
        basename = os.path.basename(filepath)
        source_id = self.normalize_concept_name(os.path.splitext(basename)[0])
        
        # Шаг 1: Двухэтапный синтаксический анализ (CoT + JSON экстракция)
        system_instruction = (
            "Ты — аналитик долгосрочной памяти ИИ. Твоя задача — извлечь из текста ключевые понятия, "
            "технологии (например, операционные системы, такие как Astra Linux), программное обеспечение, проекты и сущности. "
            "Сопоставляй синонимы. Выделяй все значимые упоминания технологий как отдельные концепты. "
            "Отвечай строго в формате JSON."
        )
        
        prompt = f"""
Проанализируй следующий документ '{basename}':
---
{content}
---

Выдели ключевые концепты, сущности (включая операционные системы, такие как Astra Linux, программное обеспечение, организации) или проекты.
Сначала проведи Chain-of-Thought рассуждения о том, какие сущности здесь упоминаются, как они связаны и какие синонимы могут быть объединены. Убедись, что упомянутые операционные системы или технологии (например, Astra Linux) выделены как концепты.
Затем сформируй результат СТРОГО в следующем формате JSON (без Markdown форматирования, только сырой JSON):
{{
  "summary": "Краткая выжимка (1-2 предложения) сути данного документа.",
  "tags": ["тег1", "тег2"],
  "concepts": [
    {{
      "id": "нормализованный-id-через-дефис (например, astra-linux)",
      "title": "Человекочитаемое Название Концепта",
      "summary": "Одно предложение: что это за концепт.",
      "description": "Подробное описание концепта и его контекста из данного документа (с цитатами)."
    }}
  ]
}}
"""
        try:
            response_raw = self.query_llm(prompt, system_instruction)
            # Очищаем от возможных ```json ... ``` оберток
            clean_json = re.sub(r"^```json\s*", "", response_raw.strip())
            clean_json = re.sub(r"\s*```$", "", clean_json.strip())
            extracted = json.loads(clean_json)
        except Exception as e:
            logger.error(f"Не удалось извлечь сущности через LLM: {e}. Сырой ответ: {response_raw if 'response_raw' in locals() else 'None'}")
            return f"Ошибка при извлечении сущностей через LLM: {e}"
            
        summary_text = extracted.get("summary", "")
        tags = extracted.get("tags", [])
        concepts = extracted.get("concepts", [])
        for c in concepts:
            if "id" in c:
                c["id"] = self.normalize_concept_name(c["id"])
        
        # Добавляем стандартные теги
        if "summary" not in tags:
            tags.append("summary")
            
        today = datetime.today().strftime("%Y-%m-%d")
        
        # Собираем список связей (relations)
        relations_list = [f"[[{c['id']}]]" for c in concepts]
        relations_str = ", ".join(relations_list)
        
        # 1. Создаем страницу-выжимку источника: wiki/[source_id].md
        summary_filename = f"{source_id}.md"
        summary_filepath = os.path.join(self.wiki_dir, summary_filename)
        
        summary_metadata = {
            "id": source_id,
            "type": "summary",
            "tags": tags,
            "last_updated": today,
            "relations": relations_str
        }
        
        summary_content = self.format_frontmatter(summary_metadata)
        summary_content += f"# {basename}\n\n"
        summary_content += f"**Summary**: {summary_text}\n\n"
        summary_content += f"**Sources**: (source: {basename})\n\n"
        summary_content += "---\n\n"
        summary_content += "## Содержание источника\n"
        # Сокращенный текст оригинального файла
        summary_content += f"{content[:1500]}...\n\n" if len(content) > 1500 else f"{content}\n\n"
        summary_content += "## Related pages\n"
        for c in concepts:
            summary_content += f"- [[{c['id']}]]\n"
            
        os.makedirs(os.path.dirname(summary_filepath), exist_ok=True)
        with open(summary_filepath, "w", encoding="utf-8") as f:
            f.write(summary_content)
            
        # 2. Создаем или обновляем концептуальные страницы: wiki/[concept-name].md
        created_concepts = []
        for c in concepts:
            c_id = c["id"]
            c_title = c["title"]
            c_summary = c["summary"]
            c_desc = c["description"]
            
            c_filename = f"{c_id}.md"
            c_filepath = os.path.join(self.wiki_dir, c_filename)
            
            # Проверяем, существует ли файл
            if os.path.exists(c_filepath):
                with open(c_filepath, "r", encoding="utf-8") as f:
                    old_content = f.read()
                old_meta, old_body = self.parse_frontmatter(old_content)
                
                # Обновляем метаданные
                old_tags = old_meta.get("tags", [])
                new_tags = list(set(old_tags + tags + ["concept"]))
                if "summary" in new_tags: new_tags.remove("summary")
                
                old_relations = self.extract_wikilinks(old_meta.get("relations", ""))
                new_relations = list(set(old_relations + [source_id]))
                new_relations_str = ", ".join([f"[[{r}]]" for r in new_relations])
                
                old_meta["tags"] = new_tags
                old_meta["last_updated"] = today
                old_meta["relations"] = new_relations_str
                
                # Интегрируем новое описание в старое содержание
                updated_content = self.format_frontmatter(old_meta)
                # Парсим старое описание, ищем "## Описание" или "## Основное содержание"
                body_split = re.split(r"(## (?:Описание|Основное содержание))", old_body, maxsplit=1)
                
                if len(body_split) == 3:
                    header = body_split[1]
                    rest = body_split[2]
                    # Вставляем новый абзац с цитированием
                    updated_content += body_split[0] + header + "\n"
                    updated_content += f"- {c_desc} (source: {basename})\n" + rest
                else:
                    # Если структуры нет, просто дописываем
                    updated_content += old_body + f"\n\n## Описание\n- {c_desc} (source: {basename})\n"
            else:
                # Создаем новый концепт
                c_metadata = {
                    "id": c_id,
                    "type": "concept",
                    "tags": ["concept"],
                    "last_updated": today,
                    "relations": f"[[{source_id}]]"
                }
                updated_content = self.format_frontmatter(c_metadata)
                updated_content += f"# {c_title}\n\n"
                updated_content += f"**Summary**: {c_summary}\n\n"
                updated_content += f"**Sources**: (source: {basename})\n\n"
                updated_content += "---\n\n"
                updated_content += f"## Описание\n{c_desc} (source: {basename})\n\n"
                updated_content += "## Related pages\n"
                updated_content += f"- [[{source_id}]]\n"
                
            with open(c_filepath, "w", encoding="utf-8") as f:
                f.write(updated_content)
            created_concepts.append(c_id)
            
        # 3. Обновляем оглавление wiki/index.md
        index_filepath = os.path.join(self.wiki_dir, "index.md")
        if os.path.exists(index_filepath):
            with open(index_filepath, "r", encoding="utf-8") as f:
                index_content = f.read()
            idx_meta, idx_body = self.parse_frontmatter(index_content)
            
            # Извлекаем текущие концепты и добавляем новые
            all_links = self.extract_wikilinks(idx_body)
            # Обновляем список
            lines = idx_body.split("\n")
            
            # Ищем, где начинается список концептов
            concept_idx = -1
            for i, line in enumerate(lines):
                if "## Скомпилированные концепты" in line:
                    concept_idx = i
                    break
                    
            if concept_idx != -1:
                # Очищаем дефолтную заглушку о пустой базе
                for k in range(concept_idx + 1, len(lines)):
                    if "База знаний пуста" in lines[k]:
                        lines[k] = ""
                
                # Добавляем новые записи
                new_entries = []
                # Добавим сам файл источника
                if f"[[{source_id}]]" not in idx_body:
                    new_entries.append(f"- [[{source_id}]] — Выжимка из источника {basename}.")
                # Добавим новые концепты
                for c in concepts:
                    if f"[[{c['id']}]]" not in idx_body:
                        new_entries.append(f"- [[{c['id']}]] — {c['summary']}")
                        
                if new_entries:
                    lines.insert(concept_idx + 1, "\n".join(new_entries))
                    
            idx_meta["last_updated"] = today
            new_index_content = self.format_frontmatter(idx_meta) + "\n".join(lines)
            with open(index_filepath, "w", encoding="utf-8") as f:
                f.write(new_index_content)
                
        # 4. Обновляем лог wiki/log.md
        log_filepath = os.path.join(self.wiki_dir, "log.md")
        if os.path.exists(log_filepath):
            with open(log_filepath, "r", encoding="utf-8") as f:
                log_content = f.read()
            log_meta, log_body = self.parse_frontmatter(log_content)
            
            log_lines = log_body.split("\n")
            history_idx = -1
            for i, line in enumerate(log_lines):
                if "## История изменений" in line:
                    history_idx = i
                    break
                    
            if history_idx != -1:
                log_entry = (
                    f"- **{today}**: Импортирован файл `{basename}`. "
                    f"Создана выжимка [[{source_id}]]. Созданы/обновлены концепты: {', '.join([f'[[{c}]]' for c in created_concepts])}."
                )
                log_lines.insert(history_idx + 1, log_entry)
                
            log_meta["last_updated"] = today
            new_log_content = self.format_frontmatter(log_meta) + "\n".join(log_lines)
            with open(log_filepath, "w", encoding="utf-8") as f:
                f.write(new_log_content)
                
        # 5. Git Commit
        self.git_commit(f"auto-commit: Ingest {basename}")
        
        return f"Успешно обработан файл: {basename}. Создана выжимка [[{source_id}]] и концепты: {', '.join(created_concepts)}"

    # --- Бизнес-логика: Query Memory (RLM Engine) ---

    def query_memory(self, query: str) -> str:
        """
        Точечный поиск по базе знаний с последовательной RLM-фильтрацией.
        Определяет кандидатов по оглавлению, опрашивает чанки, собирает факты.
        """
        # 1. Читаем все файлы в wiki/ (включая поддиректории) и собираем информацию о них
        wiki_files = []
        if not os.path.exists(self.wiki_dir):
            return "База знаний пуста или папка wiki/ не создана."
            
        for root, dirs, files in os.walk(self.wiki_dir):
            for f_name in files:
                if f_name.endswith(".md") and f_name not in ["index.md", "log.md"]:
                    filepath = os.path.join(root, f_name)
                    rel_path = os.path.relpath(filepath, self.wiki_dir)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read()
                        meta, _ = self.parse_frontmatter(content)
                        
                        # Ищем заголовок и summary
                        summary_match = re.search(r"\*\*Summary\*\*:\s*(.*)", content)
                        summary = summary_match.group(1).strip() if summary_match else ""
                        
                        wiki_files.append({
                            "id": meta.get("id", f_name[:-3]),
                            "type": meta.get("type", "unknown"),
                            "summary": summary,
                            "filename": rel_path
                        })
                    except Exception as e:
                        logger.error(f"Не удалось распарсить файл {rel_path}: {e}")
                    
        if not wiki_files:
            return "В базе знаний wiki/ нет доступных файлов концептов или выжимок."
            
        # 2. LLM выбирает кандидатов на основе оглавления
        files_summary = "\n".join([
            f"- ID: {f['id']} (тип: {f['type']}) — Сводка: {f['summary']}"
            for f in wiki_files
        ])
        
        system_select = "Ты — библиотекарь базы знаний. Твоя задача — отобрать наиболее релевантные файлы для ответа на запрос."
        prompt_select = f"""
Доступные файлы памяти (сводка):
{files_summary}

Запрос пользователя: "{query}"

Выбери из списка ID файлов (не более 3-4 самых подходящих), которые содержат информацию для ответа на этот запрос.
Верни ответ СТРОГО в следующем формате JSON (без Markdown форматирования):
{{
  "relevant_files": ["id1", "id2"]
}}
"""
        try:
            res_select = self.query_llm(prompt_select, system_select)
            clean_select = re.sub(r"^```json\s*", "", res_select.strip())
            clean_select = re.sub(r"\s*```$", "", clean_select.strip())
            selected_ids = json.loads(clean_select).get("relevant_files", [])
        except Exception as e:
            logger.error(f"Ошибка выбора кандидатов: {e}. Сырой ответ: {res_select if 'res_select' in locals() else 'None'}")
            # Фолбэк: берем все файлы, если их мало, или первые 3
            selected_ids = [f["id"] for f in wiki_files[:3]]
            
        # 3. Последовательная RLM-фильтрация выбранных файлов
        collected_facts = []
        
        for file_id in selected_ids:
            # Находим имя файла по ID
            matching = [f for f in wiki_files if f["id"] == file_id]
            if not matching:
                continue
            filename = matching[0]["filename"]
            filepath = os.path.join(self.wiki_dir, filename)
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    file_text = f.read()
            except Exception as e:
                logger.error(f"Не удалось прочитать файл {filename}: {e}")
                continue
                
            # Нарезаем файл на чанки по 3000 токенов (9000 символов)
            chunks = self.chunk_text(file_text, max_tokens=self.max_chunk_tokens, overlap=self.chunk_overlap)
            
            # Sequential Batching (опрос по одному в цикле)
            for idx, chunk in enumerate(chunks):
                system_filter = "Ты — фильтр релевантности контекста. Твоя задача — извлечь точные факты."
                prompt_filter = f"""
Фрагмент документа [[{file_id}]]:
---
{chunk}
---

Вопрос пользователя: "{query}"

Содержит ли данный фрагмент конкретные факты или утверждения, полезные для ответа на вопрос пользователя?
Ответь строго в формате JSON (без Markdown форматирования):
{{
  "has_answer": true или false,
  "key_facts": "Ключевые факты и цитаты из этого фрагмента, которые отвечают на вопрос. Обязательно добавь к фактам метку (source: {filename}). Если информации нет, оставь строку пустой."
}}
"""
                try:
                    res_filter = self.query_llm(prompt_filter, system_filter)
                    clean_filter = re.sub(r"^```json\s*", "", res_filter.strip())
                    clean_filter = re.sub(r"\s*```$", "", clean_filter.strip())
                    filter_data = json.loads(clean_filter)
                    
                    if filter_data.get("has_answer") and filter_data.get("key_facts"):
                        collected_facts.append(filter_data["key_facts"])
                except Exception as e:
                    logger.error(f"Ошибка RLM-фильтрации чанка {idx} файла {filename}: {e}")
                    
        # 4. Финальный синтез ответа
        if not collected_facts:
            return "В базе знаний не найдено информации по вашему запросу."
            
        facts_context = "\n\n".join(collected_facts)
        system_synthesis = "Ты — эксперт, формирующий ответ на основе долгосрочной памяти."
        prompt_synthesis = f"""
Контекст из долгосрочной памяти:
===
{facts_context}
===

Вопрос пользователя: "{query}"

Сформулируй точный, развернутый и конкретный ответ на вопрос пользователя на основе предоставленного контекста.
Используй цитирование источников вида (source: имя_файла.md) для каждого приведенного факта.
Если в контексте нет точного ответа, укажи, что информация в памяти отсутствует или неполная.
"""
        try:
            final_answer = self.query_llm(prompt_synthesis, system_synthesis)
            return final_answer
        except Exception as e:
            return f"Ошибка синтеза финального ответа: {e}"
