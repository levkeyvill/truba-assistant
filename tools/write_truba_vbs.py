"""Пересобирает «Труба.vbs» в UTF-16LE с BOM и CRLF.

Сам файл держим в репозитории как есть, а текст правим здесь: VBScript
читает только UTF-16 с BOM, и любой редактор, сохранивший его в UTF-8,
ломает запуск пульта. Запуск: python tools/write_truba_vbs.py
"""

from pathlib import Path

VBS = r'''Option Explicit

' Единственная кнопка запуска Трубы. Двойной щелчок — открыть или показать
' пульт. С ключом /tray (ярлык автозагрузки) пульт стартует в трее.
'
' Работать может только одна Труба на компьютере (решение хозяина от
' 01.10). Здесь это решается быстро и без 30-секундного ожидания:
'   — отвечает на 8765 пульт ИМЕННО этой копии (сверили отпечаток из
'     data\install_id.txt) → это повторный щелчок, окно покажет сам
'     Python (ui\window.py поднимает своё окно по заголовку);
'   — на порту кто-то есть, но это не наш отпечаток → сразу сообщение
'     «уже работает из другой папки» и выход. Чужое окно не поднимаем;
'   — порт свободен → запускаем Python.
' Второй пульт всё равно не поднимется: общий mutex держит ui\window.py.
'
' ВАЖНО: файл должен быть UTF-16 с BOM — так его читает VBScript.
' Правь текст здесь и запускай: python tools\write_truba_vbs.py
'
' Порт у всех копий один и привычный — тот же, что и раньше.
Const PORT_DEFAULT = 8765

Dim shell, fso, folder, venv, cfg, cfgFile, line, home, pythonw
Dim sitePackages, command, arg, trayStart, waitSteps, ready, attempt
Dim myPort, myId, dataDir, state
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
venv = fso.BuildPath(folder, ".venv")
cfg = fso.BuildPath(venv, "pyvenv.cfg")
sitePackages = fso.BuildPath(venv, "Lib\site-packages")
dataDir = fso.BuildPath(folder, "data")

trayStart = False
For Each arg In WScript.Arguments
  If LCase(arg) = "/tray" Then trayStart = True
Next

' Прочитать файл установки. Нет файла — пустая строка, а не ошибка:
' у свежей копии их ещё нет, и это обычное состояние.
Function ReadTextFile(filePath)
  Dim f
  If Not fso.FileExists(filePath) Then
    ReadTextFile = ""
  Else
    Set f = fso.OpenTextFile(filePath, 1)
    ' Установщик записывает простое ASCII без BOM.
    ReadTextFile = Trim(f.ReadAll)
    f.Close
  End If
End Function

' Порт у всех копий общий и привычный; сверять надо только отпечаток.
Sub ReadInstance()
  myPort = PORT_DEFAULT
  ' Отпечаток этой установки. Пусто — копия ещё ни разу не запускалась.
  myId = ReadTextFile(fso.BuildPath(dataDir, "install_id.txt"))
End Sub

ReadInstance

' Кто отвечает на общем порту: «свой», «чужая» или «никто».
' Отвечает сам пульт этой копии — /api/install есть только у новых версий;
' у старой 0.9.6 его нет, и она видна по /api/runtime (он локальный).
Function КтоНаПорте()
  Dim request
  КтоНаПорте = "никто"
  On Error Resume Next
  Set request = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  request.setTimeouts 500, 500, 500, 500
  request.open "GET", "http://127.0.0.1:" & myPort & "/api/install", False
  request.send
  If Err.Number = 0 Then
    If request.status = 200 Then
      If Len(myId) > 0 And Trim(request.responseText) = myId Then
        КтоНаПорте = "свой"
      Else
        КтоНаПорте = "чужая"
      End If
    End If
  End If
  Err.Clear
  If КтоНаПорте = "никто" Then
    Set request = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    request.setTimeouts 500, 500, 500, 500
    request.open "GET", "http://127.0.0.1:" & myPort & "/api/runtime", False
    request.send
    If Err.Number = 0 And request.status = 200 Then КтоНаПорте = "чужая"
    Err.Clear
  End If
  On Error GoTo 0
End Function

' Повторный щелчок: своё окно показывает сам Python (ui\window.py находит
' его по заголовку и сразу выходит). Здесь только ждём, когда оно ответит.
Function PultReady()
  Dim request
  PultReady = False
  If Len(myId) = 0 Then Exit Function
  On Error Resume Next
  Set request = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  request.setTimeouts 1000, 1000, 1000, 1000
  request.open "GET", "http://127.0.0.1:" & myPort & "/api/install", False
  request.send
  If Err.Number = 0 Then
    PultReady = (request.status = 200 And Trim(request.responseText) = myId)
  End If
  Err.Clear
  On Error GoTo 0
End Function

' Решение сразу, без ожидания и без фокусировки чужого окна: на порту
' другая Труба (своя папка или старая версия) — второй пульт поднимать
' нельзя, микрофон он всё равно не отдаст.
state = КтоНаПорте()
If state = "свой" Then
  ' Своя копия уже работает: из автозагрузки делать нечего, а по
  ' двойному щелчку своё окно покажет Python.
  If trayStart Then WScript.Quit
ElseIf state = "чужая" Then
  MsgBox "Труба уже работает из другой папки; закрой её и повтори запуск." & vbCrLf & vbCrLf & _
    "Пульт на этом компьютере может быть только один: две Трубы мешают" & vbCrLf & _
    "друг другу за микрофон. Папки установки при этом независимы — эта" & vbCrLf & _
    "копия не тронута, её можно запустить позже." & vbCrLf & vbCrLf & _
    "Чужое окно мы не открываем, чтобы не перепутать, чьё это Труба.", _
    vbExclamation, "Труба — запуск"
  WScript.Quit
End If

' Окна нет, а окружения нет: Python в этой папке не запустится. Причину
' установщик уже записывал в install.log — папку могли не доделать при
' установке, а могли удалить или повредить и позже, поэтому текст не
' утверждает, что дело именно в незаконченной установке.
If Not fso.FileExists(cfg) Or Not fso.FolderExists(sitePackages) Then
  MsgBox "Не нашёл окружение Трубы. Папка .venv повреждена или отсутствует." & vbCrLf & vbCrLf & _
    "Нужных файлов Python в этой папке нет, поэтому пульт не запустится." & vbCrLf & _
    "Ничего удалять не нужно." & vbCrLf & vbCrLf & _
    "Что делать: закрой это окно и запусти «Установить Трубу» ещё раз" & vbCrLf & _
    "(файл «Установить Трубу.bat» в корне этой папки) — установщик" & vbCrLf & _
    "проверит окружение заново и скажет, чего не хватает." & vbCrLf & vbCrLf & _
    "Журнал предыдущей установки находится в файле" & vbCrLf & _
    dataDir & "\install.log", vbCritical, "Труба — запуск"
  WScript.Quit
End If

home = ""
Set cfgFile = fso.OpenTextFile(cfg, 1)
Do Until cfgFile.AtEndOfStream
  line = Trim(cfgFile.ReadLine)
  If LCase(Left(line, 7)) = "home = " Then home = Trim(Mid(line, 8))
Loop
cfgFile.Close
pythonw = fso.BuildPath(home, "pythonw.exe")
If home = "" Or Not fso.FileExists(pythonw) Then
  MsgBox "Не нашёл Python для Трубы. Нужна проверка установки." & vbCrLf & vbCrLf & _
    "Запусти «Установить Трубу» ещё раз, подробности — в файле" & vbCrLf & _
    dataDir & "\install.log", vbCritical, "Труба — запуск"
  WScript.Quit
End If

shell.Environment("PROCESS")("PYTHONPATH") = sitePackages
command = """" & pythonw & """ -m ui.window"
If trayStart Then command = command & " --tray"

' Если своя копия уже работала (повторный щелчок), окно могло быть
' спрятано в трей. Новый ui.window сверяет отпечаток, находит своё окно
' по заголовку и показывает его, после чего сразу выходит.
shell.Run command, 1, False

' До 30 с ждём готовности своего пульта на порту. Если вместо своего
' пульта на порту оказалась чужая Труба — сообщаем сразу, не ждём остаток.
' Проверка не только на второй итерации: чужая копия может занять порт и
' через десять секунд, и тогда пришлось бы ждать все 30 с и сказать не то.
' Поэтому спрашиваем периодически до конца ожидания. Чужое окно при этом
' не фокусируем: это не наш пульт.
waitSteps = 120
If trayStart Then waitSteps = 480
ready = False
For attempt = 1 To waitSteps
  WScript.Sleep 250
  If PultReady() Then
    ready = True
    WScript.Quit
  End If
  ' Каждую восьмую итерацию (раз в 2 с) смотрим, кто на порту.
  If attempt Mod 8 = 0 Then
    state = КтоНаПорте()
    If state = "чужая" Then
      MsgBox "Труба уже работает из другой папки; закрой её и повтори запуск." & vbCrLf & vbCrLf & _
        "Второй пульт не поднялся — так и должно быть: на компьютере" & vbCrLf & _
        "работает только одна Труба, иначе они мешают друг другу за микрофон." & vbCrLf & _
        "Эта копия не тронута, запусти её позже.", vbExclamation, "Труба — запуск"
      WScript.Quit
    End If
  End If
Next

If Not ready Then
  MsgBox "Пульт не запустился." & vbCrLf & vbCrLf & _
    "Порт пульта — " & myPort & ". Если его держит другая программа (не" & vbCrLf & _
    "Труба), закрой её и повтори запуск." & vbCrLf & _
    "Подробности — в data\session.log.", vbCritical, "Труба — запуск"
End If
'''

root = Path(__file__).resolve().parent.parent
цель = root / "Труба.vbs"
текст = VBS.replace("\n", "\r\n")
цель.write_bytes(b"\xff\xfe" + текст.encode("utf-16-le"))
print(f"Записан {цель} ({цель.stat().st_size} байт)")
