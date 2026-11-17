"""Tests for tools/mirror.py.

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
import urllib.error
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mirror  # noqa: E402

REPOSITORY = "o/r"
REPOSITORY_LINK = "https://github.com/o/r"
NO_LICENSE_REPOSITORY = "o/none"
NO_LICENSE_LINK = "https://github.com/o/none"
OTHER_REPOSITORY = "x/y"
OTHER_LINK = "https://github.com/x/y"
SITE_LINK = "https://example.com/plugin"
MIRROR_REPOSITORY = "me/plugins"
ASSET_BASE = "https://github.com/me/plugins/releases/download/elgato-mirror/"
ONE = "com.a.one"
TWO = "com.a.two"
SHA = "a" * 64
MIT = "MIT"
NON_COMMERCIAL = "CC-BY-NC-4.0"
PROPRIETARY = "LicenseRef-Proprietary"
AUTHOR = "Someone"
LIMIT_BYTES = 2000


def package(plugin_id: str, extra: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{plugin_id}.sdPlugin/manifest.json", json.dumps({"Name": "Test", "Version": "1.0.0.0", "Author": "A"}))
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


class FakeNetwork(mirror.Network):
    def __init__(self, licenses=None, packages=None):
        super().__init__(token="x")
        self.licenses = licenses or {}
        self.packages = packages or {}
        self.downloaded = []

    def license(self, owner, repository):
        value = self.licenses[f"{owner}/{repository}"]
        if isinstance(value, Exception):
            raise value
        return value

    def package(self, plugin_id, limit):
        self.downloaded.append(plugin_id)
        data = self.packages[plugin_id]
        if len(data) > limit:
            raise ValueError("too large")
        return data


def archive(plugin_id, link=REPOSITORY_LINK, version="1.0", name=None):
    return {"id": plugin_id, "name": name or plugin_id, "author": AUTHOR, "link": link, "version": version, "download": "https://x", "icon": "https://x"}


def run(network, catalogue, previous=None, blocked_ids=(), blocked_repositories=(), dry_run=False, limit=None, max_bytes=1024 * 1024):
    log = []
    result = mirror.run(network, catalogue, previous=previous or {}, blocked_ids=set(blocked_ids), blocked_repositories=set(blocked_repositories),
                        max_bytes=max_bytes, repository=MIRROR_REPOSITORY, dry_run=dry_run, limit=limit, report=log.append)
    return result + (log,)


def mirrored_before(plugin_id=ONE, version="1.0"):
    """What the previous run kept for a plugin that was already mirrored."""
    return {plugin_id: {"id": plugin_id, "version": version, "mirrored": True, "sha256": SHA, "download_url": f"{ASSET_BASE}{plugin_id}-{version}.zip"}}


def mirrored_flags(entries):
    return [entry["mirrored"] for entry in entries]


def mirrored_by_id(entries):
    return {entry["id"]: entry["mirrored"] for entry in entries}


def any_line_contains(log, text):
    return any(text in line for line in log)


def any_line_starts_with(log, text):
    return any(line.startswith(text) for line in log)


def has_no_download(entry):
    return entry["download_url"] is None and entry["sha256"] is None


def every_entry_links_to_the_marketplace(entries):
    return all(entry["marketplace_url"].startswith("https://marketplace.elgato.com/") for entry in entries)


def notice_for(entries):
    return mirror.notice(entries, MIRROR_REPOSITORY)


class RunTests(unittest.TestCase):
    def test_run_allowed_license_mirrors_the_package_with_its_checksum(self):
        # Arrange
        data = package(ONE)
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: data})

        # Act
        entries, uploads, stale, _ = run(network, [archive(ONE)])

        # Assert
        entry = entries[0]
        self.assertTrue(entry["mirrored"])
        self.assertEqual(entry["license"], MIT)
        self.assertEqual(entry["download_url"], f"{ASSET_BASE}{ONE}-1.0.zip")
        self.assertEqual(len(entry["sha256"]), 64)
        self.assertEqual(uploads, {f"{ONE}-1.0.zip": data})
        self.assertEqual(stale, [])

    def test_run_license_off_the_allowlist_lists_the_plugin_without_a_package(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: NON_COMMERCIAL})

        # Act
        entries, uploads, _, log = run(network, [archive(ONE)])

        # Assert
        self.assertEqual(mirrored_flags(entries), [False])
        self.assertEqual(entries[0]["license"], NON_COMMERCIAL)
        self.assertEqual(uploads, {})
        self.assertEqual(network.downloaded, [])
        self.assertTrue(any_line_contains(log, "allowlist"))

    def test_run_repository_without_license_lists_the_plugin_without_a_package(self):
        # Arrange
        network = FakeNetwork({NO_LICENSE_REPOSITORY: None})

        # Act
        entries, uploads, _, log = run(network, [archive(ONE, link=NO_LICENSE_LINK)])

        # Assert
        self.assertEqual(mirrored_flags(entries), [False])
        self.assertEqual(uploads, {})
        self.assertTrue(any_line_contains(log, "no license"))

    def test_run_link_that_is_not_a_github_repository_lists_the_plugin_without_a_package(self):
        # Arrange
        network = FakeNetwork()

        # Act
        entries, uploads, _, _ = run(network, [archive(ONE, link=SITE_LINK)])

        # Assert
        self.assertEqual(mirrored_flags(entries), [False])
        self.assertEqual(uploads, {})
        self.assertEqual(network.downloaded, [])

    def test_run_catalog_only_plugin_has_no_download_and_keeps_its_marketplace_link(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: NON_COMMERCIAL})

        # Act
        entries, _, _, _ = run(network, [archive(ONE)])

        # Assert
        self.assertTrue(has_no_download(entries[0]))
        self.assertTrue(every_entry_links_to_the_marketplace(entries))

    def test_run_blocklisted_id_is_not_mirrored_despite_a_good_license(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE)})

        # Act
        entries, uploads, _, _ = run(network, [archive(ONE)], blocked_ids={ONE})

        # Assert
        self.assertEqual(mirrored_flags(entries), [False])
        self.assertEqual(uploads, {})
        self.assertEqual(network.downloaded, [])

    def test_run_blocklisted_repository_is_not_mirrored_despite_a_good_license(self):
        # Arrange
        network = FakeNetwork({OTHER_REPOSITORY: MIT}, {TWO: package(TWO)})

        # Act
        entries, uploads, _, _ = run(network, [archive(TWO, link=OTHER_LINK)], blocked_repositories={OTHER_REPOSITORY})

        # Assert
        self.assertEqual(mirrored_flags(entries), [False])
        self.assertEqual(uploads, {})
        self.assertEqual(network.downloaded, [])

    def test_run_package_with_a_path_outside_the_archive_is_not_mirrored(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE, {"../escape.txt": "x"})})

        # Act
        entries, uploads, _, log = run(network, [archive(ONE)])

        # Assert
        self.assertEqual(mirrored_flags(entries), [False])
        self.assertEqual(uploads, {})
        self.assertTrue(any_line_contains(log, "outside the archive"))

    def test_run_package_that_is_not_a_zip_is_not_mirrored(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: b"not a zip"})

        # Act
        entries, _, _, log = run(network, [archive(ONE)])

        # Assert
        self.assertEqual(mirrored_flags(entries), [False])
        self.assertTrue(any_line_contains(log, "not a zip"))

    def test_run_package_over_the_size_limit_is_not_mirrored(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE, {"f": "x" * 5000})})

        # Act
        entries, _, _, log = run(network, [archive(ONE)], max_bytes=LIMIT_BYTES)

        # Assert
        self.assertEqual(mirrored_flags(entries), [False])
        self.assertTrue(any_line_contains(log, "download failed"))

    def test_run_good_package_is_mirrored_next_to_bad_ones(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE, {"../escape.txt": "x"}), TWO: package(TWO)})

        # Act
        entries, uploads, _, _ = run(network, [archive(ONE), archive(TWO, version="2")])

        # Assert
        self.assertEqual(mirrored_by_id(entries), {ONE: False, TWO: True})
        self.assertEqual(list(uploads), [f"{TWO}-2.zip"])

    def test_run_package_of_another_plugin_is_not_mirrored(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: package(TWO)})

        # Act
        entries, uploads, _, log = run(network, [archive(ONE)])

        # Assert
        self.assertFalse(entries[0]["mirrored"])
        self.assertEqual(uploads, {})
        self.assertTrue(any_line_contains(log, f"not {ONE}"))

    def test_run_version_already_mirrored_is_kept_and_not_downloaded_again(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT})

        # Act
        entries, uploads, stale, _ = run(network, [archive(ONE)], previous=mirrored_before())

        # Assert
        self.assertTrue(entries[0]["mirrored"])
        self.assertEqual(entries[0]["sha256"], SHA)
        self.assertEqual(network.downloaded, [])
        self.assertEqual((uploads, stale), ({}, []))

    def test_run_new_version_uploads_the_new_asset_and_removes_the_old_one(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE)})

        # Act
        entries, uploads, stale, _ = run(network, [archive(ONE, version="1.1")], previous=mirrored_before())

        # Assert
        self.assertEqual(entries[0]["version"], "1.1")
        self.assertEqual(list(uploads), [f"{ONE}-1.1.zip"])
        self.assertEqual(stale, [f"{ONE}-1.0.zip"])

    def test_run_license_lost_takes_the_package_down(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: PROPRIETARY})

        # Act
        entries, _, stale, _ = run(network, [archive(ONE)], previous=mirrored_before())

        # Assert
        self.assertFalse(entries[0]["mirrored"])
        self.assertIsNone(entries[0]["download_url"])
        self.assertEqual(stale, [f"{ONE}-1.0.zip"])

    def test_run_plugin_added_to_the_blocklist_takes_the_package_down(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT})

        # Act
        entries, _, stale, _ = run(network, [archive(ONE)], previous=mirrored_before(), blocked_ids={ONE})

        # Assert
        self.assertFalse(entries[0]["mirrored"])
        self.assertEqual(stale, [f"{ONE}-1.0.zip"])

    def test_run_failed_license_lookup_keeps_what_was_mirrored(self):
        # Arrange
        error = urllib.error.HTTPError("https://api.github.com", 403, "rate limited", {}, None)
        before = mirrored_before()

        # Act
        entries, _, stale, _ = run(FakeNetwork({REPOSITORY: error}), [archive(ONE)], previous=before)

        # Assert
        self.assertEqual(entries[0], before[ONE])
        self.assertEqual(stale, [])

    def test_run_failed_license_lookup_without_an_earlier_copy_only_lists_the_plugin(self):
        # Arrange
        error = urllib.error.HTTPError("https://api.github.com", 403, "rate limited", {}, None)

        # Act
        entries, uploads, _, _ = run(FakeNetwork({REPOSITORY: error}), [archive(TWO)])

        # Assert
        self.assertFalse(entries[0]["mirrored"])
        self.assertEqual(uploads, {})

    def test_run_dry_run_downloads_nothing_and_says_what_it_would_mirror(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT})

        # Act
        entries, uploads, _, log = run(network, [archive(ONE)], dry_run=True)

        # Assert
        self.assertEqual(network.downloaded, [])
        self.assertEqual(uploads, {})
        self.assertFalse(entries[0]["mirrored"])
        self.assertTrue(any_line_starts_with(log, "would mirror"))

    def test_run_limit_mirrors_at_most_that_many_new_packages(self):
        # Arrange
        ids = [f"com.a.{i}" for i in range(3)]
        network = FakeNetwork({REPOSITORY: MIT}, {plugin_id: package(plugin_id) for plugin_id in ids})

        # Act
        entries, uploads, _, _ = run(network, [archive(plugin_id) for plugin_id in ids], limit=2)

        # Assert
        self.assertEqual(len(uploads), 2)
        self.assertEqual(sum(mirrored_flags(entries)), 2)


class WriteOutputTests(unittest.TestCase):
    def test_write_output_lists_plugins_by_id_and_stages_the_packages(self):
        # Arrange
        data = package(ONE)
        entries, uploads, stale, _ = run(FakeNetwork({REPOSITORY: MIT}, {ONE: data}), [archive(TWO, link="https://x.example"), archive(ONE)])
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)

            # Act
            mirror.write_output(out, entries, uploads, stale, MIRROR_REPOSITORY)

            # Assert
            catalog = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["id"] for entry in catalog], [ONE, TWO])
            self.assertEqual((out / "upload" / f"{ONE}-1.0.zip").read_bytes(), data)

    def test_write_output_notice_names_the_copied_plugin_and_not_the_catalog_only_one(self):
        # Arrange
        entries, uploads, stale, _ = run(FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE)}), [archive(TWO, link="https://x.example"), archive(ONE)])
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)

            # Act
            mirror.write_output(out, entries, uploads, stale, MIRROR_REPOSITORY)

            # Assert
            notice = (out / "NOTICE.md").read_text(encoding="utf-8")
            self.assertIn("1 of 2 plugins are copied", notice)
            self.assertIn(f"| {ONE} | {AUTHOR} | 1.0 | {MIT} | {REPOSITORY_LINK} |", notice)
            self.assertNotIn(f"{TWO} |", notice)

    def test_run_started_from_the_written_output_changes_nothing(self):
        # Arrange
        catalogue = [archive(TWO, link="https://x.example"), archive(ONE)]
        entries, uploads, stale, _ = run(FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE)}), catalogue)
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)
            mirror.write_output(out, entries, uploads, stale, MIRROR_REPOSITORY)
            previous = mirror.load_previous(out)

            # Act
            again, again_uploads, again_stale, _ = run(FakeNetwork({REPOSITORY: MIT}), catalogue, previous=previous)

            # Assert
            self.assertEqual(again, entries)
            self.assertEqual((again_uploads, again_stale), ({}, []))


class NoticeTests(unittest.TestCase):
    def test_notice_says_the_mirror_is_not_by_the_plugin_authors_or_affiliated_with_elgato(self):
        # Arrange
        entries = []

        # Act
        notice = notice_for(entries)

        # Assert
        self.assertIn("We are not the authors of these plugins and are not affiliated with Elgato", notice)

    def test_notice_credits_and_links_the_community_archive(self):
        # Arrange
        entries = []

        # Act
        notice = notice_for(entries)

        # Assert
        self.assertIn("https://github.com/nekename/ElgatoAppStoreArchive", notice)

    def test_notice_says_every_plugin_keeps_its_license_and_author(self):
        # Arrange
        entries = []

        # Act
        notice = notice_for(entries)

        # Assert
        self.assertIn("Every plugin keeps", notice)
        self.assertIn("its own license and its author", notice)

    def test_notice_names_the_takedown_route(self):
        # Arrange
        entries = []

        # Act
        notice = notice_for(entries)

        # Assert
        self.assertIn(f"https://github.com/{MIRROR_REPOSITORY}", notice)
        self.assertIn("mirror/blocklist.json", notice)


class SourceRepositoryTests(unittest.TestCase):
    def test_source_repository_plain_github_link_gives_owner_and_repository(self):
        # Arrange
        link = "https://github.com/BarRaider/streamdeck-delayedtext"

        # Act
        result = mirror.source_repository(link)

        # Assert
        self.assertEqual(result, ("BarRaider", "streamdeck-delayedtext"))

    def test_source_repository_dot_git_suffix_is_dropped(self):
        # Arrange
        link = "https://github.com/o/r.git"

        # Act
        result = mirror.source_repository(link)

        # Assert
        self.assertEqual(result, ("o", "r"))

    def test_source_repository_link_into_a_folder_gives_the_repository(self):
        # Arrange
        link = "https://github.com/o/r/tree/main/plugin"

        # Act
        result = mirror.source_repository(link)

        # Assert
        self.assertEqual(result, ("o", "r"))

    def test_source_repository_other_site_gives_none(self):
        # Arrange
        link = "https://barraider.github.io/"

        # Act
        result = mirror.source_repository(link)

        # Assert
        self.assertIsNone(result)

    def test_source_repository_no_link_gives_none(self):
        # Arrange
        link = None

        # Act
        result = mirror.source_repository(link)

        # Assert
        self.assertIsNone(result)


class AssetNameTests(unittest.TestCase):
    def test_asset_name_unsafe_characters_become_underscores(self):
        # Arrange
        version = "1.0+beta 2"

        # Act
        name = mirror.asset_name(ONE, version)

        # Assert
        self.assertEqual(name, f"{ONE}-1.0_beta_2.zip")


class AllowedLicensesTests(unittest.TestCase):
    def test_allowed_licenses_excludes_non_commercial_and_unnamed_licenses(self):
        # Arrange
        refused = {NON_COMMERCIAL, "CC-BY-NC-SA-4.0", "NOASSERTION", "Other", PROPRIETARY}

        # Act
        overlap = refused & mirror.ALLOWED_LICENSES

        # Assert
        self.assertEqual(overlap, set())


class BlocklistTests(unittest.TestCase):
    def test_load_blocklist_ids_and_repositories_are_read_in_lower_case(self):
        # Arrange
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "blocklist.json"
            path.write_text(json.dumps({"ids": ["Com.A.One"], "repositories": ["O/R"]}), encoding="utf-8")

            # Act
            result = mirror.load_blocklist(path)

            # Assert
            self.assertEqual(result, ({ONE}, {REPOSITORY}))

    def test_load_blocklist_missing_file_gives_empty_sets(self):
        # Arrange
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "none.json"

            # Act
            result = mirror.load_blocklist(path)

            # Assert
            self.assertEqual(result, (set(), set()))

    def test_load_blocklist_the_file_in_the_repository_parses(self):
        # Arrange
        path = pathlib.Path(__file__).resolve().parent.parent / "mirror" / "blocklist.json"

        # Act
        ids, repositories = mirror.load_blocklist(path)

        # Assert
        self.assertIsInstance(ids, set)
        self.assertIsInstance(repositories, set)


if __name__ == "__main__":
    unittest.main()
