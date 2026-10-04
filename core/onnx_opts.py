"""Настройки сессий onnxruntime: без «кручения» рабочих потоков.

Потоки onnxruntime по умолчанию активно ждут следующую работу и нагружают
процессор, пока детектор речи слушает. Отключаем это ожидание, чтобы
освободить процессор между вызовами.
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
