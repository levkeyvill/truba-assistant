# Установщик Трубы. Запускается файлом «Установить Трубу.bat» в корне.
#
# Кодировка — UTF-8 с BOM: Windows PowerShell 5.1 без BOM читает файл как
# ANSI (cp1251), и весь русский текст рассыпается. Переводы строк — CRLF.
#
# Всё ставится ВНУТРЬ папки Трубы: Python — в .python, кеш uv — в .cache,
# сам uv — в .tools. Прав администратора не нужно, удалить Трубу = удалить
# папку. Если установка сорвалась — запусти скрипт ещё раз, уже сделанное
# не переделывается.

[CmdletBinding()]
param(
    # Дополнительно поставить тяжёлые голоса (Higgs, ESpeech). Нужна видеокарта
    # NVIDIA. Модель Higgs (~9 ГБ) при этом не качается — она скачается сама,
    # когда голос выберут в пульте.
    [switch]$Voices,
    # Не запускать пульт в конце. Для проверок и для тихой установки.
    [switch]$NoLaunch,
    # Пересоздать .venv с нуля: лечит сломанное окружение.
    [switch]$Repair,
    # Не спрашивать «Установить?»: кнопка в пульте уже спросила сама.
    [switch]$Yes
)

# $PSScriptRoot = <корень>\tools, корень Трубы на уровень выше.
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root 'data'
$log = Join-Path $data 'install.log'
$uv = Join-Path $root '.tools\uv.exe'
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$vbs = Join-Path $root 'Труба.vbs'

# uv не должен писать в профиль пользователя и в общий кеш: иначе после
# удаления папки остаётся хвост, а «удалил папку — удалил Трубу» врёт.
# Старые сборки Windows 10 в PowerShell 5.1 ходят по TLS 1.0, а GitHub и PyPI
# его давно не принимают: «Не удалось создать защищённый канал SSL/TLS».
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# Русский вывод Python и uv идёт через конвейер PowerShell: без этих двух
# строк он декодируется в кодировке консоли (cp866) и в окне, и в журнале
# превращается в кракозябры.
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$env:UV_PYTHON_INSTALL_DIR = Join-Path $root '.python'
$env:UV_CACHE_DIR = Join-Path $root '.cache\uv'

$script:всего = 8
if ($Voices) { $script:всего = 10 }

function Write-Лог {
    param([string]$Текст, [string]$Цвет = 'Gray')
    # Всё, что видит хозяин, остаётся в файле: после ошибки он пришлёт лог.
    Add-Content -LiteralPath $log -Value ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $Текст) -Encoding UTF8
    Write-Host $Текст -ForegroundColor $Цвет
}

function Шаг {
    param([int]$Номер, [string]$Текст)
    Write-Host ''
    Write-Лог ("[{0}/{1}] {2}" -f $Номер, $script:всего, $Текст) 'Cyan'
}

function Отказ {
    param([string]$Сообщение)
    Write-Host ''
    Write-Лог "НЕ ПОЛУЧИЛОСЬ. $Сообщение" 'Red'
    Write-Лог ''
    Write-Лог "Подробности: $log" 'Red'
    Write-Лог 'Что делать: запусти «Установить Трубу» ещё раз — установщик' 'Red'
    Write-Лог 'продолжит с того места, где остановился.' 'Red'
    exit 1
}

function Выполнить {
    # Внешняя программа: весь её вывод — в консоль и в лог, код возврата
    # проверяем руками. $ErrorActionPreference на время вызова снимаем:
    # stderr нативной программы при 'Stop' даёт NativeCommandError, и обычное
    # предупреждение от uv валило бы установку.
    param([string]$Путь, [string[]]$Аргументы, [string]$Что)
    $было = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        Write-Лог ("> {0} {1}" -f $Путь, ($Аргументы -join ' ')) 'DarkGray'
        & $Путь @Аргументы 2>&1 | ForEach-Object { Write-Лог ([string]$_) 'DarkGray' }
        $код = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $было
    }
    if ($код -ne 0) {
        $имя = [IO.Path]::GetFileName($Путь)
        Отказ "${Что}: программа $имя закончилась с кодом $код."
    }
}

function ПортЗанят {
    # Пока пульт работает, порт 8765 держит он. Второй пульт не поднимаем.
    try {
        $клиент = New-Object System.Net.Sockets.TcpClient
        $клиент.Connect('127.0.0.1', 8765)
        $клиент.Close()
        return $true
    }
    catch {
        return $false
    }
}

# Дальше в командах пути относительные (requirements.txt, tools\...): если
# скрипт запустили не из корня (например, правой кнопкой → «Запустить с
# помощью PowerShell»), uv взял бы файлы не оттуда.
Set-Location -LiteralPath $root

Write-Host ''
Write-Host '  Труба — установка' -ForegroundColor Cyan
Write-Host '  -----------------' -ForegroundColor DarkCyan

# --- Путь без русских букв ---------------------------------------------------
#
# Первым делом и раньше всего: «Труба.vbs» читает .venv\pyvenv.cfg как ANSI-текст
# (FSO.OpenTextFile без кодировки). С русскими буквами в пути оттуда берётся
# мусор, pythonw не запускается, и пульт молча не открывается. Лучше отказать
# сразу, чем ставить всё и получить неработающую Трубу.

if ($root -match '[^\x00-\x7F]') {
    Write-Host ''
    Write-Host '  НЕ ПОЛУЧИЛОСЬ: в пути к папке Трубы есть русские буквы.' -ForegroundColor Red
    Write-Host ''
    Write-Host '  Например, C:\Users\Иван\Downloads\Рабочий стол\Труба —' -ForegroundColor Red
    Write-Host '  из такой папки Windows прочитает .venv\pyvenv.cfg криво, и пульт' -ForegroundColor Red
    Write-Host '  не запустится.' -ForegroundColor Red
    Write-Host ''
    Write-Host '  Перенеси папку Трубы туда, где в пути нет русских букв,' -ForegroundColor Red
    Write-Host '  например C:\Truba, и запусти установщик оттуда.' -ForegroundColor Red
    exit 1
}

# --- Спросить, прежде чем ставить --------------------------------------------
#
# Двойной щелчок бывает случайным, а установка качает гигабайты и идёт минут
# двадцать. 29.09 первые люди попросили: «надо предупреждение и "нажми да,
# чтобы установить"». Поэтому сначала говорим, что будет, и ждём «Д».
# Без вопроса — с ключом -Yes (кнопка «Поставить качественные голоса» в
# пульте уже спросила сама) и когда ввода нет вовсе (проверки, тихая
# установка): спрашивать там некого.

if (-not $Yes -and -not [Console]::IsInputRedirected) {
    Write-Host ''
    if ($Voices) {
        Write-Host '  Будут поставлены качественные голоса Higgs и ESpeech (видеокарта NVIDIA):' -ForegroundColor White
        Write-Host '  torch для видеокарты и библиотеки — около 5 ГБ, 15–30 минут. Модель' -ForegroundColor Gray
        Write-Host '  Higgs (~9 ГБ) скачается потом, когда выберешь этот голос в пульте.' -ForegroundColor Gray
    }
    else {
        Write-Host '  Будет установлена Труба — голосовой ассистент.' -ForegroundColor White
        Write-Host '  Python, библиотеки и модели — около 4 ГБ, 15–25 минут, нужен интернет.' -ForegroundColor Gray
    }
    if ($Repair) {
        Write-Host '  Окружение .venv будет пересоздано с нуля (ключ -Repair).' -ForegroundColor Gray
    }
    Write-Host "  Всё ставится в эту папку: $root" -ForegroundColor Gray
    Write-Host '  Прав администратора не нужно; удалить Трубу — удалить эту папку.' -ForegroundColor Gray
    Write-Host ''
    Write-Host '  Установить? Нажми Д — да. Любая другая клавиша — отмена.' -ForegroundColor Yellow
    # Клавиша, а не буква: «Д» в русской раскладке и «L» в английской — одна
    # и та же кнопка. «Y» не берём: в русской раскладке это «Н», то есть «нет».
    $да = $true
    try {
        $да = ([Console]::ReadKey($true).Key -eq [ConsoleKey]::L)
    }
    catch {
        # Клавиатуру не прочитать (не консоль) — спрашивать некого, ставим.
        $да = $true
    }
    if (-not $да) {
        Write-Host ''
        Write-Host '  Отменено — ничего не установлено.' -ForegroundColor Gray
        Start-Sleep -Seconds 2
        exit 0
    }
    Write-Host '  Ставлю.' -ForegroundColor White
}

New-Item -ItemType Directory -Force -Path $data | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root '.tools') | Out-Null
Write-Лог "Установка в $root" 'White'
Write-Лог "Журнал: $log" 'DarkGray'

# Файлы из скачанного ZIP Windows помечает «из интернета» (Zone.Identifier), и
# двойной щелчок по «Труба.vbs» каждый раз спрашивает «Запустить?». Снимаем
# метку с файлов Трубы; окружение и модели не из архива, их не перебираем.
foreach ($что in Get-ChildItem -LiteralPath $root -Force) {
    if ($что.Name -in @('.venv', '.python', '.cache', '.tools', '.git', 'models', 'data')) { continue }
    if ($что.PSIsContainer) {
        Get-ChildItem -LiteralPath $что.FullName -Recurse -File -Force -ErrorAction SilentlyContinue |
            Unblock-File -ErrorAction SilentlyContinue
    }
    else {
        Unblock-File -LiteralPath $что.FullName -ErrorAction SilentlyContinue
    }
}

# --- [1/8] Проверки ----------------------------------------------------------

Шаг 1 'Проверяю компьютер, диск и интернет (несколько секунд)…'

if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
    Отказ 'Труба ставится только на Windows 10 или 11, 64 бита.'
}

$свободно = (New-Object System.IO.DriveInfo((Split-Path -Qualifier $root))).AvailableFreeSpace / 1GB
Write-Лог ("Свободно на диске: {0:N1} ГБ" -f $свободно)
if ($свободно -lt 4) {
    Отказ (("На диске мало места: свободно {0:N1} ГБ, а нужно хотя бы 4 ГБ (библиотеки и модели). " -f $свободно) +
          'Освободи место и запусти установщик ещё раз.')
}

try {
    Invoke-WebRequest -Uri 'https://pypi.org' -Method Head -UseBasicParsing -TimeoutSec 20 -ErrorAction Stop | Out-Null
}
catch {
    Отказ ('Нет связи с интернетом. Труба ставит себя из сети, без неё никак. ' +
          'Проверь подключение и запусти установщик ещё раз.')
}
Write-Лог 'Интернет есть.'

# --- [2/8] uv ----------------------------------------------------------------
#
# uv ставит Python и библиотеки в разы быстрее pip и умеет ставить сборку
# torch для процессора с сервера PyTorch. Свой, внутри папки Трубы.

if (Test-Path $uv) {
    Write-Лог 'uv уже на месте, пропускаю.'
}
else {
    Шаг 2 'Скачиваю uv — маленькую программу для установки (15 МБ, минута)…'
    $архив = Join-Path $env:TEMP 'uv-truba.zip'
    $куда = Join-Path $env:TEMP 'uv-truba'
    try {
        Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/download/0.12.2/uv-x86_64-pc-windows-msvc.zip' `
            -OutFile $архив -UseBasicParsing -TimeoutSec 300 -ErrorAction Stop
        if (Test-Path $куда) { Remove-Item -Recurse -Force $куда }
        Expand-Archive -LiteralPath $архив -DestinationPath $куда -Force
        $найден = Get-ChildItem -Path $куда -Filter 'uv.exe' -Recurse | Select-Object -First 1
        if (-not $найден) { Отказ 'В архиве uv нет uv.exe — архив скачался битым. Запусти установщик ещё раз.' }
        Copy-Item -LiteralPath $найден.FullName -Destination $uv -Force
    }
    finally {
        Remove-Item -Force $архив -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $куда -ErrorAction SilentlyContinue
    }
}


# --- [3/8] Python ------------------------------------------------------------

Шаг 3 'Ставлю Python 3.11 (30 МБ, около минуты)…'
# --no-registry и --no-bin: без них uv прописывает Python в реестр Windows
# (HKCU\Software\Python\Astral) и кладёт python3.11.exe в ~\.local\bin —
# и «удалил папку — удалил Трубу» перестаёт быть правдой (28.09, пробная
# установка перезаписала так запись в реестре на компьютере автора).
Выполнить $uv @('python', 'install', '3.11', '--no-registry', '--no-bin') 'Не поставился Python 3.11'

# --- [4/8] Окружение ---------------------------------------------------------
#
# --seed обязателен: без pip в .venv не работает обновление программы,
# core/updater.py зовёт `python -m pip`.

if ($Repair -and (Test-Path $venv)) {
    Шаг 4 'Пересоздаю окружение .venv заново (ключ -Repair)…'
    Remove-Item -Recurse -Force $venv
}
elseif (Test-Path $python) {
    Шаг 4 'Окружение .venv уже есть, пропускаю.'
}
else {
    Шаг 4 'Создаю окружение .venv (несколько секунд)…'
    Выполнить $uv @('venv', '.venv', '--python', '3.11', '--seed') 'Не создалось окружение .venv'
}

if (-not (Test-Path $python)) {
    Отказ "В папке .venv нет $python — окружение создалось не полностью. Запусти установщик с ключом -Repair."
}

# --- [5/8] torch для процессора ----------------------------------------------
#
# Сборка с сайта PyTorch: обычный torch с PyPI тянет 2.5 ГБ с CUDA, а нужна
# версия для процессора (~250 МБ). Видеокарта Трубе не нужна.

Шаг 5 'Ставлю torch для процессора (250 МБ, 2–4 минуты)…'
Выполнить $uv @('pip', 'install', '--python', $python, 'torch==2.10.0',
    '--index-url', 'https://download.pytorch.org/whl/cpu') 'Не поставился torch для процессора'

# --- [6/8] Библиотеки --------------------------------------------------------

Шаг 6 'Ставлю библиотеки Трубы (около 1 ГБ, 5–10 минут)…'
Write-Лог 'Кажется, что всё зависло — так и есть: качаются и распаковываются пакеты.' 'DarkGray'
Выполнить $uv @('pip', 'install', '--python', $python, '-r', 'requirements.txt',
    '--extra-index-url', 'https://download.pytorch.org/whl/cpu',
    '--index-strategy', 'unsafe-best-match') 'Не поставились библиотеки из requirements.txt'

# --- Шаги только с ключом -Voices ---------------------------------------------
#
# Качественные голоса держат на видеокарте, а не на процессоре, и весят много.
# Ставятся поверх базовой установки и по желанию: обычный голос Silero из
# базовой установки на процессоре звучит вполне живо.

if ($Voices) {
    if (-not (Get-Command 'nvidia-smi.exe' -ErrorAction SilentlyContinue)) {
        Отказ ('Ключ -Voices ставит качественные голоса (Higgs, ESpeech), а они работают ' +
              'только на видеокарте NVIDIA, и драйвера на компьютере нет. ' +
              'Поставь Трубу без этого ключа — голос Silero звучит на процессоре.')
    }
    Write-Лог ('Видеокарта: ' + ((nvidia-smi.exe --query-gpu=name --format=csv,noheader) -join ', '))

    Шаг 7 'Ставлю torch с CUDA для видеокарты NVIDIA (3–4 ГБ, 5–15 минут)…'
    Write-Лог 'Тяжёлые голосы требуют видеокарты, поэтому torch здесь с CUDA.' 'DarkGray'
    Выполнить $uv @('pip', 'install', '--python', $python, 'torch==2.10.0', 'torchaudio==2.10.0',
        '--index-url', 'https://download.pytorch.org/whl/cu130', '--reinstall-package', 'torch') `
        'Не поставился torch с CUDA'

    Шаг 8 'Ставлю библиотеки качественных голосов (1–2 ГБ, 5–15 минут)…'
    Выполнить $uv @('pip', 'install', '--python', $python, '-r', 'requirements-voices.txt',
        '--extra-index-url', 'https://download.pytorch.org/whl/cu130',
        '--index-strategy', 'unsafe-best-match') 'Не поставились библиотеки из requirements-voices.txt'
    Write-Лог 'Модель Higgs (~9 ГБ) не качаю: она скачается сама, когда выберешь этот голос в пульте.' 'DarkGray'
}

# --- Модели заранее ----------------------------------------------------------
#
# prefetch_models.py качает ровно то, что потом просит пульт: модель
# распознавания, VAD, модель отпечатка голоса и голос Silero. Пока идёт
# установка, хозяин видит его вывод, поэтому «висит на моделях» не пугает.

$номер = 7
if ($Voices) { $номер = 9 }
Шаг $номер 'Скачиваю модели: распознавание, VAD, голоса (500 МБ, 3–6 минут)…'
Write-Лог 'Это надолго, и это нормально: идёт обычная загрузка из сети.' 'DarkGray'
Выполнить $python @('tools\prefetch_models.py') 'Не скачались модели (в логе есть, какая именно)'

# --- Ярлыки и первые настройки -----------------------------------------------

$номер = 8
if ($Voices) { $номер = 10 }
Шаг $номер 'Делаю ярлык «Труба» и первые настройки…'

$значок = Join-Path $data 'truba.ico'
$ярлыки = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Труба.lnk'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Труба.lnk')
)
$оболочка = New-Object -ComObject WScript.Shell
foreach ($путь in $ярлыки) {
    try {
        $ярлык = $оболочка.CreateShortcut($путь)
        # Запускаем wscript.exe, а не python: пульт открывается без чёрного
        # окна в консоли — так же, как при двойном щелчке по «Труба.vbs».
        $ярлык.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
        $ярлык.Arguments = '"' + $vbs + '"'
        $ярлык.WorkingDirectory = $root
        $ярлык.Description = 'Труба — голосовой ассистент'
        if (Test-Path $значок) { $ярлык.IconLocation = "$значок,0" }
        $ярлык.Save()
        Write-Лог "Ярлык: $путь"
    }
    catch {
        Write-Лог "Не смог сделать ярлык $путь : $_" 'Yellow'
    }
}

# Пульту нужен .env. Ключ вписывается в мастере первого запуска (или потом:
# Настройки → Ответы и подключение), здесь только заготовка.
$envФайл = Join-Path $root '.env'
if (-not (Test-Path $envФайл)) {
    $образец = Join-Path $root '.env.example'
    if (Test-Path $образец) {
        Copy-Item -LiteralPath $образец -Destination $envФайл
        Write-Лог 'Создал .env из .env.example — ключ впишешь в мастере при первом запуске пульта.'
    }
}

# --- Пульт -------------------------------------------------------------------

Write-Host ''
if ($NoLaunch) {
    Write-Лог 'Готово! Пульт не запускаю (ключ -NoLaunch).' 'Green'
    Write-Host '  Готово! Пульт не запускаю (ключ -NoLaunch).' -ForegroundColor Green
    Write-Host '  Запустить потом можно ярлыком «Труба» или двойным щелчком по «Труба.vbs».' -ForegroundColor Gray
    exit 0
}

if (ПортЗанят) {
    Write-Лог 'Готово! Порт 8765 занят — похоже, пульт уже работает, второй не запускаю.' 'Green'
    Write-Host '  Готово! Порт 8765 занят — похоже, пульт уже работает.' -ForegroundColor Green
    Write-Host '  Второй пульт не запускаю: два пульта не уживаются.' -ForegroundColor Gray
    exit 0
}

Write-Host '  Готово! Труба установлена. Запускаю…' -ForegroundColor Green
Write-Лог 'Готово! Труба установлена. Запускаю пульт.' 'Green'
Start-Process -FilePath (Join-Path $env:WINDIR 'System32\wscript.exe') -ArgumentList ('"' + $vbs + '"') `
    -WorkingDirectory $root
exit 0

