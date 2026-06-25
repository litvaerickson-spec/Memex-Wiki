#!/bin/bash
# Скрипт автоматического запуска сервера и открытия веб-панели Memex-Wiki

# Очищаем терминал
clear
echo "=== Запуск локальной панели управления Memex-Wiki ==="

# Проверяем, запущен ли уже веб-сервер на порту 8000
if ! lsof -i :8000 -t >/dev/null; then
    # Запуск сервера из папки проекта через виртуальное окружение .venv
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    ROOT_DIR="$( dirname "$SCRIPT_DIR" )"
    "$ROOT_DIR/.venv/bin/python3" "$ROOT_DIR/src/web_server.py" > /dev/null 2>&1 &
    # Даем серверу время на инициализацию портов
    sleep 1.5
else
    echo "Веб-сервер Memex-Wiki уже работает на порту 8000."
fi

# Открываем веб-интерфейс в браузере по умолчанию
echo "Открытие веб-интерфейса в браузере..."
open http://localhost:8000

echo "Готово! Окно можно закрыть."
exit 0
