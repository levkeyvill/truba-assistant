"""Инструменты действий: снимок, клип, взгляд на экран.

Ни сети, ни настоящего снимка экрана, ни NVIDIA: подменены и действия, и
телефон. Проверяется одно: модель зовёт инструмент — действие происходит, и
ей уходит честный ответ; чего в `brain.actions` нет, того нет и в промпте.
"""

import json
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from PIL import Image

import config
from core import abilities, hands, hotkeys, launcher, replay, screen
from core.brain import Brain

APPS = [{"id": "youtube", "title": "YouTube", "kind": "url", "url": "https://youtube.com"}]
SHOT = "data:image/jpeg;base64,КАРТИНКА"


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


def _tool_call(name, args="{}", call_id="c1"):
    return [_chunk(calls=[_call(0, call_id, name, args)])]


class _Client:
    """Отдаёт заранее заготовленные потоки и запоминает тела запросов."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _brain(streams, actions=None, provider="deepseek"):
    brain = object.__new__(Brain)
    brain.provider = provider
    brain._home = provider
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client(streams)
    brain._reply_lock = threading.RLock()
    brain._history = deque(maxlen=10)
    brain._persona = "тест"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    brain._keep_history = lambda: None
    brain._undigested = 0
    brain._tools_ok = None
    brain._quiet = None
    brain._usage_ok = None
    brain.on_event = None
    brain.actions = {} if actions is None else actions
    # Снимок и взгляд спрашивают у модели «просил ли он?» (core/hands.JUDGED).
    # Здесь она отвечает «да»: этот файл проверяет сами действия, а отказ
    # модели разбирает tests/test_action_judge.py.
    brain._ask_plainly = lambda *a, **k: NS(
        choices=[NS(message=NS(content="да"))])
    return brain


def _names(brain, index=0):
    return [t["function"]["name"] for t in brain._client.bodies[index].get("tools", [])]


def _fake_actions(calls):
    """Подменённый `brain.actions`: запоминает, что позвали, и отвечает."""
    def make(key, answer):
        def action():
            calls.append(key)
            return answer
        return action

    return {
        "screenshot": make("screenshot", "готово, снимок на телефоне: shot.jpg"),
        "moment": make("moment", "нажал Alt+Shift+F9"),
        "look": make("look", SHOT),
    }



class ActionToolsTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        # Память о недавних действиях общая на процесс: без сброса второй
        # снимок подряд получает «Уже сделала», а не снимает.
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def test_screenshot_action_runs_and_needs_no_second_round(self):
        # 26 сентября: снимок получился, а второй круг в облако уходил только
        # ради слова «готово». Теперь его нет, и слово говорим мы сами.
        calls = []
        brain = _brain([_tool_call(hands.SHOT_NAME, '{"because": "сделать скриншот"}'),
                        [_chunk("Готово, скинула на телефон.")]],
                       _fake_actions(calls))
        # Ровно та фраза из журнала: короткий разбор её не берёт.
        said = list(brain.reply("Слушай, а ты можешь сделать скриншот?"))
        self.assertEqual(calls, ["screenshot"])
        self.assertEqual(len(brain._client.bodies), 1)
        # Сказано подтверждение, а не ответ модели из второго круга: выбор
        # варианта случайный, поэтому сверяем со всем набором, а не с одной
        # строкой — иначе тест ловил бы только один из двух вариантов.
        self.assertEqual(len(said), 1)
        self.assertIn(said[0], hands.CONFIRM[hands.SHOT_NAME])
        # В историю идёт сказанное — иначе потом она не знает, что снимок был.
        self.assertEqual(brain._history[-1]["content"], said[0])

    def test_screenshot_reaches_the_journal(self):
        # Здесь словарь настоящий (hands.actions_for), подменён только экран:
        # так проверяется, что событие и снимок идут тем же путём, что и у
        # голосовой команды.
        events = []
        path = mock.Mock()
        path.__str__ = lambda _self="": "shot.jpg"
        path.name = "shot.jpg"
        brain = _brain([_tool_call(hands.SHOT_NAME, '{"because": "сделай скриншот"}'),
                        [_chunk("Вот.")]],
                       hands.actions_for(lambda kind, payload: events.append((kind, payload))))
        with mock.patch.object(screen, "take", return_value=(path, SHOT)) as take:
            list(brain.reply("сделай скриншот"))
        take.assert_called_once_with("primary")
        # В журнал пульта — тот же вид события, что у голосовой команды.
        self.assertEqual(events, [("shot_ready", "shot.jpg")])
        self.assertEqual(len(brain._client.bodies), 1)

    def test_moment_with_replay_off_reaches_the_model_honestly(self):
        calls = []
        actions = _fake_actions(calls)
        # Настоящее действие при выключенном повторе отдаёт ошибку, а не
        # текст: подмена обязана вести себя так же, иначе проверяет не то.
        actions["moment"] = lambda: (
            calls.append("moment"),
            json.dumps({"error": "момент не сохранился: повтор выключен. "
                                 "Повтор включила"}, ensure_ascii=False),
        )[1]
        brain = _brain([_tool_call(hands.MOMENT_NAME,
                                   '{"because": "сделать мгновенный повтор"}'),
                        [_chunk("Не вышло, момент не сохранился.")]], actions)
        said = list(brain.reply("А ты можешь сделать мгновенный повтор?"))
        self.assertEqual(calls, ["moment"])
        self.assertIn("не сохранился", brain._client.bodies[1]["messages"][-1]["content"])
        self.assertEqual(said, ["Не вышло, момент не сохранился."])

    def test_look_sends_the_picture_to_the_model(self):
        calls = []
        brain = _brain([_tool_call(hands.LOOK_NAME,
                                   '{"because": "посмотреть, что у меня сейчас на экране"}'),
                        [_chunk("У тебя там валорот открыт.")]],
                       _fake_actions(calls))
        said = list(brain.reply("А ты можешь посмотреть, что у меня сейчас на экране?"))
        self.assertEqual(calls, ["look"])
        self.assertEqual(said, ["У тебя там валорот открыт."])
        messages = brain._client.bodies[1]["messages"]
        # Сообщение tool картинку не несёт: следом кладём сообщение с ней.
        self.assertEqual(messages[-2]["role"], "tool")
        self.assertEqual(messages[-2]["content"], "снимок ниже")
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"][1]["image_url"]["url"], SHOT)
        # В историю разговора картинка не идёт: дорого тащить в каждый запрос.
        turn = brain._history[-1]
        self.assertIsInstance(turn["content"], str)

    def test_screenshot_and_look_in_one_request_capture_once(self):
        events = []
        actions = hands.actions_for(lambda kind, payload: events.append((kind, payload)))
        calls = [
            _chunk(calls=[
                _call(0, "shot", hands.SHOT_NAME,
                      '{"because":"сделай скриншот"}'),
                _call(1, "look", hands.LOOK_NAME,
                      '{"because":"скажи что на экране"}'),
            ]),
        ]
        brain = _brain([calls, [_chunk("На экране фильм.")]], actions)
        path = NS(name="shot.jpg")
        with mock.patch.object(screen, "take", return_value=(path, SHOT)) as take:
            self.assertEqual(list(brain.reply(
                "Сделай скриншот и скажи что на экране")), ["На экране фильм."])
        take.assert_called_once_with("primary")
        self.assertEqual(events, [("shot_ready", str(path))])
        self.assertEqual(brain._client.bodies[1]["messages"][-1]["content"][1]
                         ["image_url"]["url"], SHOT)

    def test_looking_at_saved_shots_does_not_capture_again(self):
        actions = hands.actions_for(lambda *_: None)
        brain = _brain([_tool_call(hands.SAVED_LOOK_NAME,
                                   '{"because":"посмотри на прошлые снимки"}'),
                        [_chunk("На первом кадре фильм.")]], actions)
        saved = [(NS(name="one.png"), "data:image/jpeg;base64,ONE"),
                 (NS(name="two.png"), "data:image/jpeg;base64,TWO")]
        with mock.patch.object(screen, "recent", return_value=saved), \
                mock.patch.object(screen, "take") as take:
            said = list(brain.reply("Посмотри на прошлые снимки"))
        self.assertEqual(said, ["На первом кадре фильм."])
        take.assert_not_called()
        messages = brain._client.bodies[1]["messages"]
        self.assertEqual(messages[-3]["content"], "снимки ниже")
        self.assertEqual([m["content"][1]["image_url"]["url"]
                          for m in messages[-2:]],
                         ["data:image/jpeg;base64,ONE",
                          "data:image/jpeg;base64,TWO"])


class ToolSetTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = False
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.WEB_SEARCH = self._saved

    def _names_for(self, text, actions=None):
        brain = _brain([[_chunk("Ок.")]], _fake_actions([]) if actions is None else actions)
        list(brain.reply(text))
        return _names(brain)

    def test_same_set_for_a_greeting_and_for_a_request(self):
        # Одинаковый набор нужен и модели, и кешу: запрос с тем же началом
        # OpenAI считает дешевле.
        self.assertEqual(self._names_for("как дела?"), self._names_for("сделай скрин"))

    def test_action_tools_come_first_and_keep_their_order(self):
        names = self._names_for("как дела?")
        # YouTube стоит сразу после close_app и всегда: ролики и каналы
        # запускаются голосом, а не кнопкой, и от `apps.json` он не зависит.
        # Заметки — сразу за ним: их тоже не бывает, если в apps.json пусто.
        # Раскладка, музыка и звук компьютера — сразу за чтением заметок: тоже
        # всегда и тоже ни от чего внешнего не зависят. Папки — сразу за ними:
        # стандартные папки Windows есть у каждого, и «открывай загрузки» не
        # должно уходить в облако. Напоминания и таймеры — сразу за папками, тем
        # же блоком и всегда: «напомни через двадцать минут» тоже не должно
        # уходить в облако на уточнение.
        # Документы — сразу за ними: файлы у хозяина есть всегда, а
        # «перескажи последний скачанный PDF» — длинная фраза, которой
        # незачем идти в облако на уточнение. Поиск файла по названию — сразу за
        # документами: он тоже всегда, и «открой на диске ц документ» — длинная
        # фраза, которой незачем идти в облако. Запись в буфер обмена — сразу за
        # чтением буфера и тоже всегда: «исправь ошибки в скопированном и положи
        # обратно» — длинная фраза, а без инструмента модель сказала бы, что не
        # умеет. Питание компьютера — сразу за ними и тоже всегда: «выключи
        # компьютер» незачем уводить в облако на уточнение, а сам инструмент
        # только задаёт вопрос.
        self.assertEqual(
            names, [hands.NAME, hands.CLOSE_NAME, hands.YT_NAME, hands.NOTE_NAME,
                    hands.READ_NAME, hands.LAYOUT_NAME, hands.MEDIA_NAME,
                    hands.PCVOL_NAME, hands.FOLDER_NAME, hands.SET_REM_NAME,
                    hands.LIST_REM_NAME, hands.CANCEL_REM_NAME,
                    hands.DOC_NAME, hands.FIND_NAME, hands.CLIP_NAME,
                    hands.PUT_NAME, hands.POWER_NAME,
                    hands.SHOT_NAME, hands.MOMENT_NAME, hands.LOOK_NAME]
        )
        self.assertEqual(self._names_for("а ты можешь снять скрин?"), names)

    def test_no_action_means_no_tool(self):
        for name in (hands.SHOT_NAME, hands.MOMENT_NAME, hands.LOOK_NAME):
            self.assertNotIn(name, self._names_for("сделай скрин", actions={}))
        # Часть словаря — выдаётся только то, что в нём есть.
        names = self._names_for("сделай скрин", actions={"screenshot": lambda: ""})
        self.assertIn(hands.SHOT_NAME, names)
        self.assertNotIn(hands.MOMENT_NAME, names)
        self.assertNotIn(hands.LOOK_NAME, names)


class HandsActionTests(unittest.TestCase):
    """Живой код действий — с подменой экрана, NVIDIA и телефона."""

    def setUp(self):
        self.events = []
        # Память о недавних действиях общая на процесс: без сброса второй
        # снимок подряд отвечает «Уже сделала», а не снимает.
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        self.server = NS(
            sounds=[], shots=[],
            send_sound=lambda name: self.server.sounds.append(name),
            send_shot=lambda image, caption, hide=15.0: self.server.shots.append(
                (image, caption, hide)),
        )
        self.emit = lambda kind, payload: self.events.append((kind, payload))
        self.path = mock.Mock()
        self.path.__str__ = lambda _self="": "shot.jpg"
        self.path.name = "shot.jpg"

    def test_shoot_shuts_the_blink_and_sends_the_picture(self):
        with mock.patch.object(screen, "take", return_value=(self.path, SHOT)):
            _got, image = hands.shoot(self.emit, lambda: self.server)
        self.assertEqual(image, SHOT)
        self.assertEqual(self.events, [("shot_ready", "shot.jpg")])
        self.assertEqual(self.server.sounds, ["shutter"])
        # Сколько секунд лежать — тоже в сообщении: телефон ставит по ним таймер.
        self.assertEqual(self.server.shots, [(SHOT, "shot.jpg", hands.SHOT_HIDE)])

    def test_shoot_without_a_phone_still_takes_the_picture(self):
        with mock.patch.object(screen, "take", return_value=(self.path, SHOT)):
            _got, image = hands.shoot(self.emit, None)
        self.assertEqual(image, SHOT)
        self.assertEqual(self.server.shots, [])

    def test_clip_reports_what_it_pressed(self):
        with mock.patch.object(hotkeys, "save_moment", return_value="Alt+Shift+F9"):
            what, off = hands.clip(self.emit, lambda: self.server)
        self.assertEqual((what, off), ("Alt+Shift+F9", False))
        self.assertEqual(self.events, [("moment_sent", "Alt+Shift+F9")])
        self.assertEqual(self.server.sounds, ["moment"])

    def test_clip_with_replay_off_says_so_and_asks_to_turn_it_on(self):
        thread = NS(start=mock.Mock())
        with mock.patch.object(hotkeys, "save_moment",
                               side_effect=replay.ReplayOff("повтор выключен")), \
                mock.patch.object(replay, "turn_on", return_value=True) as turn_on, \
                mock.patch.object(hands.threading, "Thread", return_value=thread) as spawn:
            what, off = hands.clip(self.emit, lambda: self.server)
        self.assertTrue(off)
        self.assertIn("не сохранился", what)
        self.assertEqual(self.events, [("moment_failed", "повтор выключен")])
        self.assertEqual(self.server.sounds, ["fail"])
        # Повтор включаем сразу и в фоне: это нажатия клавиш, они занимают время.
        self.assertIs(spawn.call_args.kwargs["target"], turn_on)
        self.assertTrue(spawn.call_args.kwargs["daemon"])
        thread.start.assert_called_once()

    def test_actions_for_reuses_a_capture_within_one_reply(self):
        actions = hands.actions_for(self.emit, lambda: self.server)
        self.assertEqual(sorted(actions), ["look", "look_saved", "moment",
                                           "reset_capture", "screenshot"])
        with mock.patch.object(hands, "shoot", return_value=(self.path, SHOT)) as shoot:
            self.assertIn("готово", actions["screenshot"]())
            # «посмотри» отдаёт модели сам снимок, а не путь к нему.
            self.assertEqual(actions["look"](), SHOT)
            shoot.assert_called_once()
            actions["reset_capture"]()
            self.assertEqual(actions["look"](), SHOT)
            self.assertEqual(shoot.call_count, 2)

    def test_run_action_without_an_action_says_it_is_unavailable(self):
        self.assertIn("недоступно", json.loads(hands.run_action(hands.SHOT_NAME, {}))["error"])

    def test_previous_screenshots_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(screen, "shots_dir", return_value=Path(folder)):
            for number in range(1, 4):
                Image.new("RGB", (2, 2), (number * 30, 0, 0)).save(
                    Path(folder) / f"2026-09-30_21-00-0{number}.png")
            (Path(folder) / "2026-09-30_21-00-04.png").write_bytes(b"broken")
            found = screen.recent(3)
        self.assertEqual([path.name for path, _image in found],
                         ["2026-09-30_21-00-02.png", "2026-09-30_21-00-03.png"])
        self.assertTrue(all(image.startswith("data:image/jpeg;base64,")
                            for _path, image in found))


class PromptTests(unittest.TestCase):
    def test_prompt_never_talks_about_buttons_or_access(self):
        text = abilities.describe(APPS)
        for word in ("не дали", "кнопк", "может не быть", "доступ"):
            self.assertNotIn(word, text.lower(), word)
        self.assertIn(abilities.NO_LYING, text)

    def test_prompt_names_the_tools(self):
        text = abilities.describe(APPS)
        for name in (hands.SHOT_NAME, hands.MOMENT_NAME, hands.LOOK_NAME):
            self.assertIn(name, text)

    def test_last_round_without_tools_pushes_no_explanations(self):
        # Последний круг: инструментов нет, и слова про устройство вещей
        # толкать её не должны. Действие здесь неудачное: короткий путь после
        # удачного закончил бы ответ после первого круга.
        actions = _fake_actions([])
        actions["screenshot"] = lambda: '{"error": "не вышло"}'
        brain = _brain([_tool_call(hands.SHOT_NAME)] * 4 + [[_chunk("Готово.")]],
                       actions)
        list(brain.reply("сделай скриншот"))
        last = brain._client.bodies[-1]
        self.assertNotIn("tools", last)
        words = last["messages"][-1]["content"]
        self.assertIn("хватит пробовать", words)
        for word in ("не дали", "кнопк", "доступ"):
            self.assertNotIn(word, words.lower(), word)


class WiringTests(unittest.TestCase):
    """Кто создаёт мозг — тот и отдаёт ему действия. Проверяем оба места."""

    def test_voice_loop_fills_the_brain_actions(self):
        from core.voice_loop import VoiceLoop

        brain = NS()
        loop = object.__new__(VoiceLoop)
        loop._brain = brain
        loop._emit = lambda kind, payload: None
        loop._server = None
        loop._wire_brain()
        # «Диктовка» — тоже действие: без него у модели нет способа начать
        # запись заметки (27.09 — слова «диктуй» без режима). И чтение вслух:
        # без него модель на «прочитай вслух» не знала бы, что читать некому.
        self.assertEqual(sorted(brain.actions),
                         ["dictation", "look", "look_saved", "moment",
                          "read_aloud", "reset_capture", "screenshot"])
        self.assertEqual(len(hands.action_tools(brain.actions)), 4)

    def test_voice_loop_without_a_brain_survives(self):
        from core.voice_loop import VoiceLoop

        loop = object.__new__(VoiceLoop)
        loop._brain = None
        loop._emit = lambda kind, payload: None
        loop._wire_brain()  # мозга ещё нет — это не ошибка

    def test_pult_fills_the_brain_actions(self):
        from ui.web_runtime import WebRuntime

        brain = NS()
        pult = object.__new__(WebRuntime)
        pult.brain = brain
        pult.server = None
        pult.voice = NS(begin_dictation=mock.Mock(return_value=True),
                        read_aloud=mock.Mock(return_value=(True, "отчёт.txt")))
        pult._remember = lambda kind, payload: None
        pult._wire_brain()
        # Чат пульта ходит в тот же мозг: действия те же, что и голосом.
        self.assertEqual(sorted(brain.actions),
                         ["dictation", "look", "look_saved", "moment",
                          "read_aloud", "reset_capture", "screenshot"])
        self.assertIsNotNone(brain.on_event)


if __name__ == "__main__":
    unittest.main()


    def test_run_action_turns_a_crash_into_an_error(self):
        def boom():
            raise OSError("занято")

        error = json.loads(hands.run_action(hands.MOMENT_NAME, {"moment": boom}))["error"]
        self.assertIn("не вышло", error)
        self.assertIn("занято", error)


        self.assertEqual(turn["content"], "У тебя там валорот открыт.")
        self.assertNotIn(SHOT, json.dumps(turn, ensure_ascii=False))

    def test_look_failure_does_not_send_a_picture(self):
        brain = _brain([_tool_call(hands.LOOK_NAME), [_chunk("Не получилось.")]])
        list(brain.reply("глянь на экран"))
        answer = brain._client.bodies[1]["messages"][-1]
        self.assertEqual(answer["role"], "tool")
        self.assertIn("error", json.loads(answer["content"]))
