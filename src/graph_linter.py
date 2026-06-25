import os
import re
import yaml
import logging
from typing import Dict, Any, List, Set

# Настраиваем логирование
logger = logging.getLogger("graph_linter")

class GraphLinter:
    def __init__(self, config_path: str = None):
        if not config_path:
            cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(cwd, "config.yaml")
        
        self.config_path = config_path
        self.config = {}
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Не удалось загрузить config.yaml: {e}")
                
        self.obsidian_vault = self.config.get("obsidian_vault_path", "/Users/sergej/Documents/AI_Нейросети/01_MyObsidian/")
        self.wiki_dir = self.config.get("wiki_folder_path", os.path.join(self.obsidian_vault, "wiki/"))

    @staticmethod
    def parse_frontmatter(content: str):
        """Парсит frontmatter и возвращает метаданные и тело файла."""
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if m:
            frontmatter_text = m.group(1)
            body = content[m.end():]
            try:
                metadata = yaml.safe_load(frontmatter_text) or {}
                return metadata, body, None
            except Exception as e:
                return {}, body, str(e)
        return {}, content, "Отсутствует YAML frontmatter"

    @staticmethod
    def extract_wikilinks(text: str) -> Set[str]:
        """Находит все ссылки вида [[wiki-link]] в тексте, исключая блоки кода."""
        # Очищаем от блоков кода
        clean_text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        clean_text = re.sub(r"`.*?`", "", clean_text)
        
        links = re.findall(r"\[\[(.*?)\]\]", clean_text)
        cleaned = set()
        for l in links:
            name = l.split("|")[0].strip()
            if name:
                cleaned.add(name.lower()) # приводим к нижнему регистру для сверки ID
        return cleaned

    def lint_memory(self) -> str:
        """Запускает аудит целостности графа знаний и метаданных."""
        if not os.path.exists(self.wiki_dir):
            return "Ошибка: папка wiki/ не существует."

        errors = []
        warnings = []
        
        # Карта файлов: id -> {filepath, metadata, outgoing_links, last_updated}
        file_map = {}
        # Карта всех найденных ID для сверки связей
        all_ids = set()
        
        # 1. Сканируем файлы
        for root, dirs, files in os.walk(self.wiki_dir):
            for f_name in files:
                if f_name.endswith(".md"):
                    filepath = os.path.join(root, f_name)
                    rel_path = os.path.relpath(filepath, self.wiki_dir)
                    
                    # Пропускаем некоторые системные файлы от строгих проверок
                    is_special_system = f_name in ["log.md"]
                    
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read()
                    except Exception as e:
                        errors.append(f"Файл {rel_path} не может быть прочитан: {e}")
                        continue
                        
                    meta, body, err = self.parse_frontmatter(content)
                    
                    if err:
                        if not is_special_system:
                            errors.append(f"Файл {rel_path}: {err}")
                        continue
                        
                    # Проверяем обязательные поля
                    file_id = meta.get("id")
                    if not file_id:
                        errors.append(f"Файл {rel_path} не содержит обязательного поля 'id' во frontmatter")
                        file_id = f_name[:-3].lower()
                        
                    all_ids.add(file_id.lower())
                    
                    # Проверяем типы страниц
                    page_type = meta.get("type")
                    if not page_type and not is_special_system and f_name != "index.md":
                        warnings.append(f"Страница {rel_path} не имеет поля 'type' во frontmatter")
                        
                    # Извлекаем исходящие ссылки из frontmatter `relations` и из тела
                    outgoing = self.extract_wikilinks(body)
                    
                    # Также добавим связи из поля relations во frontmatter
                    relations_raw = meta.get("relations", "")
                    if relations_raw:
                        outgoing.update(self.extract_wikilinks(relations_raw))
                        
                    # Проверяем валидность формата даты обновления
                    last_updated = meta.get("last_updated")
                    if last_updated:
                        # Пытаемся распарсить дату ГГГГ-ММ-ДД
                        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(last_updated)):
                            warnings.append(f"Файл {rel_path}: неверный формат даты '{last_updated}' (ожидается ГГГГ-ММ-ДД)")
                    
                    file_map[file_id.lower()] = {
                        "rel_path": rel_path,
                        "metadata": meta,
                        "outgoing": outgoing,
                        "last_updated": last_updated
                    }
                    
        # 2. Проверяем связи: битые ссылки и изолированные файлы
        incoming_links = {fid: set() for fid in file_map}
        
        # Заполняем входящие ссылки
        for fid, data in file_map.items():
            for out in data["outgoing"]:
                if out in file_map:
                    incoming_links[out].add(fid)
                else:
                    # Исключаем ссылки на системные файлы типа index и log, если они существуют
                    if out not in ["index", "log"]:
                        errors.append(f"Битая ссылка в {data['rel_path']}: указывает на [[{out}]], но этот файл отсутствует в wiki/")

        # Ищем сироты (orphan pages) - исключая index.md
        for fid, data in file_map.items():
            if fid in ["index", "user_profile", "system_rules", "log"]:
                continue
            if not incoming_links.get(fid):
                warnings.append(f"Страница-сирота {data['rel_path']}: на неё нет входящих вики-ссылок из других файлов")

        # 3. Ищем противоречия дат обновлений по связанным страницам
        # Например, если выжимка (summary) обновилась раньше, чем концепт, о котором она говорит,
        # или наоборот, это может указывать на устаревание концепта.
        for fid, data in file_map.items():
            if data["metadata"].get("type") == "summary":
                # Это файл выжимки. Найдем все связанные концепты
                for out in data["outgoing"]:
                    concept_data = file_map.get(out)
                    if concept_data and concept_data["metadata"].get("type") == "concept":
                        # Сверяем даты
                        t_summary = data["last_updated"]
                        t_concept = concept_data["last_updated"]
                        if t_summary and t_concept:
                            try:
                                # Преобразуем в строки для сравнения, если они в формате ГГГГ-ММ-ДД
                                if t_summary > t_concept:
                                    warnings.append(
                                        f"Потенциальное устаревание: выжимка {data['rel_path']} обновилась ({t_summary}), "
                                        f"но связанный концепт [[{out}]] в {concept_data['rel_path']} имеет более старую дату ({t_concept})"
                                    )
                            except Exception:
                                pass

        # 4. Формируем итоговый отчет
        report = []
        report.append("# Отчет о проверке целостности базы знаний (Graph Lint)")
        report.append(f"Всего проверено файлов: {len(file_map)}\n")
        
        if not errors and not warnings:
            report.append("✅ Проверка пройдена! Ошибок и предупреждений не обнаружено.")
            return "\n".join(report)
            
        if errors:
            report.append("❌ **Найденные ошибки (требуют исправления):**")
            for err in errors:
                report.append(f"- {err}")
            report.append("")
            
        if warnings:
            report.append("⚠️ **Предупреждения:**")
            for warn in warnings:
                report.append(f"- {warn}")
            report.append("")
            
        return "\n".join(report)

if __name__ == "__main__":
    linter = GraphLinter()
    print(linter.lint_memory())
