"""Установщик Трубы: «Установить Трубу.bat», tools/install.ps1, prefetch.

Текстовые проверки и одна локальная проверка функции PowerShell. Ничего не
ставится и не скачивается: установщик качает гигабайты, а тесты должны
проходить быстро и без сети.

Что тут ловится по делу. `Установить Трубу.bat` — только ASCII и CRLF:
кириллица в .bat ломается кодировкой cmd, а одиночные LF ломают весь файл.
`install.ps1` — UTF-8 с BOM: Windows PowerShell 5.1 без BOM читает файл как
cp1251 и рассыпает русский текст. `--seed` у `uv venv` обязателен: без pip в
.venv не работает обновление программы (core/updater.py зовёт `python -m pip`).
Версии в requirements закреплены `==`, иначе однажды придёт несовместимая
библиотека. Тяжёлое в prefetch_models.py — внутри функций, иначе файл нельзя
ни разобрать, ни импортировать без сети.
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import config

BAT = Path(config.ROOT) / "Установить Трубу.bat"
INSTALL_PS1 = Path(config.ROOT) / "tools" / "install.ps1"
PREFETCH = Path(config.ROOT) / "tools" / "prefetch_models.py"
README = Path(config.ROOT) / "README.md"
REQUIREMENTS = Path(config.ROOT) / "requirements.txt"
REQUIREMENTS_VOICES = Path(config.ROOT) / "requirements-voices.txt"


def _одиночные_lf(байты: bytes) -> list[int]:
    """Номера байт, где LF не идёт за CR — то есть строки без CRLF."""
    return [i for i, b in enumerate(байты) if b == 10 and (i == 0 or байты[i - 1] != 13)]


class NoSystemTraceTests(unittest.TestCase):
    """Python — только в папке Трубы: ни реестра, ни ~/.local/bin."""

    def test_uv_python_install_leaves_no_trace(self):
        текст = INSTALL_PS1.read_bytes()[3:].decode("utf-8")
        строка = [s for s in текст.splitlines() if "'python', 'install'" in s]
        self.assertEqual(len(строка), 1)
        self.assertIn("--no-registry", строка[0])
        self.assertIn("--no-bin", строка[0])


class VoicesBatTests(unittest.TestCase):
    """Второй .bat — для качественных голосов: ключ человеку без командной
    строки не передать, поэтому отдельный файл с тем же установщиком."""

    def test_ascii_crlf_and_the_voices_key(self):
        путь = Path(config.ROOT) / "Установить качественные голоса.bat"
        сырьё = путь.read_bytes()
        сырьё.decode("ascii")
        self.assertIn(b"\r\n", сырьё)
        self.assertIn(b"tools\\install.ps1\" -Voices", сырьё)


class BatTests(unittest.TestCase):
    """Обёртка для обычного человека: только ASCII, CRLF и вызов ps1."""

    @classmethod
    def setUpClass(cls):
        cls.байты = BAT.read_bytes()
        cls.текст = cls.байты.decode("ascii")

    def test_the_file_is_ascii_only(self):
        # Кириллица в .bat ломает кодировку cmd: сообщения выходят
        # квадратами. Имя файла с кириллицей cmd открывает нормально —
        # поэтому имя и оставляем, а содержимое нет.
        непонятные = [b for b in self.байты if b > 127]
        self.assertEqual(непонятные, [], f"в .bat есть не-ASCII: {sorted(set(непонятные))[:10]}")
        self.assertIn(b"@echo off", self.байты)
        self.assertIn(b'cd /d "%~dp0"', self.байты)

    def test_every_line_ends_with_crlf(self):
        self.assertEqual(_одиночные_lf(self.байты), [], "в .bat есть строки без CRLF")

    def test_it_starts_powershell_and_pauses_after_a_failure(self):
        # -ExecutionPolicy Bypass: иначе на машине с ограниченной политикой
        # скрипт просто не запустится.
        self.assertIn("-NoProfile", self.текст)
        self.assertIn("-ExecutionPolicy Bypass", self.текст)
        self.assertIn("tools\\install.ps1", self.текст)
        # Аргументы пользователя (-Voices, -Repair) летят в скрипт как есть.
        self.assertIn("%*", self.текст)
        # Код возврата PowerShell проверяется, и при ошибке окно не исчезает
        # молча.
        self.assertIn("if errorlevel 1", self.текст)
        self.assertIn("pause", self.текст)
        self.assertIn("install.log", self.текст)


class InstallPs1Tests(unittest.TestCase):
    """Скрипт установки: кодировка, шаги, ключи и то, куда всё ставится."""

    @classmethod
    def setUpClass(cls):
        cls.байты = INSTALL_PS1.read_bytes()
        cls.текст = cls.байты.decode("utf-8-sig")

    def test_the_file_starts_with_a_bom(self):
        # Windows PowerShell 5.1 без BOM читает .ps1 как ANSI (cp1251) —
        # русский текст в сообщениях рассыпается на месте.
        self.assertEqual(self.байты[:3], b"\xef\xbb\xbf",
                         "install.ps1 без BOM: PowerShell 5.1 прочитает его как cp1251")

    def test_crlf_line_endings(self):
        self.assertEqual(_одиночные_lf(self.байты), [],
                         "в install.ps1 есть строки без CRLF")

    def test_everything_goes_inside_the_truba_folder(self):
        # Прав администратора нет, и удаление = удаление папки: свой uv, свой
        # Python, свой кеш. Иначе после удаления папки в профиле пользователя
        # остаётся хвост.
        self.assertIn("$env:UV_PYTHON_INSTALL_DIR", self.текст)
        self.assertIn("$env:UV_CACHE_DIR", self.текст)
        self.assertIn(".python", self.текст)
        self.assertIn(".tools", self.текст)
        for запрещённое in ("Program Files", "pip install --user"):
            self.assertNotIn(запрещённое, self.текст)

    def test_python_and_the_environment_are_created_with_uv(self):
        self.assertIn("'python', 'install', '3.11'", self.текст)
        self.assertIn("'venv', '.venv', '--python', '3.11', '--seed'", self.текст)
        self.assertIn("https://github.com/astral-sh/uv/releases/download/0.12.2/uv-x86_64-pc-windows-msvc.zip",
                      self.текст)

    @unittest.skipUnless(os.name == "nt", "Проверка Windows PowerShell")
    def test_environment_check_reads_cfg_home_instead_of_powershell_home(self):
        # В 0.9.7 `$home` совпал с неизменяемым `$HOME` PowerShell. Из-за этого
        # установленный Python был объявлен отсутствующим в самом конце.
        base = Path(getattr(sys, "_base_executable", sys.executable)).parent
        if not (base / "pythonw.exe").is_file():
            self.skipTest("базового pythonw.exe нет в среде проверки")
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / ".venv"
            (venv / "Lib" / "site-packages").mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text(f"home = {base}\n", encoding="utf-8")
            ps = r'''
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TRUBA_TEST_INSTALLER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Ошибка разбора install.ps1' }
$fn = $ast.FindAll({param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Проверить-Окружение'}, $true) | Select-Object -First 1
if (-not $fn) { throw 'Нет функции проверки окружения' }
Invoke-Expression $fn.Extent.Text
$venv = $env:TRUBA_TEST_VENV
$python = $env:TRUBA_TEST_PYTHON
$log = ''
function Write-Лог { param($Текст, $Цвет); Write-Output $Текст }
function Отказ { param($Сообщение); throw $Сообщение }
Проверить-Окружение
'''
            env = os.environ.copy()
            env.update(TRUBA_TEST_INSTALLER=str(INSTALL_PS1),
                       TRUBA_TEST_VENV=str(venv), TRUBA_TEST_PYTHON=sys.executable)
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps], env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20)
            self.assertEqual(result.returncode, 0,
                             (result.stdout + result.stderr)[-1000:])

    def test_torch_comes_from_the_pytorch_index_for_the_cpu(self):
        # torch с PyPI тянет 2.5 ГБ с CUDA; базовой установке нужна сборка для
        # процессора. Версия совпадает с requirements.txt, иначе uv переставит
        # torch и снесёт всё, что на него смотрит.
        self.assertIn("'torch==2.10.0'", self.текст)
        self.assertIn("--index-url', 'https://download.pytorch.org/whl/cpu'", self.текст)
        self.assertIn("--extra-index-url', 'https://download.pytorch.org/whl/cpu'", self.текст)
        # unsafe-best-match: иначе общий индекс отдаст torch с CUDA.
        self.assertIn("--index-strategy', 'unsafe-best-match'", self.текст)
        self.assertIn("'--index-url', 'https://download.pytorch.org/whl/cu130'", self.текст)
        self.assertIn("requirements-voices.txt", self.текст)

    def test_the_steps_are_numbered_and_logged(self):
        # Хозяин должен видеть, что происходит и сколько ждать, а после
        # ошибки — иметь лог, который можно прислать.
        for номер in range(1, 9):
            self.assertRegex(self.текст, rf"Шаг {номер} ")
        self.assertIn("install.log", self.текст)
        self.assertIn("Add-Content", self.текст)
        # Отказ всегда красным, с журналом и ненулевым кодом.
        self.assertIn("function Отказ", self.текст)
        self.assertIn("'Red'", self.текст)
        self.assertIn("exit 1", self.текст)
        # Проверки до установки: разрядность, место, сеть.
        self.assertIn("Is64BitOperatingSystem", self.текст)
        self.assertIn("DriveInfo", self.текст)
        self.assertIn("pypi.org", self.текст)


    def test_the_steps_run_in_order_and_nothing_hangs_after_the_exit(self):
        # Шаги должны идти по порядку и до последнего exit: блок, уехавший
        # за exit 0, синтаксически не виден, а по шаблону «Шаг N» проверяется
        # — так терялась установка библиотек из requirements.txt.
        позиции = [self.текст.index(f"Шаг {номер} ") for номер in range(1, 9)]
        self.assertEqual(позиции, sorted(позиции), "шаги идут не по порядку")
        хвост = self.текст.rsplit("exit 0", 1)[1]
        self.assertEqual(хвост.strip(), "",
                         "после последнего exit 0 есть код — он не выполнится никогда")

    def test_the_key_switches_are_there(self):
        for ключ in ("-Voices", "-NoLaunch", "-Repair"):
            self.assertIn(ключ, self.текст)
        # -Voices только на NVIDIA: без видеокарты ставить нечего и тянуть
        # нечего, поэтому отказ понятный, а не «сломалось».
        self.assertIn("nvidia-smi", self.текст)
        self.assertIn("видеокарт", self.текст.lower())
        # Higgs (~9 ГБ) качается только при выборе голоса в пульте.
        self.assertRegex(self.текст, r"Higgs \(~9 ГБ\) не качаю")

    def test_the_cyrillic_path_is_refused_before_anything_is_installed(self):
        # «Труба.vbs» читает .venv\pyvenv.cfg как ANSI-текст, и кириллица в
        # пути ломает запуск пульта. Отказ первым, до uv и Python.
        начало = self.текст.index("НЕ ПОЛУЧИЛОСЬ: в пути")
        self.assertIn("C:\\Truba", self.текст)
        self.assertLess(начало, self.текст.index("Шаг 1 "))
        self.assertIn("exit 1", self.текст[начало:начало + 900])

    def test_it_asks_before_installing(self):
        # 29.09, первые отзывы: «установочник сразу начинает установку. надо
        # предупреждение и "нажми да чтобы установить"». Вопрос — после отказа
        # по русским буквам в пути и раньше всего, что пишет на диск.
        вопрос = self.текст.index("Установить? Нажми Д")
        self.assertLess(self.текст.index("НЕ ПОЛУЧИЛОСЬ: в пути"), вопрос)
        self.assertLess(вопрос, self.текст.index("New-Item -ItemType Directory"))
        self.assertLess(вопрос, self.текст.index("Unblock-File"))
        self.assertLess(вопрос, self.текст.index("Шаг 1 "))
        # «Д» — клавиша L в любой раскладке; «Y» в русской — это «Н», «нет».
        self.assertIn("[ConsoleKey]::L", self.текст)
        self.assertNotIn("[ConsoleKey]::Y", self.текст)
        # Отмена ничего не ставит и не пугает окном «Installation failed».
        отмена = self.текст.index("Отменено — ничего не установлено")
        self.assertIn("exit 0", self.текст[отмена:отмена + 200])
        # Без вопроса — только ключом -Yes и без клавиатуры (проверки).
        self.assertIn("[switch]$Yes", self.текст)
        self.assertIn("[Console]::IsInputRedirected", self.текст)

    def test_shortcuts_first_settings_and_the_launch(self):
        self.assertIn("CreateShortcut", self.текст)
        self.assertIn("'Труба.lnk'", self.текст)
        self.assertIn("Start Menu", self.текст)
        self.assertIn("wscript.exe", self.текст)
        self.assertIn("Труба.vbs", self.текст)
        self.assertIn("truba.ico", self.текст)
        self.assertIn(".env.example", self.текст)
        # Порт занят — второй пульт не поднимаем.
        self.assertIn("8765", self.текст)
        self.assertIn("Готово! Труба установлена. Запускаю", self.текст)

    def test_the_prefetch_is_run_by_the_same_python(self):
        # Модели качает тем же интерпретатором, что и пульт: у него свой
        # site-packages с onnx_asr и silero.
        self.assertIn("prefetch_models.py", self.текст)
        self.assertIn("Выполнить $python @('tools\\prefetch_models.py')", self.текст)

    def test_powershell_can_parse_the_script(self):
        # Тот же парсер, которым Windows читает .ps1. Ничего не выполняем.
        кусок = (
            "$e=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{INSTALL_PS1}',[ref]$null,[ref]$e) | Out-Null; "
            "if ($e) { $e | ForEach-Object { $_.ToString() }; exit 1 }"
        )
        try:
            разбор = subprocess.run(["powershell", "-NoProfile", "-Command", кусок],
                                    capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"PowerShell недоступен: {exc}")
        self.assertEqual(разбор.returncode, 0,
                         f"парсер PowerShell: {разбор.stdout}{разбор.stderr}")



class PrefetchTests(unittest.TestCase):
    """prefetch_models.py должен разбираться и импортироваться без сети."""

    @classmethod
    def setUpClass(cls):
        cls.исходник = PREFETCH.read_text(encoding="utf-8")
        cls.дерево = ast.parse(cls.исходник)

    def test_it_parses(self):
        self.assertIsInstance(self.дерево, ast.Module)

    def test_heavy_imports_live_inside_functions(self):
        # Всё, что качает из сети и весит сотни мегабайт, импортируется
        # внутри функций: иначе файл нельзя ни разобрать тестом, ни
        # импортировать без библиотек.
        тяжёлое = {"onnx_asr", "torch", "silero", "PIL", "numpy", "core",
                   "huggingface_hub"}
        верх = [узел for узел in self.дерево.body
                if isinstance(узел, (ast.Import, ast.ImportFrom))]
        self.assertTrue(верх, "в файле нет ни одного импорта")
        for узел in верх:
            имена = ([alias.name for alias in узел.names] if isinstance(узел, ast.Import)
                     else [узел.module or ""])
            for имя in имена:
                self.assertNotIn(имя.split(".")[0], тяжёлое,
                                 f"{имя} импортируется на верхнем уровне: модуль "
                                 "упадёт без библиотек и без сети")
        # config наверху можно: он ничего не качает, только ставит HF_HOME.
        self.assertTrue(any(isinstance(узел, (ast.Import, ast.ImportFrom))
                            and "config" in [alias.name for alias in узел.names]
                            for узел in верх),
                        "config должен импортироваться наверху — он ставит HF_HOME")

    def test_it_does_not_fail_on_import_without_the_network(self):
        # Сам импорт: config ставит HF_HOME, но ничего не качает.
        import importlib.util

        об = importlib.util.spec_from_file_location("truba_prefetch", PREFETCH)
        модуль = importlib.util.module_from_spec(об)
        try:
            об.loader.exec_module(модуль)
        except Exception as exc:  # noqa: BLE001 — тест сам на это и смотрит
            self.fail(f"import prefetch_models упал без сети: {type(exc).__name__}: {exc}")
        self.assertTrue(callable(модуль.main))

    def test_every_model_is_wrapped_and_reported(self):
        # Каждая модель в своём try, иначе упавшая одна отменяет остальные.
        self.assertIn("try:", self.исходник)
        self.assertIn("except Exception", self.исходник)
        self.assertIn("ЗАДАЧИ", self.исходник)
        # Код возврата 1, если хоть одна не скачалась: установщик на это ругается.
        self.assertTrue(any(isinstance(узел, ast.FunctionDef) and узел.name == "main"
                            for узел in self.дерево.body), "нужна функция main")
        self.assertIn("return 1", self.исходник)
        # Подписи с размерами и «готово» — чтобы «висит» не пугало.
        self.assertRegex(self.исходник, r"Скачиваю распознавание речи \(~225 МБ\)…")
        self.assertIn("...готово", self.исходник)
        # Модели те же, что просит пульт.
        for кусок in ("config.STT_MODEL", "config.VAD_MODEL", "Voiceprint",
                      "SileroVoice", "load_vad", "load_model"):
            self.assertIn(кусок, self.исходник)
        # Значок ярлыков заново не рисуется, если он уже есть. Рисунок общий
        # с окном пульта — в core/app_icon.py.
        self.assertIn("ICO_PATH.exists()", self.исходник)
        self.assertIn("app_icon.draw(ICO_PATH)", self.исходник)
        значок = (Path(config.ROOT) / "core" / "app_icon.py").read_text(encoding="utf-8")
        self.assertIn('format="ICO"', значок)
        for размер in (256, 64, 48, 32, 16):
            self.assertIn(str(размер), значок)
        # Цвета — янтарное кольцо на тёмном круге, как в шапке пульта.
        self.assertIn("232, 145, 58", значок)
        self.assertIn("20, 22, 28", значок)


class RequirementsTests(unittest.TestCase):
    """Версии закреплены: иначе придёт несовместимая библиотека."""

    def _строки(self, путь: Path) -> list[str]:
        строки = []
        for строка in путь.read_text(encoding="utf-8").splitlines():
            строка = строка.strip()
            if строка and not строка.startswith("#"):
                строки.append(строка)
        return строки

    def test_base_requirements_are_pinned(self):
        строки = self._строки(REQUIREMENTS)
        self.assertTrue(строки)
        for строка in строки:
            self.assertIn("==", строка, f"не закреплена версия: {строка}")
            # torch нужен ровно той же версии, что ставит установщик: иначе
            # uv переставит его поверх.
            if строка.startswith("torch"):
                self.assertEqual(строка, "torch==2.10.0")

    def test_voice_requirements_are_pinned(self):
        строки = self._строки(REQUIREMENTS_VOICES)
        self.assertTrue(строки)
        for строка in строки:
            self.assertIn("==", строка, f"не закреплена версия: {строка}")



class ReadmeTests(unittest.TestCase):
    """README — для человека, который раньше Трубу не видел."""

    @classmethod
    def setUpClass(cls):
        cls.текст = README.read_text(encoding="utf-8")

    def test_it_points_at_the_releases(self):
        # Ссылка нужна одна и та же, что в config.UPDATE_REPO: иначе хозяин
        # скачает не тот архив, а обновление будет искать другой.
        self.assertIn(config.UPDATE_REPO, self.текст)
        self.assertRegex(self.текст, r"levkeyvill/truba-assistant/releases")

    def test_requirements_have_a_websocket_library(self):
        # 28.09 установка с нуля: uvicorn без websockets/wsproto не умеет
        # WebSocket, и телефон не подключался вовсе. У автора библиотека
        # стояла случайно, как чужая зависимость.
        требования = (Path(config.ROOT) / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(требования, r"(?m)^(websockets|wsproto|uvicorn\[standard\])")

    def test_it_answers_the_obvious_questions(self):
        for кусок in ("Установить Трубу", "Windows 10", "икрофон", "OpenAI",
                      "DeepSeek", "OpenRouter", "Ollama", "Установить качественные голоса",
                      # «О программе» с 28.09 — свой пункт меню, не «Настройки».
                      "install.log", "C:\\Truba", "«О программе» → «Проверить обновления»",
                      "Настройки → Телефон", "t.me/levkeyvill",
                      "youtube.com/@levkeyvill", "dalink.to/levkeyvill",
                      "Лев Кейвилл", "Удаление", "Обновление", "видеокарт",
                      "мастер первой настройки", "Что уходит в интернет",
                      "THIRD_PARTY.md", "некоммерческого"):
            self.assertIn(кусок, self.текст, f"в README нет: {кусок}")

    def test_it_warns_about_the_cyrillic_path(self):
        # Кириллица в пути ломает пульт (pyvenv.cfg читается как ANSI) и
        # молча — поэтому предупреждение обязано быть видно сразу, а не
        # прятаться в сноску про ANSI.
        self.assertRegex(self.текст.lower(), r"русск\w*\s+букв",
                         "в README нет предупреждения о русских буквах в пути")
        self.assertIn("C:\\Truba", self.текст)

    def test_it_has_no_emoji_soup(self):
        # README читают без рук на клавиатуре, с телефона, в полутьме.
        подозрительные = [символ for символ in self.текст
                          if ord(символ) > 0x2100 and символ not in "→…«»—"]
        self.assertEqual(подозрительные, [],
                         f"в README эмодзи или символы вне текста: {подозрительные[:10]}")


if __name__ == "__main__":
    unittest.main()

