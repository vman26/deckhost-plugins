"""Tests for tools/platforms.py.

Written to Microsoft's unit testing best practices: names are test_<function>_<scenario>_<expected>, every
test has Arrange / Act / Assert sections with one Act, no loops or conditions in a test body (helpers
hold them), and no magic strings (named constants).
"""

import io
import json
import pathlib
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import catalog  # noqa: E402
import platforms  # noqa: E402

OPENACTION_ID = "com.example.counter"
ELGATO_ID = "com.example.mixer"
DECKHOST_ID = "deckhost.plugin.example"
OWNER = "someone"
REPOSITORY = "counter"
RELEASE_FILE = "https://github.com/someone/counter/releases/download/v1/com.example.counter.streamDeckPlugin"
NEWER_RELEASE_FILE = "https://github.com/someone/counter/releases/download/v2/com.example.counter.streamDeckPlugin"
DECKHOST_FILE = "https://example.com/deckhost.plugin.example.zip"
VERSION = "1.0.0"
NEWER_VERSION = "1.1.0"
WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"
LIMIT_BYTES = 10_000


def stream_deck_package(plugin_id: str, systems: list[str] | None) -> bytes:
    manifest = {"Name": "Test", "Version": VERSION}
    if systems is not None:
        manifest["OS"] = [{"Platform": system} for system in systems]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{plugin_id}.sdPlugin/manifest.json", json.dumps(manifest))
    return buffer.getvalue()


def deckhost_package(plugin_id: str, systems: list[str] | None = None) -> bytes:
    manifest = {"id": plugin_id, "name": "Test", "version": VERSION, "assembly": "Test.dll"}
    if systems is not None:
        manifest["platforms"] = systems
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
    return buffer.getvalue()


class FakeNetwork:
    """The catalogues, releases and packages a test sets up; counts the downloads."""

    def __init__(self, openaction=None, archive=None, packages=None, releases=None):
        self.catalogues = {platforms.OPENACTION_CATALOGUE: openaction or {}, platforms.ARCHIVE_CATALOGUE: archive or []}
        self.packages = packages or {}
        self.releases = releases or {}
        self.downloads = []

    def json(self, url):
        return self.catalogues[url]

    def get(self, url, limit=None):
        self.downloads.append(url)
        if url not in self.packages:
            raise OSError(f"no package at {url}")
        return self.packages[url]

    def newest_release_file(self, owner, repository, plugin_id):
        return self.releases.get((owner, repository))


def archive_url(plugin_id: str) -> str:
    return platforms.ARCHIVE_PACKAGE.format(id=plugin_id)


def openaction_catalogue() -> dict:
    return {OPENACTION_ID: {"name": "Counter", "repository": f"https://github.com/{OWNER}/{REPOSITORY}"}}


def run(network: FakeNetwork, previous: dict | None = None, limit: int = platforms.DEFAULT_LIMIT) -> dict:
    """Runs the tool in a temporary folder (no DeckHost entries) and returns platforms.json's plugins."""
    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)
        (root / "plugins").mkdir()
        old_plugins = catalog.PLUGINS
        catalog.PLUGINS = root / "plugins"
        try:
            previous_dir = None
            if previous is not None:
                previous_dir = root / "previous"
                previous_dir.mkdir()
                (previous_dir / platforms.OUTPUT).write_text(json.dumps({"plugins": previous}), encoding="utf-8")
            platforms.run(root / "out", previous_dir, limit, LIMIT_BYTES, network)
            return json.loads((root / "out" / platforms.OUTPUT).read_text(encoding="utf-8"))["plugins"]
        finally:
            catalog.PLUGINS = old_plugins


class PlatformsOfTests(unittest.TestCase):
    def test_platforms_of_stream_deck_manifest_gives_its_systems_in_display_order(self):
        # Arrange
        package = stream_deck_package(ELGATO_ID, ["mac", "windows"])

        # Act
        found = platforms.platforms_of(package)

        # Assert
        self.assertEqual(found, [WINDOWS, MACOS])

    def test_platforms_of_stream_deck_manifest_without_os_is_none(self):
        # Arrange
        package = stream_deck_package(ELGATO_ID, None)

        # Act
        found = platforms.platforms_of(package)

        # Assert
        self.assertIsNone(found)

    def test_platforms_of_deckhost_package_without_platforms_runs_everywhere(self):
        # Arrange
        package = deckhost_package(DECKHOST_ID)

        # Act
        found = platforms.platforms_of(package)

        # Assert
        self.assertEqual(found, platforms.ALL_PLATFORMS)

    def test_platforms_of_deckhost_package_with_platforms_keeps_them(self):
        # Arrange
        package = deckhost_package(DECKHOST_ID, [LINUX])

        # Act
        found = platforms.platforms_of(package)

        # Assert
        self.assertEqual(found, [LINUX])

    def test_platforms_of_not_a_zip_is_none(self):
        # Arrange
        package = b"not a zip"

        # Act
        found = platforms.platforms_of(package)

        # Assert
        self.assertIsNone(found)


class RunTests(unittest.TestCase):
    def test_run_elgato_and_openaction_plugins_records_their_systems(self):
        # Arrange
        network = FakeNetwork(
            openaction=openaction_catalogue(),
            archive=[{"id": ELGATO_ID, "version": VERSION}],
            packages={RELEASE_FILE: stream_deck_package(OPENACTION_ID, [WINDOWS, "mac", LINUX]), archive_url(ELGATO_ID): stream_deck_package(ELGATO_ID, [WINDOWS])},
            releases={(OWNER, REPOSITORY): RELEASE_FILE},
        )

        # Act
        found = run(network)

        # Assert
        self.assertEqual((found[OPENACTION_ID]["platforms"], found[ELGATO_ID]["platforms"]), (platforms.ALL_PLATFORMS, [WINDOWS]))

    def test_run_same_version_as_before_downloads_nothing(self):
        # Arrange
        network = FakeNetwork(archive=[{"id": ELGATO_ID, "version": VERSION}])
        previous = {ELGATO_ID: {"source": platforms.ELGATO, "version": VERSION, "platforms": [MACOS]}}

        # Act
        found = run(network, previous)

        # Assert
        self.assertEqual((network.downloads, found[ELGATO_ID]["platforms"]), ([], [MACOS]))

    def test_run_newer_openaction_release_reads_it_again(self):
        # Arrange
        network = FakeNetwork(
            openaction=openaction_catalogue(),
            packages={NEWER_RELEASE_FILE: stream_deck_package(OPENACTION_ID, [LINUX])},
            releases={(OWNER, REPOSITORY): NEWER_RELEASE_FILE},
        )
        previous = {OPENACTION_ID: {"source": platforms.OPENACTION, "version": RELEASE_FILE, "platforms": [WINDOWS]}}

        # Act
        found = run(network, previous)

        # Assert
        self.assertEqual(found[OPENACTION_ID], {"source": platforms.OPENACTION, "version": NEWER_RELEASE_FILE, "platforms": [LINUX]})

    def test_run_download_fails_keeps_what_was_found_before(self):
        # Arrange
        network = FakeNetwork(archive=[{"id": ELGATO_ID, "version": NEWER_VERSION}])
        previous = {ELGATO_ID: {"source": platforms.ELGATO, "version": VERSION, "platforms": [MACOS]}}

        # Act
        found = run(network, previous)

        # Assert
        self.assertEqual(found[ELGATO_ID]["platforms"], [MACOS])

    def test_run_over_the_limit_leaves_the_rest_for_the_next_run(self):
        # Arrange
        network = FakeNetwork(
            archive=[{"id": ELGATO_ID, "version": VERSION}, {"id": OPENACTION_ID, "version": VERSION}],
            packages={archive_url(ELGATO_ID): stream_deck_package(ELGATO_ID, [WINDOWS]), archive_url(OPENACTION_ID): stream_deck_package(OPENACTION_ID, [WINDOWS])},
        )

        # Act
        found = run(network, limit=1)

        # Assert
        self.assertEqual((len(network.downloads), sorted(found)), (1, [ELGATO_ID]))

    def test_run_manifest_without_systems_leaves_platforms_out(self):
        # Arrange
        network = FakeNetwork(archive=[{"id": ELGATO_ID, "version": VERSION}], packages={archive_url(ELGATO_ID): stream_deck_package(ELGATO_ID, None)})

        # Act
        found = run(network)

        # Assert
        self.assertNotIn("platforms", found[ELGATO_ID])


if __name__ == "__main__":
    unittest.main()
