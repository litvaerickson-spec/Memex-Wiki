#!/bin/bash

# Скрипт настройки виртуального окружения для Obsidian GraphRAG
set -e

# Цвета для вывода в консоль
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Инициализация окружения Obsidian GraphRAG ===${NC}"

# Проверяем наличие uv
UV_PATH="$HOME/.local/bin/uv"
if [ -f "$UV_PATH" ]; then
    echo -e "Используется найденный менеджер пакетов: ${GREEN}uv${NC} ($UV_PATH)"
    UV_CMD="$UV_PATH"
else
    if command -v uv &> /dev/null; then
        echo -e "Используется системный менеджер пакетов: ${GREEN}uv${NC}"
        UV_CMD="uv"
    else
        echo -e "${YELLOW}Предупреждение: uv не найден в стандартных путях.${NC}"
        echo -e "Пытаемся использовать стандартный python3 -m venv и pip..."
        UV_CMD=""
    fi
fi

# Путь к виртуальному окружению
VENV_DIR=".venv"

if [ -n "$UV_CMD" ]; then
    # Создаем venv с помощью uv
    if [ ! -d "$VENV_DIR" ]; then
        echo -e "Создание виртуального окружения через ${BLUE}uv venv${NC}..."
        $UV_CMD venv $VENV_DIR --python python3
    fi
    # Установка зависимостей через uv
    echo -e "Установка зависимостей через ${BLUE}uv pip install${NC}..."
    $UV_CMD pip install -r requirements.txt
else
    # Создаем стандартный venv
    if [ ! -d "$VENV_DIR" ]; then
        echo -e "Создание виртуального окружения через ${BLUE}python3 -m venv${NC}..."
        python3 -m venv $VENV_DIR
    fi
    # Активируем и устанавливаем зависимостей стандартным pip
    echo -e "Установка зависимостей через ${BLUE}pip3 install${NC}..."
    source $VENV_DIR/bin/activate
    pip3 install --upgrade pip
    pip3 install -r requirements.txt
    deactivate
fi

echo -e "${GREEN}=== Настройка окружения успешно завершена! ===${NC}"
echo -e ""
echo -e "Чтобы активировать виртуальное окружение, выполните:"
echo -e "  ${YELLOW}source .venv/bin/activate${NC}"
echo -e ""
echo -e "Чтобы запустить индексацию и тесты:"
echo -e "  ${YELLOW}python3 src/test_graphrag.py${NC}"
echo -e ""
