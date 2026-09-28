"""Настройки сессий onnxruntime: без «кручения» рабочих потоков.

По умолчанию потоки onnxruntime после работы не засыпают, а крутятся в
ожидании следующей — ради долей миллисекунды отклика. Детектор речи зовут
каждые 32 мс, и они не засыпали вовсе: 28.09 замер на живом пульте — четыре
потока по 100 % всё время, пока Труба слушает (четыре ядра впустую, в том
числе когда хозяин в игре). Опыт: детектор с кручением — 396 % одного ядра,
без — около нуля, вызов 0.36 → 0.39 мс; распознавание фразы в 6 с без
кручения даже быстрее (213 мс против 298) — ему не мешают крутящиеся соседи.
"""


def quiet(threads: int | None = None):
    """SessionOptions без кручения потоков. `threads` — сколько потоков дать.

    None — сколько решит onnxruntime (распознаванию полезны все ядра);
    маленькому детектору речи хватает одного.
    """
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    if threads:
        options.intra_op_num_threads = int(threads)
        options.inter_op_num_threads = 1
    return options
