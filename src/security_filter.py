import re
import os
import yaml
import logging

logger = logging.getLogger("security_filter")

class SecurityFilter:
    def __init__(self, config_path: str = None):
        self.redact_secrets = True
        self.patterns = []
        
        # Загружаем конфигурацию
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    security_config = config.get("security", {})
                    self.redact_secrets = security_config.get("redact_secrets", True)
                    
                    raw_patterns = security_config.get("redact_patterns", [])
                    for p in raw_patterns:
                        name = p.get("name", "Unknown Pattern")
                        regex_str = p.get("regex")
                        if regex_str:
                            try:
                                compiled = re.compile(regex_str)
                                self.patterns.append({"name": name, "regex": compiled})
                            except re.error as e:
                                logger.error(f"Ошибка компиляции регулярного выражения {name}: {e}")
            except Exception as e:
                logger.error(f"Не удалось загрузить config.yaml для SecurityFilter: {e}")
                
        # Если конфиг не загрузился или пуст, используем надежные дефолтные паттерны
        if not self.patterns:
            self._set_default_patterns()

    def _set_default_patterns(self):
        defaults = [
            ("Google API Key / Gemini", r'AIzaSy[A-Za-z0-9_-]{33}'),
            ("OpenAI API Key", r'sk-[A-Za-z0-9]{20,}'),
            ("OpenAI Project API Key", r'sk-proj-[A-Za-z0-9_-]{40,}'),
            ("Database Connection String", r'(mongodb\+srv|mongodb|postgresql|postgres|mysql|redis):\/\/[A-Za-z0-9_]+:[^@\s]+@[A-Za-z0-9.-]+(?::\d+)?(?:\/[A-Za-z0-9_.-]*)?'),
            ("Generic Password/Token Assign", r'(?i)(password|passwd|token|secret|private_key)\s*[:=]\s*["\']([^"\'\s]{4,})["\']')
        ]
        for name, regex_str in defaults:
            self.patterns.append({
                "name": name,
                "regex": re.compile(regex_str)
            })

    def redact_text(self, text: str) -> str:
        """
        Сканирует входящий текст и заменяет все найденные пароли, токены и API-ключи на заглушки.
        """
        if not text or not self.redact_secrets:
            return text
            
        redacted = text
        for p in self.patterns:
            name = p["name"]
            regex = p["regex"]
            
            # Для "Generic Password/Token Assign" мы хотим заменить только само секретное значение,
            # сохранив переменную и знак присвоения (например, password="секрет" -> password="[REDACTED_PASSWORD]").
            if name == "Generic Password/Token Assign":
                def replace_generic(match):
                    full_match = match.group(0)
                    key_var = match.group(1)
                    # Находим кавычки
                    quotes = '"' if '"' in full_match else "'"
                    return f"{key_var}={quotes}[REDACTED_{key_var.upper()}]{quotes}"
                
                redacted = regex.sub(replace_generic, redacted)
            else:
                # Для остальных паттернов заменяем всё совпадение целиком
                placeholder = f"[REDACTED_{name.replace(' ', '_').upper()}]"
                redacted = regex.sub(placeholder, redacted)
                
        return redacted

# Простой скрипт тестирования фильтра
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    filt = SecurityFilter()
    test_text = """
    # Конфигурация проекта
    database_url = "postgresql://admin:super-hard-pass123@localhost:5432/my_db"
    openai_key = "sk-proj-1234567890abcdef1234567890abcdef1234567890"
    gemini_key = "AIzaSyA5VY99OIEvQwa2Dwyp8-fGjMrtKR-gfvQ"
    
    # Обычный текст
    Тут идет обычное описание проекта без паролей.
    Пароль администратора: "qwerty12345"
    token = 'github_pat_12345'
    """
    print("=== ИСХОДНЫЙ ТЕКСТ ===")
    print(test_text)
    print("\n=== ОЧИЩЕННЫЙ ТЕКСТ ===")
    print(filt.redact_text(test_text))
