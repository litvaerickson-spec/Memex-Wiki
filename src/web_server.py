import os
import sys
import json
import re
import yaml
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import threading

pull_status = {"status": "idle", "progress": 0, "error": None, "model": ""}
pull_status_lock = threading.RLock()

# ── Тихая (фоновая) индексация ───────────────────────────────────────────────
# Состояние разделяется между потоком и HTTP-обработчиками через dict + Lock.
_quiet_lock = threading.RLock()
_quiet_state = {
    "running":  False,
    "stopped":  False,
    "total":    0,
    "done":     0,
    "errors":   0,
    "current":  "",
    "queue":    [],          # список rel_path файлов
    "log":      [],          # последние N строк лога
    "eta_sec":  0,
    "delay_sec": 30,         # пауза между файлами (сек)
}

def _quiet_get_cpu_idle():
    """Два замера top, возвращает CPU idle% за последнюю секунду."""
    import subprocess as _sp, re as _re
    try:
        out = _sp.run(["top", "-l", "2", "-n", "0", "-s", "1"],
                      capture_output=True, text=True, timeout=6).stdout
        matches = _re.findall(r"(\d+\.\d+)%\s+idle", out)
        if len(matches) >= 2:
            return float(matches[-1])
        if matches:
            return float(matches[0])
    except Exception:
        pass
    return 100.0


def quiet_ingest_worker():
    """Фоновый поток тихой индексации.

    Принципы:
    - os.nice(19) — минимальный приоритет CPU.
    - Пауза DELAY_SEC между файлами (по умолчанию 30 сек).
    - Перед каждым файлом проверяет CPU idle:
      если < 70% — ждёт дополнительно до 120 сек, переопрашивая каждые 10 сек.
    - Никогда не держит открытое HTTP-соединение.
    """
    import time, traceback

    # _log нужен до импорта tools — определяем первым
    def _log(msg):
        logger.info(f"[quiet] {msg}")
        with _quiet_lock:
            _quiet_state["log"].append(msg)
            if len(_quiet_state["log"]) > 100:
                _quiet_state["log"] = _quiet_state["log"][-100:]

    try:
        # Используем глобальный экземпляр MemexTools (инициализирован при старте сервера)
        # tools — это MemexTools объект, объявленный на уровне модуля
        try:
            os.nice(19)
        except Exception:
            pass

        COOL_THRESHOLD  = 70   # % CPU idle — «достаточно холодно»
        MAX_THERMAL_WAIT = 120  # максимум секунд ожидания остывания перед файлом

        with _quiet_lock:
            queue = list(_quiet_state["queue"])
            delay = _quiet_state["delay_sec"]

        _log(f"🌙 Воркер запущен: {len(queue)} файлов, пауза {delay}с")

        for idx, rel_path in enumerate(queue):
            with _quiet_lock:
                if _quiet_state["stopped"]:
                    break
                _quiet_state["current"] = rel_path
                _quiet_state["eta_sec"] = (len(queue) - idx) * delay

            _log(f"[{idx+1}/{len(queue)}] Проверяю температуру перед: {rel_path}")

            # ── Thermal wait ──────────────────────────────────────────────
            waited = 0
            while waited < MAX_THERMAL_WAIT:
                with _quiet_lock:
                    if _quiet_state["stopped"]:
                        break
                cpu_idle = _quiet_get_cpu_idle()
                if cpu_idle >= COOL_THRESHOLD:
                    break
                _log(f"  🌡 CPU idle {cpu_idle:.0f}% < {COOL_THRESHOLD}% — жду 10 сек...")
                time.sleep(10)
                waited += 10

            with _quiet_lock:
                if _quiet_state["stopped"]:
                    break

            # ── Импорт файла ──────────────────────────────────────────────
            _log(f"  ▶ Индексирую: {rel_path}")
            try:
                tools.load_config()
                result = tools.ingest_source(rel_path)

                ok = not result.startswith("Ошибка")
                with _quiet_lock:
                    if ok:
                        _quiet_state["done"] += 1
                    else:
                        _quiet_state["errors"] += 1
                
                if ok:
                    _log(f"  ✓ Готово: {rel_path}")
                else:
                    _log(f"  ✗ Ошибка LLM: {result[:120]}")
            except Exception as e:
                tb = traceback.format_exc(limit=3)
                with _quiet_lock:
                    _quiet_state["errors"] += 1
                _log(f"  ✗ Exception: {str(e)[:120]}")
                _log(f"    {tb.splitlines()[-1]}")

            with _quiet_lock:
                if _quiet_state["stopped"]:
                    break

            # ── Пауза между файлами ───────────────────────────────────────
            # 1-секундные шаги — Стоп реагирует мгновенно.
            for _ in range(delay):
                with _quiet_lock:
                    if _quiet_state["stopped"]:
                        break
                time.sleep(1)

        with _quiet_lock:
            _quiet_state["running"] = False
            _quiet_state["current"] = ""
        _log("✅ Тихая индексация завершена.")

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[quiet] Критическая ошибка воркера: {e}\n{tb}")
        _log(f"💥 Критическая ошибка: {str(e)[:200]}")
        with _quiet_lock:
            _quiet_state["running"] = False


def pull_model_thread(model_name, ollama_url_base):
    global pull_status
    with pull_status_lock:
        pull_status = {"status": "downloading", "progress": 0, "error": None, "model": model_name}
    
    try:
        parsed = urllib.parse.urlparse(ollama_url_base)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        url = f"{base_url}/api/pull"
        
        req = urllib.request.Request(
            url,
            data=json.dumps({"name": model_name}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        
        with urllib.request.urlopen(req) as response:
            for line in response:
                if not line:
                    continue
                data = json.loads(line.decode("utf-8"))
                status = data.get("status", "")
                
                with pull_status_lock:
                    if status == "success":
                        pull_status = {"status": "success", "progress": 100, "error": None, "model": model_name}
                        return
                    
                    total = data.get("total", 0)
                    completed = data.get("completed", 0)
                    if total > 0:
                        pct = int((completed / total) * 100)
                        pull_status["progress"] = pct
                        pull_status["status"] = "downloading"
                    else:
                        pull_status["status"] = status
    except Exception as e:
        import logging
        logging.getLogger("web_server").error(f"Error pulling model {model_name}: {e}")
        with pull_status_lock:
            pull_status = {"status": "error", "progress": 0, "error": str(e), "model": model_name}


# Добавляем текущую папку в пути поиска модулей
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from memex_tools import MemexTools
from graph_linter import GraphLinter

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web_server")

# Разрешаем запуск из любой директории
cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(cwd, "config.yaml")

# Инициализируем инструменты
tools = MemexTools(config_path)
linter = GraphLinter(config_path)

def should_exclude(rel_path, exclude_patterns):
    parts = rel_path.replace("\\", "/").split("/")
    for part in parts:
        if part in exclude_patterns:
            return True
    for pat in exclude_patterns:
        if pat.startswith("*."):
            ext = pat[1:] # e.g. .png
            if rel_path.lower().endswith(ext.lower()):
                return True
    # Игнорируем скрытые файлы (начинающиеся с точки)
    for part in parts:
        if part.startswith("."):
            return True
    return False

def get_ollama_models(ollama_url_base="http://localhost:11434"):
    import urllib.request
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(ollama_url_base)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        url = f"{base_url}/api/tags"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2.0) as response:
            data = json.loads(response.read().decode("utf-8"))
            return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        logger.error(f"Error fetching Ollama models: {e}")
        return []

class MemexDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Подавляем стандартный лог запросов в консоли, оставляя только важные сообщения
        pass

    def _set_headers(self, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # 1. Отдача веб-интерфейса (HTML)
        if path in ["/", "/index.html"]:
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                self._set_headers(200, "text/html")
                self.wfile.write(html_content.encode("utf-8"))
            except Exception as e:
                self._set_headers(500, "text/plain")
                self.wfile.write(f"Ошибка загрузки интерфейса: {e}".encode("utf-8"))
            return

        # 2. API: Получение конфигурации
        if path == "/api/config":
            tools.load_config()
            llm_config = tools.config.get("llm", {})
            obsidian_vault = tools.config.get("obsidian_vault_path", "")
            raw_folder = tools.config.get("raw_folder_path", "")
            exclude_patterns = tools.config.get("exclude_patterns", [])
            self._set_headers(200)
            self.wfile.write(json.dumps({
                "llm": llm_config,
                "obsidian_vault_path": obsidian_vault,
                "raw_folder_path": raw_folder,
                "exclude_patterns": exclude_patterns
            }, ensure_ascii=False).encode("utf-8"))
            return

        # 3. API: Получение графа
        if path == "/api/graph":
            tools.load_config()
            wiki_dir = tools.wiki_dir
            nodes = []
            edges = []
            
            if os.path.exists(wiki_dir):
                # Находим все файлы
                file_map = {}
                for root, dirs, files in os.walk(wiki_dir):
                    for f in files:
                        if f.endswith(".md"):
                            filepath = os.path.join(root, f)
                            rel_path = os.path.relpath(filepath, wiki_dir)
                            try:
                                with open(filepath, "r", encoding="utf-8") as file_obj:
                                    content = file_obj.read()
                                meta, body = tools.parse_frontmatter(content)
                                file_id = meta.get("id", f[:-3]).lower()
                                
                                # Исключаем лог и оглавление из графа
                                if file_id in ["log"]:
                                    continue
                                    
                                file_map[file_id] = {
                                    "id": file_id,
                                    "type": meta.get("type", "concept"),
                                    "relations": tools.extract_wikilinks(meta.get("relations", ""))
                                }
                                # Также добавим связи из тела файла
                                file_map[file_id]["relations"].extend(tools.extract_wikilinks(body))
                                file_map[file_id]["relations"] = list(set(file_map[file_id]["relations"]))
                            except Exception as e:
                                logger.error(f"Не удалось распарсить {rel_path} для графа: {e}")
                                
                # Формируем узлы и связи
                for fid, info in file_map.items():
                    nodes.append({
                        "id": fid,
                        "type": info["type"]
                    })
                    for rel in info["relations"]:
                        # Проверяем, что целевой узел существует
                        if rel.lower() in file_map:
                            edges.append({
                                "from": fid,
                                "to": rel.lower()
                            })
                            
            self._set_headers(200)
            self.wfile.write(json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False).encode("utf-8"))
            return

        # 4. API: Детали конкретной заметки
        if path == "/api/note":
            note_id = query_params.get("id", [None])[0]
            if not note_id:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Missing parameter 'id'"}, ensure_ascii=False).encode("utf-8"))
                return
                
            tools.load_config()
            wiki_dir = tools.wiki_dir
            # Ищем файл
            found = False
            note_content = ""
            for root, dirs, files in os.walk(wiki_dir):
                for f in files:
                    if f.endswith(".md"):
                        filepath = os.path.join(root, f)
                        try:
                            with open(filepath, "r", encoding="utf-8") as file_obj:
                                content = file_obj.read()
                            meta, body = tools.parse_frontmatter(content)
                            if meta.get("id", "").lower() == note_id.lower() or f[:-3].lower() == note_id.lower():
                                found = True
                                note_content = {
                                    "success": True,
                                    "id": meta.get("id", f[:-3]),
                                    "type": meta.get("type", "unknown"),
                                    "last_updated": meta.get("last_updated", "-"),
                                    "tags": meta.get("tags", []),
                                    "relations": meta.get("relations", ""),
                                    "body": body.strip()
                                }
                                break
                        except Exception:
                            pass
                if found:
                    break
                    
            if found:
                self._set_headers(200)
                self.wfile.write(json.dumps(note_content, ensure_ascii=False).encode("utf-8"))
            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({"success": false, "error": f"Note '{note_id}' not found"}, ensure_ascii=False).encode("utf-8"))
            return

        # 5. API: Запуск линтера
        if path == "/api/lint":
            linter.load_config()
            report = linter.lint_memory()
            self._set_headers(200)
            self.wfile.write(json.dumps({"report": report}, ensure_ascii=False).encode("utf-8"))
            return

        # 5a. API: Сканирование папки raw (стриминг SSE с прогрессом)
        if path == "/api/scan_raw_stream":
            tools.load_config()
            raw_dir = tools.raw_dir
            wiki_dir = tools.wiki_dir
            exclude = tools.config.get("exclude_patterns", [])

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def sse(event, payload):
                """Отправить SSE-событие клиенту."""
                data = json.dumps(payload, ensure_ascii=False)
                msg = f"event: {event}\ndata: {data}\n\n"
                try:
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass

            ALLOWED_EXT = {".txt", ".md", ".py", ".js", ".sh", ".yaml", ".json",
                           ".docx", ".pdf", ".html", ".css", ".go", ".rs", ".cpp", ".h"}

            new_files = []
            modified_files = []

            if not raw_dir or not os.path.exists(raw_dir):
                sse("error", {"message": "raw_folder_path не настроен или папка не существует"})
                sse("done", {"new_files": [], "modified_files": []})
                return

            # Шаг 1: собираем wiki-файлы для сверки
            sse("status", {"message": "Читаю индекс wiki...", "new": 0, "modified": 0})
            wiki_files = {}
            if os.path.exists(wiki_dir):
                for root, dirs, files in os.walk(wiki_dir):
                    for f in files:
                        if f.endswith(".md"):
                            wiki_files[f[:-3].lower()] = os.path.getmtime(os.path.join(root, f))

            # Шаг 2: сканируем raw folder со стримингом
            dirs_scanned = 0
            for root, dirs, files in os.walk(raw_dir):
                dirs[:] = [d for d in dirs if not should_exclude(os.path.join(root, d), exclude)]
                rel_dir = os.path.relpath(root, raw_dir)
                dirs_scanned += 1

                # Каждые несколько папок шлём обновление статуса
                if dirs_scanned % 3 == 1 or dirs_scanned == 1:
                    display_dir = rel_dir if rel_dir != "." else os.path.basename(raw_dir)
                    sse("progress", {
                        "dir": display_dir,
                        "new": len(new_files),
                        "modified": len(modified_files),
                        "dirs_scanned": dirs_scanned
                    })

                for f in files:
                    filepath = os.path.join(root, f)
                    rel_path = os.path.relpath(filepath, raw_dir)

                    if should_exclude(rel_path, exclude):
                        continue

                    ext = os.path.splitext(f)[1].lower()
                    if ext not in ALLOWED_EXT:
                        continue

                    basename_no_ext = os.path.splitext(f)[0]
                    normalized_id = tools.normalize_concept_name(basename_no_ext)
                    raw_mtime = os.path.getmtime(filepath)

                    if normalized_id not in wiki_files:
                        info = {"rel_path": rel_path, "name": f, "size": os.path.getsize(filepath)}
                        new_files.append(info)
                        sse("found_new", info)
                    elif raw_mtime > wiki_files[normalized_id] + 5:
                        info = {"rel_path": rel_path, "name": f, "size": os.path.getsize(filepath)}
                        modified_files.append(info)
                        sse("found_modified", info)

            sse("done", {"new_files": new_files, "modified_files": modified_files,
                         "dirs_scanned": dirs_scanned})
            return

        # 5a-legacy. API: Сканирование папки raw (обычный JSON, оставлен для совместимости)
        if path == "/api/scan_raw":
            tools.load_config()
            raw_dir = tools.raw_dir
            wiki_dir = tools.wiki_dir
            exclude = tools.config.get("exclude_patterns", [])

            new_files = []
            modified_files = []

            ALLOWED_EXT = {".txt", ".md", ".py", ".js", ".sh", ".yaml", ".json",
                           ".docx", ".pdf", ".html", ".css", ".go", ".rs", ".cpp", ".h"}

            if os.path.exists(raw_dir):
                wiki_files = {}
                for root, dirs, files in os.walk(wiki_dir):
                    for f in files:
                        if f.endswith(".md"):
                            wiki_files[f[:-3].lower()] = os.path.getmtime(os.path.join(root, f))

                for root, dirs, files in os.walk(raw_dir):
                    dirs[:] = [d for d in dirs if not should_exclude(os.path.join(root, d), exclude)]
                    for f in files:
                        filepath = os.path.join(root, f)
                        rel_path = os.path.relpath(filepath, raw_dir)
                        if should_exclude(rel_path, exclude):
                            continue
                        ext = os.path.splitext(f)[1].lower()
                        if ext not in ALLOWED_EXT:
                            continue
                        basename_no_ext = os.path.splitext(f)[0]
                        normalized_id = tools.normalize_concept_name(basename_no_ext)
                        raw_mtime = os.path.getmtime(filepath)
                        if normalized_id not in wiki_files:
                            new_files.append({"rel_path": rel_path, "name": f, "size": os.path.getsize(filepath)})
                        elif raw_mtime > wiki_files[normalized_id] + 5:
                            modified_files.append({"rel_path": rel_path, "name": f, "size": os.path.getsize(filepath)})

            self._set_headers(200)
            self.wfile.write(json.dumps({"new_files": new_files, "modified_files": modified_files}, ensure_ascii=False).encode("utf-8"))
            return

        # 5b. API: Получение списка локальных моделей Ollama
        if path == "/api/ollama_models":
            tools.load_config()
            ollama_url = tools.config.get("llm", {}).get("ollama_url", "http://localhost:11434/api/generate")
            models = get_ollama_models(ollama_url)
            self._set_headers(200)
            self.wfile.write(json.dumps({"models": models}, ensure_ascii=False).encode("utf-8"))
            return

        # 5c. API: Получение статуса скачивания модели Ollama
        if path == "/api/ollama_pull_status":
            with pull_status_lock:
                status_copy = dict(pull_status)
            self._set_headers(200)
            self.wfile.write(json.dumps(status_copy, ensure_ascii=False).encode("utf-8"))
            return

        # 5c. API: Тепловой статус системы (без sudo)
        if path == "/api/thermal":
            import multiprocessing, subprocess as sp, re as _re
            result = {"level": 0, "label": "cool", "cpu_idle": 100.0,
                      "load_per_core": 0.0, "battery_temp_c": None,
                      "sources": [], "pause_ms": 0}
            try:
                cpus = multiprocessing.cpu_count()
                load1, load5, _ = os.getloadavg()
                load_per_core_1m = load1 / max(cpus, 1)
                load_per_core_5m = load5 / max(cpus, 1)

                # ── Источник 1: CPU idle% — два замера, берём ВТОРОЙ (реальный дельта) ──
                # top -l 1 возвращает накопленный % с загрузки ОС — неточно.
                # top -l 2 -s 1: первый кадр = с загрузки, второй = за последнюю секунду.
                cpu_idle = 100.0
                try:
                    top_out = sp.run(
                        ["top", "-l", "2", "-n", "0", "-s", "1"],
                        capture_output=True, text=True, timeout=6
                    ).stdout
                    all_matches = _re.findall(r"(\d+\.\d+)%\s+idle", top_out)
                    if len(all_matches) >= 2:
                        cpu_idle = float(all_matches[-1])   # последний = 2-й кадр
                    elif all_matches:
                        cpu_idle = float(all_matches[0])
                except Exception as e_top:
                    logger.warning(f"top -l 2 ошибка: {e_top}")

                # ── Источник 2: температура батареи через ioreg (без sudo) ──
                # AppleSmartBattery → Temperature (в единицах 0.01°C)
                battery_temp_c = None
                virtual_temp_c = None
                try:
                    ioreg_out = sp.run(
                        ["ioreg", "-r", "-d", "1", "-c", "AppleSmartBattery",
                         "-k", "Temperature", "-k", "VirtualTemperature"],
                        capture_output=True, text=True, timeout=3
                    ).stdout
                    m_temp = _re.search(r'"Temperature"\s*=\s*(\d+)', ioreg_out)
                    m_virt = _re.search(r'"VirtualTemperature"\s*=\s*(\d+)', ioreg_out)
                    if m_temp:
                        battery_temp_c = round(int(m_temp.group(1)) / 100.0, 1)
                    if m_virt:
                        virtual_temp_c = round(int(m_virt.group(1)) / 100.0, 1)
                except Exception as e_ior:
                    logger.warning(f"ioreg температура ошибка: {e_ior}")

                # ── Уровень по каждому источнику, берём максимум ──
                sources = []

                # CPU idle (тиски: >70% cool, 55-70 warm, 35-55 hot, <35 critical)
                if cpu_idle < 35:
                    lvl_cpu, src_cpu = 3, f"CPU idle {cpu_idle:.0f}% (<35%)"
                elif cpu_idle < 55:
                    lvl_cpu, src_cpu = 2, f"CPU idle {cpu_idle:.0f}% (<55%)"
                elif cpu_idle < 70:
                    lvl_cpu, src_cpu = 1, f"CPU idle {cpu_idle:.0f}% (<70%)"
                else:
                    lvl_cpu, src_cpu = 0, f"CPU idle {cpu_idle:.0f}%"
                sources.append(src_cpu)

                # Sustained load (5-min avg): >1.0/core hot, >0.7/core warm
                if load_per_core_5m > 1.0:
                    lvl_load, src_load = 3, f"load5m {load5:.1f} (>{cpus} cores)"
                elif load_per_core_5m > 0.7:
                    lvl_load, src_load = 2, f"load5m {load5:.1f} (>0.7/core)"
                elif load_per_core_5m > 0.45:
                    lvl_load, src_load = 1, f"load5m {load5:.1f} (>0.45/core)"
                else:
                    lvl_load, src_load = 0, f"load5m {load5:.1f}"
                sources.append(src_load)

                # Температура батареи (рядом с CPU, хороший косвенный показатель)
                # MacBook нормальная: <35°C. Тёплая: 35-42°C. Горячая: 42-50°C. Критично: >50°C
                lvl_batt = 0
                if virtual_temp_c is not None:
                    temp_ref = virtual_temp_c  # VirtualTemperature точнее при нагрузке
                elif battery_temp_c is not None:
                    temp_ref = battery_temp_c
                else:
                    temp_ref = None

                if temp_ref is not None:
                    if temp_ref > 50:
                        lvl_batt, src_batt = 3, f"batt {temp_ref:.0f}°C (>50°C 🔥)"
                    elif temp_ref > 42:
                        lvl_batt, src_batt = 2, f"batt {temp_ref:.0f}°C (>42°C)"
                    elif temp_ref > 35:
                        lvl_batt, src_batt = 1, f"batt {temp_ref:.0f}°C (>35°C)"
                    else:
                        lvl_batt, src_batt = 0, f"batt {temp_ref:.0f}°C"
                    sources.append(src_batt)

                # Итоговый уровень = наихудший из всех источников
                level = max(lvl_cpu, lvl_load, lvl_batt)

                labels = {0: "cool", 1: "warm", 2: "hot", 3: "critical"}
                label  = labels[level]
                # Пауза теперь только сигнальная (фронтенд сам ждёт остывания)
                pause_ms_map = {0: 0, 1: 3000, 2: 8000, 3: 20000}

                result = {
                    "level":          level,
                    "label":          label,
                    "cpu_idle":       round(cpu_idle, 1),
                    "load_per_core":  round(load_per_core_1m, 2),
                    "battery_temp_c": temp_ref,
                    "sources":        sources,
                    "pause_ms":       pause_ms_map[level]
                }
            except Exception as e:
                logger.warning(f"Не удалось получить тепловой статус: {e}")

            self._set_headers(200)
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return


        # 6. API: Получение логов импорта
        if path == "/api/logs":
            tools.load_config()
            log_filepath = os.path.join(tools.wiki_dir, "log.md")
            logs = []
            if os.path.exists(log_filepath):
                try:
                    with open(log_filepath, "r", encoding="utf-8") as f:
                        log_content = f.read()
                    _, body = tools.parse_frontmatter(log_content)
                    # Вытаскиваем элементы списка из лога
                    # Формат: - **2026-06-25**: Импортирован...
                    matches = re.findall(r"-\s*\*\*(.*?)\*\*:\s*(.*)", body)
                    for date, msg in matches:
                        logs.append({
                            "date": date,
                            "message": msg
                        })
                except Exception as e:
                    logger.error(f"Не удалось распарсить log.md: {e}")
            self._set_headers(200)
            self.wfile.write(json.dumps(logs, ensure_ascii=False).encode("utf-8"))
            return

        # ── Тихая индексация: статус (GET) ──────────────────────────────────
        if path == "/api/ingest_quiet_status":
            with _quiet_lock:
                snap = dict(_quiet_state)
                snap.pop("queue", None)  # не гоним весь список в ответе
            self._set_headers(200)
            self.wfile.write(json.dumps(snap, ensure_ascii=False).encode("utf-8"))
            return

        # Неизвестный маршрут
        self._set_headers(404, "text/plain")
        self.wfile.write("Not Found".encode("utf-8"))

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        # Читаем тело POST запроса
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")
        
        try:
            payload = json.loads(post_data) if post_data else {}
        except Exception:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}, ensure_ascii=False).encode("utf-8"))
            return

        # 1. API: Запись конфигурации
        if path == "/api/config":
            try:
                backend = payload.get("backend")
                model = payload.get("model")
                
                if not backend or not model:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Missing 'backend' or 'model'"}, ensure_ascii=False).encode("utf-8"))
                    return
                
                # Читаем весь config.yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                
                if "llm" not in config:
                    config["llm"] = {}
                    
                config["llm"]["backend"] = backend
                config["llm"]["model"] = model
                
                if backend == "gemini":
                    config["llm"]["gemini_api_key"] = payload.get("gemini_api_key", "")
                else:
                    config["llm"]["ollama_url"] = payload.get("ollama_url", "http://localhost:11434/api/generate")
                
                # Дополнительные настройки путей и исключений
                if "obsidian_vault_path" in payload:
                    config["obsidian_vault_path"] = payload["obsidian_vault_path"]
                    vault = payload["obsidian_vault_path"]
                    if not vault.endswith("/"):
                        vault += "/"
                    config["wiki_folder_path"] = vault + "wiki/"
                    
                if "raw_folder_path" in payload:
                    config["raw_folder_path"] = payload["raw_folder_path"]
                    
                if "exclude_patterns" in payload:
                    config["exclude_patterns"] = payload["exclude_patterns"]
                
                # Записываем обратно
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
                
                # Перезагружаем конфиг в инстансах инструментов
                tools.load_config()
                linter.load_config()
                
                self._set_headers(200)
                self.wfile.write(json.dumps({"success": True}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        # 1a. API: Запуск скачивания модели Ollama
        if path == "/api/ollama_pull":
            try:
                model_name = payload.get("name")
                if not model_name:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Missing 'name' parameter"}, ensure_ascii=False).encode("utf-8"))
                    return
                    
                tools.load_config()
                ollama_url = tools.config.get("llm", {}).get("ollama_url", "http://localhost:11434/api/generate")
                
                t = threading.Thread(target=pull_model_thread, args=(model_name, ollama_url))
                t.daemon = True
                t.start()
                
                self._set_headers(200)
                self.wfile.write(json.dumps({"success": True}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        # 2. API: Поиск по памяти (query_memory)
        if path == "/api/query":
            query = payload.get("query")
            if not query:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Missing 'query' parameter"}, ensure_ascii=False).encode("utf-8"))
                return
                
            try:
                tools.load_config()
                answer = tools.query_memory(query)
                self._set_headers(200)
                self.wfile.write(json.dumps({"answer": answer}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        # 3. API: Импорт файла (ingest_source)
        if path == "/api/ingest":
            filename = payload.get("filename")
            if not filename:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Missing 'filename' parameter"}, ensure_ascii=False).encode("utf-8"))
                return
                
            try:
                tools.load_config()
                result = tools.ingest_source(filename)
                
                # Если в ответе есть слово "Ошибка", считаем операцию провалившейся
                if result.startswith("Ошибка"):
                    self._set_headers(200)
                    self.wfile.write(json.dumps({"success": False, "error": result}, ensure_ascii=False).encode("utf-8"))
                else:
                    self._set_headers(200)
                    self.wfile.write(json.dumps({"success": True, "result": result}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return


        if path == "/api/ingest_quiet_start":
            files = payload.get("files", [])
            delay = int(payload.get("delay_sec", 30))
            if not files:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "files list is empty"}).encode())
                return
            with _quiet_lock:
                if _quiet_state["running"]:
                    self._set_headers(200)
                    self.wfile.write(json.dumps({"ok": False, "error": "already running"}).encode())
                    return
                _quiet_state.update({
                    "running": True, "stopped": False,
                    "total":   len(files), "done": 0, "errors": 0,
                    "current": "", "queue":  list(files),
                    "log":     [f"🌙 Запуск тихой индексации: {len(files)} файлов, пауза {delay}с"],
                    "eta_sec": len(files) * delay, "delay_sec": delay,
                })
            t = threading.Thread(target=quiet_ingest_worker, daemon=True, name="quiet-ingest")
            t.start()
            self._set_headers(200)
            self.wfile.write(json.dumps({"ok": True, "total": len(files)}).encode())
            return

        # ── Тихая индексация: стоп ─────────────────────────────────────────
        if path == "/api/ingest_quiet_stop":
            with _quiet_lock:
                _quiet_state["stopped"] = True
                _quiet_state["running"] = False
            self._set_headers(200)
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        # Неизвестный маршрут
        self._set_headers(404, "text/plain")
        self.wfile.write("Not Found".encode("utf-8"))


def run_server(port=8000):
    server_address = ("", port)
    httpd = HTTPServer(server_address, MemexDashboardHandler)
    logger.info(f"Сервер панели управления Memex-Wiki успешно запущен на http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("\nОстановка сервера...")
        httpd.server_close()

if __name__ == "__main__":
    port = 8000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    run_server(port)
