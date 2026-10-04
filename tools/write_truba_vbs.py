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

' Настоящий отпечаток установки — 64 шестнадцатеричных знака. Ответ
' посторонней программы на том же порту нельзя принимать за другую Трубу.
Function ValidInstallId(value)
  Dim i, c
  ValidInstallId = False
  If Len(value) <> 64 Then Exit Function
  For i = 1 To 64
    c = LCase(Mid(value, i, 1))
    If InStr(1, "0123456789abcdef", c, vbBinaryCompare) = 0 Then Exit Function
  Next
  ValidInstallId = True
End Function

' Кто отвечает на общем порту: «свой», «чужая», «порт занят» или «никто».
' У старой 0.9.6 нет /api/install; узнаём её по структуре /api/runtime.
Function PortOwner()
  Dim request, status, body, hadHttp
  PortOwner = "никто"
  hadHttp = False
  ' Отпечаток мог появиться только что: у копии, обновлённой со старой
  ' версии кнопкой «Обновить», файла нет — его заводит сам пульт при первом
  ' запросе. Без перечитывания своя же копия выглядела «чужой» (01.10).
  If Len(myId) = 0 Then ReadInstance
  On Error Resume Next
  Set request = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  request.setTimeouts 500, 500, 500, 500
  request.open "GET", "http://127.0.0.1:" & myPort & "/api/install", False
  Err.Clear
  request.send
  If Err.Number = 0 Then
    hadHttp = True
    status = request.status
    If Err.Number = 0 Then
      If status = 200 Then
        body = Trim(request.responseText)
        ' Пульт мог завести файл отпечатка этим же запросом.
        If Len(myId) = 0 Then ReadInstance
        If Err.Number = 0 Then
          If Len(myId) > 0 And body = myId Then
            PortOwner = "свой"
          ElseIf ValidInstallId(body) Then
            PortOwner = "чужая"
          End If
        End If
      End If
    End If
  End If
  Err.Clear
  If PortOwner = "никто" Then
    Set request = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    request.setTimeouts 500, 500, 500, 500
    request.open "GET", "http://127.0.0.1:" & myPort & "/api/runtime", False
    Err.Clear
    request.send
    If Err.Number = 0 Then
      hadHttp = True
      status = request.status
      If Err.Number = 0 Then
        If status = 200 Then
          body = request.responseText
          If Err.Number = 0 Then
            If InStr(1, body, """voice""", vbTextCompare) > 0 And _
               InStr(1, body, """events""", vbTextCompare) > 0 And _
               InStr(1, body, """overview""", vbTextCompare) > 0 Then
              PortOwner = "чужая"
            End If
          End If
        End If
      End If
    End If
    Err.Clear
  End If
  If PortOwner = "никто" And hadHttp Then PortOwner = "порт занят"
  On Error GoTo 0
End Function

' Повторный щелчок: своё окно показывает сам Python (ui\window.py находит
' его по заголовку и сразу выходит). Здесь только ждём, когда оно ответит.
Function PultReady()
  Dim request, status, body
  PultReady = False
  ' То же, что в PortOwner: файл отпечатка заводит сам пульт.
  If Len(myId) = 0 Then ReadInstance
  If Len(myId) = 0 Then Exit Function
  On Error Resume Next
  Set request = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  request.setTimeouts 1000, 1000, 1000, 1000
  request.open "GET", "http://127.0.0.1:" & myPort & "/api/install", False
  Err.Clear
  request.send
  If Err.Number = 0 Then
    status = request.status
    If Err.Number = 0 Then
      If status = 200 Then
        body = Trim(request.responseText)
        ' Пульт мог завести файл отпечатка этим же запросом.
        If Len(myId) = 0 Then ReadInstance
        If Err.Number = 0 Then PultReady = (body = myId)
      End If
    End If
  End If
  Err.Clear
  On Error GoTo 0
End Function

' Решение сразу, без ожидания и без фокусировки чужого окна: на порту
' другая Труба (своя папка или старая версия) — второй пульт поднимать
' нельзя, микрофон он всё равно не отдаст.
state = PortOwner()
If state = "свой" Then
  ' Своя копия уже работает: из автозагрузки делать нечего, а по
  ' двойному щелчку своё окно покажет Python.
  If trayStart Then WScript.Quit
ElseIf state = "чужая" Then
  MsgBox "Уже открыт пульт Трубы из другой папки." & vbCrLf & _
    "Закрой его и повтори запуск этой копии.", vbExclamation, "Труба — запуск"
  WScript.Quit
ElseIf state = "порт занят" Then
  MsgBox "Труба не может запуститься: порт 8765 занят другой программой." & vbCrLf & _
    "Закрой её и повтори запуск.", vbExclamation, "Труба — запуск"
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
    state = PortOwner()
    If state = "чужая" Then
      MsgBox "Уже открыт пульт Трубы из другой папки." & vbCrLf & _
        "Закрой его и повтори запуск этой копии.", vbExclamation, "Труба — запуск"
      WScript.Quit
    ElseIf state = "порт занят" Then
      MsgBox "Труба не может запуститься: порт 8765 занят другой программой." & vbCrLf & _
        "Закрой её и повтори запуск.", vbExclamation, "Труба — запуск"
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
