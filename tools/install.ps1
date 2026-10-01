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
    param([int]$Порт)
    # Занят ли порт на этом компьютере. Проверка только на 127.0.0.1: наружу
    # в сеть установщик не ходит.
    try {
        $клиент = New-Object System.Net.Sockets.TcpClient
        $клиент.Connect('127.0.0.1', $Порт)
        $клиент.Close()
        return $true
    }
    catch {
        return $false
    }
}

function Записать-Значение {
    # Файл установки (install_id.txt) читают три разных
    # читателя: Python, PowerShell и «Труба.vbs». VBScript читает его через
    # FSO.OpenTextFile как ANSI, то есть BOM от `Set-Content -Encoding UTF8`
    # в PowerShell 5.1 попадает в значение и ломает сверку отпечатка.
    # Поэтому пишем простой ASCII без BOM: [Text.Encoding]::ASCII не ставит
    # BOM, в отличие от Set-Content.
    param([string]$Файл, [string]$Значение)
    try {
        [IO.File]::WriteAllText($Файл, $Значение, [Text.Encoding]::ASCII)
    }
    catch {
        Отказ "Не получилось записать $Файл : $_"
    }
}

function Прочитать-Значение {
    # Значение из файла установки как простая строка. Старый BOM (файл могли
    # записать с `Set-Content -Encoding UTF8`) срезаем, иначе `int` и
    # сравнение отпечатка получают лишний символ.
    param([string]$Файл)
    if (-not (Test-Path -LiteralPath $Файл)) { return '' }
    $текст = [IO.File]::ReadAllText($Файл).Trim([char]0xFEFF, ' ', "`r", "`n", "`t")
    return $текст
}

function НашПультОтвечает {
    # Отвечает ли на порту пульт ИМЕННО этой копии. Сверяется отпечаток
    # установки (`/api/install`, `core\instance.py`): иначе вторая Труба
    # решила бы, что она уже работает, и не запустилась.
    #
    # Сравнивается ТОЧНОЕ тело ответа при статусе 200, а не вхождение
    # подстроки во всём ответе: иначе достаточно было бы, чтобы чужое
    # приложение на том же порту процитировало наш отпечаток где угодно —
    # в заголовке, в HTML, в поле ошибки.
    param([int]$Порт, [string]$Отпечаток)
    if (-not $Отпечаток) { return $false }
    $клиент = $null
    try {
        $клиент = New-Object System.Net.Sockets.TcpClient
        $клиент.ReceiveTimeout = 1500
        $клиент.SendTimeout = 1500
        $клиент.Connect('127.0.0.1', $Порт)
        $поток = $клиент.GetStream()
        $поток.ReadTimeout = 1500
        $поток.WriteTimeout = 1500
        $запрос = "GET /api/install HTTP/1.1`r`nHost: 127.0.0.1:$Порт`r`nConnection: close`r`n`r`n"
        $байты = [Text.Encoding]::ASCII.GetBytes($запрос)
        $поток.Write($байты, 0, $байты.Length)
        $поток.Flush()
        $буфер = New-Object byte[] 4096
        $ответ = ''
        $прочитано = $поток.Read($буфер, 0, $буфер.Length)
        while ($прочитано -gt 0) {
            $ответ += [Text.Encoding]::UTF8.GetString($буфер, 0, $прочитано)
            $прочитано = $поток.Read($буфер, 0, $буфер.Length)
        }
        # Статус — отдельно от тела: «HTTP/1.1 404» с нашим же отпечатком
        # в каком-нибудь поле не считается ответом пульта.
        $части = $ответ -split "`r`n`r`n", 2
        if ($части.Count -lt 2) { return $false }
        if ($части[0] -notmatch '^HTTP/1\.[01] 200') { return $false }
        return ($части[1].Trim() -ceq $Отпечаток)
    }
    catch {
        return $false
    }
    finally {
        # Сокет закрываем всегда: иначе на повторных проверках (а их тут
        # несколько) Windows держит подключения TIME_WAIT.
        if ($клиент) {
            try { $клиент.Close() } catch { }
        }
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

# --- Порт и отпечаток этой установки ------------------------------------------
#
# Работает только одна Труба на компьютере (решение хозяина от 01.10), и
# порт у всех копий один и привычный — соседних портов больше нет. Поэтому
# установщик проверяет занятость 8765 в начале (чтобы честно сказать) и
# ещё раз перед запуском пульта, а второй пульт не поднимает никогда.

$файлОтпечатка = Join-Path $data 'install_id.txt'
$порт = 8765

if (-not (Test-Path $файлОтпечатка)) {
    # Отпечаток установки: им сверяются две копии на одном компьютере.
    # В журнал он не пишется: это ключ локальной проверки личности, а не
    # диагностика, и лишняя строка в логе только мешает.
    $отпечаток = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
    Записать-Значение $файлОтпечатка $отпечаток
    Write-Лог 'Отпечаток установки записан в install_id.txt.' 'DarkGray'
}
else {
    $отпечаток = Прочитать-Значение $файлОтпечатка
    if (-not $отпечаток) {
        Отказ "Файл install_id.txt пустой или прочитан как мусор ($файлОтпечатка). Удали его и запусти установщик ещё раз."
    }
    # Старый UTF-8 BOM мог остаться от прерванной установки. VBS читает
    # этот файл как ANSI, поэтому нормализуем его в ASCII без BOM.
    Записать-Значение $файлОтпечатка $отпечаток
}

Write-Лог "Порт пульта: $порт (общий для всех копий, как и раньше)" 'DarkGray'
if (НашПультОтвечает $порт $отпечаток) {
    Write-Лог 'Пульт этой копии уже работает — повторный запуск покажет его окно.' 'DarkGray'
}
elseif (ПортЗанят $порт) {
    # Установку не отменяем: папка независима, ярлык нужен. Но второй пульт
    # не поднимаем — хозяин решил, что работает одна Труба.
    Write-Лог "Порт $порт уже занят: пульт сейчас не запущу." 'Yellow'
}

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
$оболочка = New-Object -ComObject WScript.Shell
$имяУстановки = Split-Path -Leaf $root

function Ставим-Ярлык {
    # Один ярлык, перезаписываемый своей копией, и один — когда занято чужой.
    # Чужие ярлыки первой установки не трогаем: иначе её ярлык на рабочем
    # столе вдруг стал бы запускать вторую копию (и наоборот).
    param([string]$Папка, [string]$Имя)
    $путь = Join-Path $Папка $Имя
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

function ЯрлыкЧужой {
    # Настоящий ли ярлык «Труба» в этой папке — или он принадлежит другой
    # установке? Смотрим, куда он запускает.
    param([string]$Папка, [string]$Имя)
    $путь = Join-Path $Папка $Имя
    if (-not (Test-Path $путь)) { return $false }
    try {
        $чужой = $оболочка.CreateShortcut($путь)
        return ($чужой.Arguments -notlike "*$vbs*")
    }
    catch {
        return $false
    }
}

$папкиЯрлыков = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
)
foreach ($папка in $папкиЯрлыков) {
    if ([string]::IsNullOrWhiteSpace($папка)) { continue }
    if (-not (Test-Path $папка)) { continue }
    if (ЯрлыкЧужой $папка 'Труба.lnk') {
        # Место «Труба» занято другой установкой. Свой ярлык кладём рядом под
        # именем папки и говорим об этом — молча переписать чужой нельзя.
        Ставим-Ярлык $папка ("Труба ($имяУстановки).lnk")
        Write-Лог "Ярлык «Труба» в папке $папка принадлежит другой установке — не трогаю его." 'Yellow'
    }
    else {
        Ставим-Ярлык $папка 'Труба.lnk'
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

# --- Проверка окружения перед «готово» ---------------------------------------
#
# «Установить Трубу» закрылся успешно, а пульт при двойном щелчке отвечает
# «Папка .venv повреждена или отсутствует» — так установка выглядит
# законченной, пока на самом деле не проверена. Проверяем по-настоящему и
# ДО обеих веток успеха (с -NoLaunch тоже): иначе «Готово! Пульт не запускаю»
# объявлялось при неработающем .venv.
#
# Проверяем ровно то, на что смотрит «Труба.vbs»: pyvenv.cfg, папку
# Lib\site-packages, базовый Python из pyvenv.cfg (его читает сам VBS) и
# настоящий импорт библиотек пульта. Причина отказа пишется в журнал, а не
# только «дальше установщик продолжит».

function Проверить-Окружение {
    $cfgФайл = Join-Path $venv 'pyvenv.cfg'
    $sitePackages = Join-Path $venv 'Lib\site-packages'

    if (-not (Test-Path $python)) {
        Write-Лог "НЕТ ФАЙЛА: $python" 'Red'
        Отказ ("В папке .venv нет $python — окружение создалось не полностью. " +
               'Запусти установщик с ключом -Repair.')
    }
    if (-not (Test-Path -LiteralPath $cfgФайл)) {
        Write-Лог "НЕТ ФАЙЛА: $cfgФайл — без него «Труба.vbs» не найдёт Python." 'Red'
        Отказ "Нет файла .venv\pyvenv.cfg. Запусти установщик с ключом -Repair."
    }
    if (-not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
        Write-Лог "НЕТ ПАПКИ: $sitePackages" 'Red'
        Отказ "Нет папки .venv\Lib\site-packages — окружение неполное. Запусти установщик с ключом -Repair."
    }

    # Базовый Python из pyvenv.cfg — ровно тот, который ищет VBS при старте.
    $home = ''
    foreach ($строка in [IO.File]::ReadAllLines($cfgФайл)) {
        if ($строка -match '^\s*home\s*=\s*(.+?)\s*$') { $home = $Matches[1] }
    }
    if (-not $home) {
        Write-Лог "В $cfgФайл нет строки home — VBS не найдёт базовый Python." 'Red'
        Отказ "В .venv\pyvenv.cfg нет строки home. Запусти установщик с ключом -Repair."
    }
    $pythonw = Join-Path $home 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) {
        Write-Лог "НЕТ ФАЙЛА: $pythonw (home из pyvenv.cfg)" 'Red'
        Отказ ("Базовый Python из pyvenv.cfg не найден: $pythonw. " +
               'Папку .python могли удалить или перенести. Запусти установщик с ключом -Repair.')
    }

    # Настоящий импорт: файлы на месте — ещё не значит, что пакеты рабочие.
    $проба = & $python -c "import fastapi, uvicorn, numpy, soundfile, webview" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Лог "ИМПОРТ НЕ ПРОШЁЛ: $python -c 'import fastapi, uvicorn, numpy, soundfile, webview'" 'Red'
        Write-Лог "Вывод Python: $проба" 'Red'
        Отказ ("Проверка окружения не прошла: $python не смог импортировать нужные библиотеки. " +
               'Значит, установка остановилась на пакетах, и пульт не запустится. ' +
               "Что именно не так — в журнале: $log")
    }
    Write-Лог 'Окружение проверено: pyvenv.cfg, site-packages, базовый Python и библиотеки на месте.' 'DarkGray'
}

Проверить-Окружение

# --- Пульт -------------------------------------------------------------------

Write-Host ''
if ($NoLaunch) {
    Write-Лог 'Готово! Пульт не запускаю (ключ -NoLaunch).' 'Green'
    Write-Host '  Готово! Пульт не запускаю (ключ -NoLaunch).' -ForegroundColor Green
    Write-Host '  Запустить потом можно ярлыком «Труба» или двойным щелчком по «Труба.vbs».' -ForegroundColor Gray
    exit 0
}

# Второй пульт не поднимаем: пока работает какая-то Труба, пульт этой
# копии не запустится (общий mutex в ui\window.py), и хозяину нужен
# понятный текст, а не тишина и не чужое окно поверх.
if (НашПультОтвечает $порт $отпечаток) {
    Write-Host '  Готово! Пульт этой копии уже работает — показываю его окно.' -ForegroundColor Green
    Write-Лог 'Готово! Пульт этой копии уже работает — показываю его окно.' 'Green'
}
elseif (ПортЗанят $порт) {
    Write-Host ''
    Write-Host '  Готово! Труба установлена.' -ForegroundColor Green
    Write-Host '  Но пульт сейчас не запущен: порт 8765 уже занят кем-то другим.' -ForegroundColor Yellow
    Write-Host '  Это может быть Труба из другой папки (тогда закрой её и запусти' -ForegroundColor Yellow
    Write-Host '  этот ярлык) или посторонняя программа (её нужно закрыть или' -ForegroundColor Yellow
    Write-Host '  освободить порт). Второй пульт не запускаю.' -ForegroundColor Yellow
    Write-Лог 'Готово! Труба установлена. Порт 8765 занят кем-то другим (Труба из другой папки или посторонняя программа) — пульт не запускаю. Освободи порт и запусти этот ярлык.' 'Yellow'
    exit 0
}
else {
    Write-Host '  Готово! Труба установлена. Запускаю…' -ForegroundColor Green
    Write-Лог "Готово! Труба установлена. Запускаю пульт на порту $порт." 'Green'
}

Start-Process -FilePath (Join-Path $env:WINDIR 'System32\wscript.exe') -ArgumentList ('"' + $vbs + '"') `
    -WorkingDirectory $root
exit 0

