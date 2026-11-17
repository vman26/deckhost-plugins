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


def archive(plugin_id, link="https://github.com/o/r", version="1.0", name=None):
    return {"id": plugin_id, "name": name or plugin_id, "author": "Someone", "link": link, "version": version, "download": "https://x", "icon": "https://x"}


def run(network, catalogue, previous=None, blocked_ids=(), blocked_repositories=(), dry_run=False, limit=None, max_bytes=1024 * 1024):
    log = []
    result = mirror.run(network, catalogue, previous=previous or {}, blocked_ids=set(blocked_ids), blocked_repositories=set(blocked_repositories),
                        max_bytes=max_bytes, repository="me/plugins", dry_run=dry_run, limit=limit, report=log.append)
    return result + (log,)


class MirrorTests(unittest.TestCase):
    def test_an_allowed_license_mirrors_the_package_with_its_checksum(self):
        data = package("com.a.one")
        entries, uploads, stale, _ = run(FakeNetwork({"o/r": "MIT"}, {"com.a.one": data}), [archive("com.a.one")])
        entry = entries[0]
        self.assertTrue(entry["mirrored"])
        self.assertEqual(entry["license"], "MIT")
        self.assertEqual(entry["download_url"], "https://github.com/me/plugins/releases/download/elgato-mirror/com.a.one-1.0.zip")
        self.assertEqual(len(entry["sha256"]), 64)
        self.assertEqual(uploads, {"com.a.one-1.0.zip": data})
        self.assertEqual(stale, [])

    def test_other_licenses_and_unknown_sources_stay_catalog_only(self):
        network = FakeNetwork({"o/r": "CC-BY-NC-4.0", "o/none": None}, {})
        entries, uploads, _, log = run(network, [
            archive("a.nc"), archive("b.none", link="https://github.com/o/none"), archive("c.site", link="https://example.com/plugin")])
        self.assertEqual([e["mirrored"] for e in entries], [False, False, False])
        self.assertEqual(uploads, {})
        self.assertEqual(network.downloaded, [])
        self.assertTrue(all(e["download_url"] is None and e["sha256"] is None for e in entries))
        self.assertEqual(entries[0]["license"], "CC-BY-NC-4.0")
        self.assertTrue(any("allowlist" in line for line in log) and any("no license" in line for line in log))
        # Everything keeps its Marketplace link so the store can still point at it.
        self.assertTrue(all(e["marketplace_url"].startswith("https://marketplace.elgato.com/") for e in entries))

    def test_the_blocklist_wins_over_a_good_license(self):
        network = FakeNetwork({"o/r": "MIT", "x/y": "MIT"}, {"com.a.one": package("com.a.one"), "com.a.two": package("com.a.two")})
        entries, uploads, _, _ = run(network, [archive("com.a.one"), archive("com.a.two", link="https://github.com/x/y")],
                                     blocked_ids={"com.a.one"}, blocked_repositories={"X/Y".lower()})
        self.assertEqual([e["mirrored"] for e in entries], [False, False])
        self.assertEqual(uploads, {})
        self.assertEqual(network.downloaded, [])

    def test_a_package_that_is_wrong_is_not_mirrored(self):
        evil = package("com.a.evil", {"../escape.txt": "x"})
        other = package("com.a.other")
        network = FakeNetwork({"o/r": "MIT"}, {"com.a.evil": evil, "com.a.other": other, "com.a.bad": b"not a zip", "com.a.big": package("com.a.big", {"f": "x" * 5000})})
        entries, uploads, _, log = run(network, [archive("com.a.evil"), archive("com.a.other", version="2"), archive("com.a.bad"), archive("com.a.big")], max_bytes=2000)
        self.assertEqual({e["id"]: e["mirrored"] for e in entries}, {"com.a.bad": False, "com.a.big": False, "com.a.evil": False, "com.a.other": True})
        self.assertEqual(list(uploads), ["com.a.other-2.zip"])
        self.assertTrue(any("outside the archive" in line for line in log))
        self.assertTrue(any("not a zip" in line for line in log))
        self.assertTrue(any("download failed" in line for line in log))

    def test_the_package_must_be_the_plugin_the_catalogue_names(self):
        network = FakeNetwork({"o/r": "MIT"}, {"com.a.one": package("com.a.two")})
        entries, uploads, _, log = run(network, [archive("com.a.one")])
        self.assertFalse(entries[0]["mirrored"])
        self.assertEqual(uploads, {})
        self.assertTrue(any("not com.a.one" in line for line in log))

    def test_a_version_already_mirrored_is_kept_and_not_downloaded_again(self):
        network = FakeNetwork({"o/r": "MIT"}, {})
        before = {"com.a.one": {"id": "com.a.one", "version": "1.0", "mirrored": True, "sha256": "a" * 64, "download_url": "https://github.com/me/plugins/releases/download/elgato-mirror/com.a.one-1.0.zip"}}
        entries, uploads, stale, _ = run(network, [archive("com.a.one")], previous=before)
        self.assertTrue(entries[0]["mirrored"])
        self.assertEqual(entries[0]["sha256"], "a" * 64)
        self.assertEqual(network.downloaded, [])
        self.assertEqual((uploads, stale), ({}, []))

    def test_a_new_version_replaces_the_old_asset(self):
        network = FakeNetwork({"o/r": "MIT"}, {"com.a.one": package("com.a.one")})
        before = {"com.a.one": {"id": "com.a.one", "version": "1.0", "mirrored": True, "sha256": "a" * 64, "download_url": "https://github.com/me/plugins/releases/download/elgato-mirror/com.a.one-1.0.zip"}}
        entries, uploads, stale, _ = run(network, [archive("com.a.one", version="1.1")], previous=before)
        self.assertEqual(entries[0]["version"], "1.1")
        self.assertEqual(list(uploads), ["com.a.one-1.1.zip"])
        self.assertEqual(stale, ["com.a.one-1.0.zip"])

    def test_a_license_that_is_lost_or_a_blocklisting_takes_the_package_down(self):
        before = {"com.a.one": {"id": "com.a.one", "version": "1.0", "mirrored": True, "sha256": "a" * 64, "download_url": "https://github.com/me/plugins/releases/download/elgato-mirror/com.a.one-1.0.zip"}}
        entries, uploads, stale, _ = run(FakeNetwork({"o/r": "LicenseRef-Proprietary"}, {}), [archive("com.a.one")], previous=before)
        self.assertFalse(entries[0]["mirrored"])
        self.assertIsNone(entries[0]["download_url"])
        self.assertEqual(stale, ["com.a.one-1.0.zip"])
        entries, _, stale, _ = run(FakeNetwork({"o/r": "MIT"}, {}), [archive("com.a.one")], previous=before, blocked_ids={"com.a.one"})
        self.assertFalse(entries[0]["mirrored"])
        self.assertEqual(stale, ["com.a.one-1.0.zip"])

    def test_a_failed_license_lookup_does_not_take_down_what_was_mirrored(self):
        error = urllib.error.HTTPError("https://api.github.com", 403, "rate limited", {}, None)
        before = {"com.a.one": {"id": "com.a.one", "version": "1.0", "mirrored": True, "sha256": "a" * 64, "download_url": "https://github.com/me/plugins/releases/download/elgato-mirror/com.a.one-1.0.zip"}}
        entries, _, stale, _ = run(FakeNetwork({"o/r": error}, {}), [archive("com.a.one")], previous=before)
        self.assertEqual(entries[0], before["com.a.one"])
        self.assertEqual(stale, [])
        # With nothing mirrored yet the plugin is just listed, and tried again next run.
        entries, uploads, _, _ = run(FakeNetwork({"o/r": error}, {}), [archive("com.a.two")])
        self.assertFalse(entries[0]["mirrored"])
        self.assertEqual(uploads, {})

    def test_a_dry_run_downloads_nothing(self):
        network = FakeNetwork({"o/r": "MIT"}, {})
        entries, uploads, _, log = run(network, [archive("com.a.one")], dry_run=True)
        self.assertEqual(network.downloaded, [])
        self.assertEqual(uploads, {})
        self.assertFalse(entries[0]["mirrored"])
        self.assertTrue(any(line.startswith("would mirror") for line in log))

    def test_the_limit_spreads_the_first_run_over_several(self):
        network = FakeNetwork({"o/r": "MIT"}, {f"com.a.{i}": package(f"com.a.{i}") for i in range(3)})
        entries, uploads, _, _ = run(network, [archive(f"com.a.{i}") for i in range(3)], limit=2)
        self.assertEqual(len(uploads), 2)
        self.assertEqual(sum(e["mirrored"] for e in entries), 2)

    def test_the_output_is_stable_and_complete(self):
        data = package("com.a.one")
        entries, uploads, stale, _ = run(FakeNetwork({"o/r": "MIT"}, {"com.a.one": data}), [archive("com.a.two", link="https://x.example"), archive("com.a.one")])
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)
            mirror.write_output(out, entries, uploads, stale, "me/plugins")
            catalog = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
            self.assertEqual([e["id"] for e in catalog], ["com.a.one", "com.a.two"])
            self.assertEqual((out / "upload" / "com.a.one-1.0.zip").read_bytes(), data)
            notice = (out / "NOTICE.md").read_text(encoding="utf-8")
            self.assertIn("1 of 2 plugins are copied", notice)
            self.assertIn("| com.a.one | Someone | 1.0 | MIT | https://github.com/o/r |", notice)
            self.assertNotIn("com.a.two |", notice)
            # The next run starts from this folder and changes nothing.
            again, again_uploads, again_stale, _ = run(FakeNetwork({"o/r": "MIT"}, {}), [archive("com.a.two", link="https://x.example"), archive("com.a.one")], previous=mirror.load_previous(out))
            self.assertEqual(again, entries)
            self.assertEqual((again_uploads, again_stale), ({}, []))

    def test_repository_links(self):
        self.assertEqual(mirror.source_repository("https://github.com/BarRaider/streamdeck-delayedtext"), ("BarRaider", "streamdeck-delayedtext"))
        self.assertEqual(mirror.source_repository("https://github.com/o/r.git"), ("o", "r"))
        self.assertEqual(mirror.source_repository("https://github.com/o/r/tree/main/plugin"), ("o", "r"))
        self.assertIsNone(mirror.source_repository("https://barraider.github.io/"))
        self.assertIsNone(mirror.source_repository(None))

    def test_asset_names_are_safe(self):
        self.assertEqual(mirror.asset_name("com.a.one", "1.0+beta 2"), "com.a.one-1.0_beta_2.zip")

    def test_the_allowlist_has_no_non_commercial_or_unnamed_licenses(self):
        for spdx in ("CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "NOASSERTION", "Other", "LicenseRef-Proprietary"):
            self.assertNotIn(spdx, mirror.ALLOWED_LICENSES)

    def test_the_blocklist_file_is_read_in_lower_case(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "blocklist.json"
            path.write_text(json.dumps({"ids": ["Com.A.One"], "repositories": ["O/R"]}), encoding="utf-8")
            self.assertEqual(mirror.load_blocklist(path), ({"com.a.one"}, {"o/r"}))
            self.assertEqual(mirror.load_blocklist(pathlib.Path(folder) / "none.json"), (set(), set()))
        # The one in the repository parses.
        mirror.load_blocklist(pathlib.Path(__file__).resolve().parent.parent / "mirror" / "blocklist.json")


if __name__ == "__main__":
    unittest.main()
