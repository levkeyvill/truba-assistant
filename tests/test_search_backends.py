"""Поисковики в списке — только те, что знает установленная ddgs.

Незнакомое имя ddgs молча меняет на «auto», и подпись источника в журнале
была бы чужой. Сеть не нужна: проверяется реестр самой библиотеки.
"""

import unittest
from unittest import mock

from core import web


class ПоисковикиTests(unittest.TestCase):
    def test_все_поисковики_знакомы_ddgs(self):
        from ddgs.engines import ENGINES

        self.assertEqual([имя for имя in web.BACKENDS if имя not in ENGINES["text"]], [])

    def test_незнакомые_отсеиваются(self):
        with mock.patch.dict("ddgs.engines.ENGINES", {"text": {"brave": object()}}):
            self.assertEqual(web.backends(), ("brave",))

    def test_реестра_нет_список_как_есть(self):
        with mock.patch.dict("sys.modules", {"ddgs.engines": None}):
            self.assertEqual(web.backends(), web.BACKENDS)


if __name__ == "__main__":
    unittest.main()
