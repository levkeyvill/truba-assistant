"""Отмена ожидания сети без запуска следующего ответа поверх старого мозга."""
import queue
import threading


class ReplyCancelled(Exception):
    pass


class _OwnedStream:
    def __init__(self, stream, cancel):
        self._stream = stream
        self._iterator = stream_from(lambda: stream, cancel)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iterator)

    def close(self):
        self._iterator.close()
        closer = getattr(self._stream, 'close', None)
        if callable(closer):
            def close_safely():
                try:
                    closer()
                except Exception:
                    pass
            threading.Thread(target=close_safely, name='truba-owned-stream', daemon=True).start()


def check(cancel):
    if cancel is not None and cancel.is_set():
        raise ReplyCancelled()


def wrap_reply_stream(stream, cancel):
    return stream if cancel is None or isinstance(stream, _OwnedStream) else _OwnedStream(stream, cancel)


def open_reply_stream(open_stream, cancel):
    if cancel is None:
        return open_stream()
    check(cancel)
    ready = threading.Event()
    result = {}
    abandoned = threading.Event()

    def open_connection():
        try:
            stream = open_stream()
            result['stream'] = stream
            if abandoned.is_set() or cancel.is_set():
                closer = getattr(stream, 'close', None)
                if callable(closer):
                    closer()
        except Exception as exc:
            result['error'] = exc
        finally:
            ready.set()

    threading.Thread(target=open_connection, name='truba-model-open', daemon=True).start()
    try:
        while not ready.wait(.05):
            check(cancel)
        check(cancel)
        if 'error' in result:
            raise result['error']
        stream = result['stream']
        return _OwnedStream(stream, cancel)
    except ReplyCancelled:
        abandoned.set()
        if 'stream' in result:
            stream = result['stream']
            closer = getattr(stream, 'close', None)
            if callable(closer):
                threading.Thread(target=closer, name='truba-abandoned-stream', daemon=True).start()
        raise


def stream_from(open_stream, cancel):
    """Открывает и читает только сетевой поток; состояние Brain здесь не меняется.

    Отмена освобождает вызывающий поток за один короткий тик. Даже если
    HTTP ещё открывается, его поздний результат будет закрыт и отброшен.
    Очередь ограничена, чтобы быстрый провайдер не копил ответ в памяти.
    """
    if cancel is None:
        yield from open_stream()
        return
    check(cancel)
    items = queue.Queue(maxsize=8)
    abandoned = threading.Event()
    stream = None

    def close():
        closer = getattr(stream, 'close', None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass

    def put(kind, value):
        while not abandoned.is_set() and not cancel.is_set():
            try:
                items.put((kind, value), timeout=.05)
                return True
            except queue.Full:
                pass
        return False

    def read():
        nonlocal stream
        try:
            stream = open_stream()
            if cancel.is_set() or abandoned.is_set():
                return
            for chunk in stream:
                if not put('chunk', chunk):
                    return
        except Exception as exc:
            put('error', exc)
        finally:
            close()
            put('done', None)

    threading.Thread(target=read, name='truba-model-stream', daemon=True).start()
    try:
        while True:
            check(cancel)
            try:
                kind, value = items.get(timeout=.05)
            except queue.Empty:
                continue
            check(cancel)
            if kind == 'done':
                return
            if kind == 'error':
                raise value
            yield value
    finally:
        abandoned.set()
        # close() может ждать HTTP-замка, поэтому не блокирует отмену хода.
        threading.Thread(target=close, name='truba-close-stream', daemon=True).start()
