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
PNG = mirror.PNG_SIGNATURE + b"icon"
FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "marketplace_page.json"
FIRST_PAGE = "https://mp-gateway.elgato.com/products?page=1"
SECOND_PAGE = "https://mp-gateway.elgato.com/products?page=2"
ELSEWHERE_PAGE = "https://example.com/products?page=2"
ONE_PRODUCT = "11111111-1111-1111-1111-111111111111"
PAID_PRODUCT = "22222222-2222-2222-2222-222222222222"
PAID_NAME = "Paid Plugin"
PAGES_ICON = "https://me.github.io/plugins/mirror/icons/com.a.one.png"


def package(plugin_id: str, extra: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{plugin_id}.sdPlugin/manifest.json", json.dumps({"Name": "Test", "Version": "1.0.0.0", "Author": "A"}))
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


class FakeNetwork(mirror.Network):
    def __init__(self, licenses=None, packages=None, icons=None, pages=None):
        super().__init__(token="x")
        self.licenses = licenses or {}
        self.packages = packages or {}
        self.icons = icons or {}
        self.pages = pages or {}
        self.downloaded = []
        self.read_pages = []

    def icon(self, plugin_id, limit):
        value = self.icons[plugin_id]
        if isinstance(value, Exception):
            raise value
        return value

    def marketplace_page(self, url):
        self.read_pages.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value

    def license(self, owner, repository):
        value = self.licenses[f"{owner}/{repository}"]
        if isinstance(value, Exception):
            raise value
        return value

    def package(self, plugin_id, limit):
        self.downloaded.append(plugin_id)
        data = self.packages[plugin_id]
        if isinstance(data, Exception):
            raise data
        if len(data) > limit:
            raise ValueError("too large")
        return data


def archive(plugin_id, link=REPOSITORY_LINK, version="1.0", name=None):
    return {"id": plugin_id, "name": name or plugin_id, "author": AUTHOR, "link": link, "version": version, "download": "https://x", "icon": "https://x"}


def run(network, catalogue, previous=None, blocked_ids=(), blocked_repositories=(), dry_run=False, limit=None, max_bytes=1024 * 1024,
        icons=(), listed_ids=()):
    log = []
    result = mirror.run(network, catalogue, previous=previous or {}, blocked_ids=set(blocked_ids), blocked_repositories=set(blocked_repositories),
                        max_bytes=max_bytes, repository=MIRROR_REPOSITORY, dry_run=dry_run, limit=limit, icons=set(icons),
                        listed_ids=set(listed_ids), report=log.append)
    return result + (log,)


def fixture_page():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def page(results, next_url=None):
    return {"count": len(results), "next": next_url, "previous": None, "results": results}


def fixture_products():
    return [mirror.normalize_marketplace(product) for product in fixture_page()["results"]]


def merge(entries, marketplace, blocked_ids=(), blocked_repositories=()):
    return mirror.merge(entries, marketplace, set(blocked_ids), set(blocked_repositories), report=lambda line: None)


def by_id(entries):
    return {entry["id"]: entry for entry in entries}


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


class RunSnapshotTests(unittest.TestCase):
    def test_run_plugin_listed_in_the_reviewed_catalog_is_left_out_and_its_copy_removed(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: MIT})

        # Act
        entries, _, stale, log = run(network, [archive(ONE)], previous=mirrored_before(), listed_ids={ONE})

        # Assert
        self.assertEqual(entries, [])
        self.assertEqual(stale, [f"{ONE}-1.0.zip"])
        self.assertTrue(any_line_starts_with(log, "live"))

    def test_run_archive_without_the_package_lists_the_plugin_with_no_copy_kept(self):
        # Arrange
        error = urllib.error.HTTPError("https://plugins.amankhanna.me", 404, "gone", {}, None)
        network = FakeNetwork({REPOSITORY: MIT}, {ONE: error})

        # Act
        entries, uploads, _, log = run(network, [archive(ONE)])

        # Assert
        self.assertTrue(has_no_download(entries[0]))
        self.assertEqual(uploads, {})
        self.assertTrue(any_line_contains(log, "no copy kept"))

    def test_run_icon_we_keep_is_served_from_our_pages(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: NON_COMMERCIAL})

        # Act
        entries, _, _, _ = run(network, [archive(ONE)], icons={mirror.icon_name(ONE)})

        # Assert
        self.assertEqual(entries[0]["icon"], PAGES_ICON)

    def test_run_icon_we_do_not_keep_is_none(self):
        # Arrange
        network = FakeNetwork({REPOSITORY: NON_COMMERCIAL})

        # Act
        entries, _, _, _ = run(network, [archive(ONE)])

        # Assert
        self.assertIsNone(entries[0]["icon"])


class ImportTests(unittest.TestCase):
    def test_import_snapshot_is_sorted_by_id_without_the_archive_download_and_icon(self):
        # Arrange
        network = FakeNetwork(icons={ONE: PNG, TWO: PNG})

        # Act
        entries, _ = mirror.import_snapshot(network, [archive(TWO), archive(ONE)], report=lambda line: None)

        # Assert
        self.assertEqual([entry["id"] for entry in entries], [ONE, TWO])
        self.assertEqual(set(entries[0]), set(mirror.SNAPSHOT_FIELDS))

    def test_import_snapshot_keeps_png_icons_and_skips_the_rest(self):
        # Arrange
        network = FakeNetwork(icons={ONE: PNG, TWO: b"<html>not an icon</html>"})

        # Act
        _, icons = mirror.import_snapshot(network, [archive(ONE), archive(TWO)], report=lambda line: None)

        # Assert
        self.assertEqual(icons, {mirror.icon_name(ONE): PNG})

    def test_import_snapshot_failed_icon_download_skips_only_that_icon(self):
        # Arrange
        network = FakeNetwork(icons={ONE: ValueError("larger than allowed"), TWO: PNG})

        # Act
        entries, icons = mirror.import_snapshot(network, [archive(ONE), archive(TWO)], report=lambda line: None)

        # Assert
        self.assertEqual(len(entries), 2)
        self.assertEqual(list(icons), [mirror.icon_name(TWO)])

    def test_write_import_writes_the_snapshot_and_the_icons(self):
        # Arrange
        entries = mirror.snapshot_entries([archive(ONE)])
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)

            # Act
            mirror.write_import(out, entries, {mirror.icon_name(ONE): PNG})

            # Assert
            self.assertEqual(json.loads((out / "snapshot.json").read_text(encoding="utf-8")), entries)
            self.assertEqual(mirror.load_icons(out), {mirror.icon_name(ONE)})


class NormalizeMarketplaceTests(unittest.TestCase):
    def test_normalize_marketplace_free_product_has_its_link_icon_platforms_and_source(self):
        # Arrange
        product = fixture_page()["results"][0]

        # Act
        entry = mirror.normalize_marketplace(product)

        # Assert
        self.assertEqual(entry["marketplace_url"], f"https://marketplace.elgato.com/product/one-{ONE_PRODUCT}")
        self.assertTrue(entry["icon"].startswith("https://mp-cdn.elgato.com/"))
        self.assertEqual(entry["platforms"], ["Windows", "macOS"])
        self.assertEqual(entry["source_repository"], REPOSITORY_LINK)
        self.assertEqual((entry["is_free"], entry["price"]), (True, None))

    def test_normalize_marketplace_paid_product_has_its_price(self):
        # Arrange
        product = fixture_page()["results"][1]

        # Act
        entry = mirror.normalize_marketplace(product)

        # Assert
        self.assertEqual((entry["is_free"], entry["price"]), (False, "€6.90"))
        self.assertIsNone(entry["source_repository"])

    def test_normalize_marketplace_product_without_a_slug_is_refused(self):
        # Arrange
        product = {"id": PAID_PRODUCT, "name": PAID_NAME}

        # Act
        act = lambda: mirror.normalize_marketplace(product)  # noqa: E731

        # Assert
        self.assertRaises(mirror.MarketplaceError, act)

    def test_marketplace_price_subscription_names_its_plan(self):
        # Arrange
        variant = {"price": {"currency": "USD", "plan": "month", "unit_amount": 299}}

        # Act
        price = mirror.marketplace_price(variant)

        # Assert
        self.assertEqual(price, "$2.99/month")


class ReadMarketplaceTests(unittest.TestCase):
    def test_read_marketplace_follows_next_until_the_last_page(self):
        # Arrange
        first, second = fixture_page()["results"]
        network = FakeNetwork(pages={FIRST_PAGE: page([first], SECOND_PAGE), SECOND_PAGE: page([second])})

        # Act
        products = mirror.read_marketplace(network, FIRST_PAGE)

        # Assert
        self.assertEqual([product["marketplace_id"] for product in products], [ONE_PRODUCT, PAID_PRODUCT])
        self.assertEqual(network.read_pages, [FIRST_PAGE, SECOND_PAGE])

    def test_read_marketplace_next_page_on_another_host_is_refused(self):
        # Arrange
        first = fixture_page()["results"][0]
        network = FakeNetwork(pages={FIRST_PAGE: page([first], ELSEWHERE_PAGE)})

        # Act
        act = lambda: mirror.read_marketplace(network, FIRST_PAGE)  # noqa: E731

        # Assert
        self.assertRaises(mirror.MarketplaceError, act)

    def test_read_marketplace_failed_later_page_refuses_the_partial_list(self):
        # Arrange
        first = fixture_page()["results"][0]
        network = FakeNetwork(pages={FIRST_PAGE: page([first], SECOND_PAGE), SECOND_PAGE: OSError("timed out")})

        # Act
        act = lambda: mirror.read_marketplace(network, FIRST_PAGE)  # noqa: E731

        # Assert
        self.assertRaises(mirror.MarketplaceError, act)

    def test_read_marketplace_page_of_another_shape_is_refused(self):
        # Arrange
        network = FakeNetwork(pages={FIRST_PAGE: {"items": []}})

        # Act
        act = lambda: mirror.read_marketplace(network, FIRST_PAGE)  # noqa: E731

        # Assert
        self.assertRaises(mirror.MarketplaceError, act)

    def test_read_marketplace_empty_list_is_refused(self):
        # Arrange
        network = FakeNetwork(pages={FIRST_PAGE: page([])})

        # Act
        act = lambda: mirror.read_marketplace(network, FIRST_PAGE)  # noqa: E731

        # Assert
        self.assertRaises(mirror.MarketplaceError, act)

    def test_read_marketplace_stops_after_the_page_limit(self):
        # Arrange
        first = fixture_page()["results"][0]
        network = FakeNetwork(pages={FIRST_PAGE: page([first], FIRST_PAGE)})

        # Act
        act = lambda: mirror.read_marketplace(network, FIRST_PAGE, max_pages=3)  # noqa: E731

        # Assert
        self.assertRaises(mirror.MarketplaceError, act)


class MergeTests(unittest.TestCase):
    def test_merge_marketplace_plugin_in_the_snapshot_keeps_the_copy_and_takes_the_marketplace_link(self):
        # Arrange
        entries, _, _, _ = run(FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE)}), [archive(ONE)])

        # Act
        merged = by_id(merge(entries, fixture_products()))

        # Assert
        entry = merged[ONE]
        self.assertTrue(entry["mirrored"])
        self.assertEqual(entry["download_version"], "1.0")
        self.assertEqual(entry["marketplace_url"], f"https://marketplace.elgato.com/product/one-{ONE_PRODUCT}")
        self.assertEqual(entry["source"], "marketplace")

    def test_merge_marketplace_only_plugin_is_listed_with_a_link_and_no_download(self):
        # Arrange
        entries = []

        # Act
        merged = by_id(merge(entries, fixture_products()))

        # Assert
        entry = merged[mirror.MARKETPLACE_ID_PREFIX + PAID_PRODUCT]
        self.assertTrue(has_no_download(entry))
        self.assertFalse(entry["mirrored"])
        self.assertEqual(entry["price"], "€6.90")

    def test_merge_snapshot_plugin_not_on_the_marketplace_stays_as_it_was(self):
        # Arrange
        entries, _, _, _ = run(FakeNetwork({OTHER_REPOSITORY: NON_COMMERCIAL}), [archive(TWO, link=OTHER_LINK)])

        # Act
        merged = by_id(merge(entries, fixture_products()))

        # Assert
        self.assertEqual(merged[TWO], entries[0])

    def test_merge_blocklisted_marketplace_plugin_is_hidden(self):
        # Arrange
        blocked = {mirror.MARKETPLACE_ID_PREFIX + PAID_PRODUCT}

        # Act
        merged = by_id(merge([], fixture_products(), blocked_ids=blocked))

        # Assert
        self.assertNotIn(mirror.MARKETPLACE_ID_PREFIX + PAID_PRODUCT, merged)

    def test_merge_marketplace_plugin_whose_repository_is_blocklisted_is_hidden(self):
        # Arrange
        blocked = {REPOSITORY}

        # Act
        merged = by_id(merge([], fixture_products(), blocked_repositories=blocked))

        # Assert
        self.assertNotIn(mirror.MARKETPLACE_ID_PREFIX + ONE_PRODUCT, merged)

    def test_run_started_from_a_merged_catalog_keeps_the_copy(self):
        # Arrange
        entries, _, _, _ = run(FakeNetwork({REPOSITORY: MIT}, {ONE: package(ONE)}), [archive(ONE)])
        previous = by_id(merge(entries, fixture_products()))
        network = FakeNetwork({REPOSITORY: MIT})

        # Act
        again, uploads, _, _ = run(network, [archive(ONE)], previous=previous)

        # Assert
        self.assertTrue(again[0]["mirrored"])
        self.assertEqual((uploads, network.downloaded), ({}, []))


class MarketplaceFallbackTests(unittest.TestCase):
    def test_load_marketplace_reads_the_list_written_by_the_last_run(self):
        # Arrange
        products = fixture_products()
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)
            mirror.write_output(out, [], {}, [], MIRROR_REPOSITORY, products)

            # Act
            loaded = mirror.load_marketplace(out)

            # Assert
            self.assertEqual(loaded, products)

    def test_load_marketplace_without_an_earlier_list_gives_none(self):
        # Arrange
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)

            # Act
            loaded = mirror.load_marketplace(out)

            # Assert
            self.assertIsNone(loaded)


class ListedIdsTests(unittest.TestCase):
    def test_load_listed_ids_reads_the_ids_in_lower_case(self):
        # Arrange
        with tempfile.TemporaryDirectory() as folder:
            directory = pathlib.Path(folder)
            (directory / "x.json").write_text(json.dumps({"id": "Com.A.One"}), encoding="utf-8")

            # Act
            ids = mirror.load_listed_ids(directory)

            # Assert
            self.assertEqual(ids, {ONE})


class PagesUrlTests(unittest.TestCase):
    def test_pages_url_owner_is_lower_case(self):
        # Arrange
        repository = "Me/plugins"

        # Act
        url = mirror.pages_url(repository)

        # Assert
        self.assertEqual(url, "https://me.github.io/plugins")


if __name__ == "__main__":
    unittest.main()
