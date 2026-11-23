"""Tests for the discovery search in tools/catalog.py.

In the style of tools/test_mirror.py: names are test_<function>_<scenario>_<expected>, Arrange / Act /
Assert with one Act, no loops or conditions in a test body, named constants.
"""

import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import catalog  # noqa: E402

QUERY = "topic:streamdeck-plugin archived:false"


def repositories(count, start=0):
    return [{"html_url": f"https://github.com/o/r{index}"} for index in range(start, start + count)]


class FakeSearch:
    """Answers search/repositories calls with the given pages (page number → items)."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def __call__(self, path):
        page = int(path.rsplit("&page=", 1)[1])
        self.asked.append(page)
        return {"items": self.pages.get(page, [])}


def search(pages):
    fake = FakeSearch(pages)
    with mock.patch.object(catalog, "github", fake):
        found = list(catalog.search_repositories(QUERY))
    return found, fake.asked


class SearchRepositoriesTests(unittest.TestCase):
    def test_search_repositories_full_first_page_reads_the_second(self):
        # Arrange
        pages = {1: repositories(catalog.SEARCH_PAGE_SIZE), 2: repositories(3, start=catalog.SEARCH_PAGE_SIZE)}

        # Act
        found, asked = search(pages)

        # Assert
        self.assertEqual(len(found), catalog.SEARCH_PAGE_SIZE + 3)
        self.assertEqual(asked, [1, 2])

    def test_search_repositories_short_page_is_the_last_one_read(self):
        # Arrange
        pages = {1: repositories(5)}

        # Act
        found, asked = search(pages)

        # Assert
        self.assertEqual(len(found), 5)
        self.assertEqual(asked, [1])

    def test_search_repositories_stops_at_githubs_result_limit(self):
        # Arrange
        pages = {page: repositories(catalog.SEARCH_PAGE_SIZE) for page in range(1, catalog.SEARCH_MAX_PAGES + 3)}

        # Act
        _, asked = search(pages)

        # Assert
        self.assertEqual(asked, list(range(1, catalog.SEARCH_MAX_PAGES + 1)))


if __name__ == "__main__":
    unittest.main()
