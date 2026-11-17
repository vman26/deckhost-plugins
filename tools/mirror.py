#!/usr/bin/env python3
"""A mirror of the Elgato Marketplace archive for DeckHost: a catalog of every plugin, and a copy of the
package for each plugin whose source repository has a license that allows redistribution.

    python3 tools/mirror.py run --out DIR [--previous DIR] [--catalogue URL|FILE] [--blocklist FILE]
                                 [--dry-run] [--limit N] [--max-size MB]

Reads the archive's catalogue (https://plugins.amankhanna.me/catalogue.json by default). For each plugin:

  - on the blocklist (mirror/blocklist.json): never mirrored, listed without a package;
  - no GitHub repository in its link, or no license on it, or one that isn't on the allowlist
    (ALLOWED_LICENSES): listed without a package ("catalog only": name, author, version and the
    link to the Elgato Marketplace);
  - otherwise: its package (the archive's re-zipped copy) is downloaded, checked (a valid zip of at
    most --max-size MB, no paths outside the archive, a Stream Deck or DeckHost manifest with the same
    id), hashed and staged for upload as <id>-<version>.zip.

Writes DIR/catalog.json (what DeckHost reads), DIR/NOTICE.md (who and which license), DIR/upload/ (the
packages to attach to the rolling release) and DIR/remove.txt (assets of the previous run that no
longer belong: other versions, takedowns). --previous is the DIR of the last run: a plugin whose
version is already mirrored is kept as it is, so a package is never replaced under its checksum, and a
license lookup that merely failed keeps what was there. --dry-run says what would happen without
downloading packages or writing anything.

The GH_TOKEN (or GITHUB_TOKEN) environment variable raises GitHub's API limit.
"""

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from catalog import read_manifest  # noqa: E402  (the same package reader the catalog checks use)

ARCHIVE_CATALOGUE = "https://plugins.amankhanna.me/catalogue.json"
ARCHIVE_PACKAGE = "https://plugins.amankhanna.me/rezipped/{id}.zip"
ARCHIVE_ICON = "https://plugins.amankhanna.me/icons/{id}.png"
COMMUNITY_ARCHIVE = "https://github.com/nekename/ElgatoAppStoreArchive"
MARKETPLACE_SEARCH = "https://marketplace.elgato.com/search?q={name}"
RELEASE_TAG = "elgato-mirror"
DEFAULT_MAX_MB = 50

# SPDX ids (as GitHub reports them) of licenses that allow passing a copy on, with the license kept
# and the author named (NOTICE.md does both). Anything else, "Other" and no license included, is
# catalog only.
ALLOWED_LICENSES = frozenset({
    "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC", "Zlib", "Unlicense", "CC0-1.0",
    "MPL-2.0", "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-2.1", "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
})
GITHUB_REPOSITORY = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?].*)?$")


class Network:
    """The three things the mirror reads from the network; tests replace this."""

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    def _get(self, url: str, headers: dict | None = None, limit: int | None = None) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "deckhost-plugins-mirror", **(headers or {})})
        with urllib.request.urlopen(request, timeout=120) as response:
            if limit is None:
                return response.read()
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"larger than {limit} bytes")
            return data

    def catalogue(self, source: str) -> list:
        if re.match(r"^https?://", source):
            return json.loads(self._get(source))
        return json.loads(pathlib.Path(source).read_text(encoding="utf-8"))

    def license(self, owner: str, repository: str) -> str | None:
        """The SPDX id GitHub detects (None: no license, or one it can't name). Raises when the lookup fails."""
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            data = json.loads(self._get(f"https://api.github.com/repos/{owner}/{repository}/license", headers))
        except urllib.error.HTTPError as error:
            if error.code == 404:  # no license file (or no such repository)
                return None
            raise
        spdx = (data.get("license") or {}).get("spdx_id")
        return None if spdx in (None, "", "NOASSERTION") else spdx

    def package(self, plugin_id: str, limit: int) -> bytes:
        return self._get(ARCHIVE_PACKAGE.format(id=urllib.request.quote(plugin_id, safe="")), limit=limit)


def source_repository(link: str | None) -> tuple[str, str] | None:
    """(owner, repository) from the link of an archive entry when it points at a GitHub repository."""
    match = GITHUB_REPOSITORY.match((link or "").strip())
    return (match.group(1), match.group(2)) if match else None


def asset_name(plugin_id: str, version: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{plugin_id}-{version}") + ".zip"


def check_package(data: bytes, plugin_id: str) -> str | None:
    """Why this isn't a package to pass on (None: fine)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return "not a zip file"
    with archive:
        for name in archive.namelist():
            parts = name.replace("\\", "/").split("/")
            if name.startswith(("/", "\\")) or ".." in parts or re.match(r"^[A-Za-z]:", name):
                return f"a path outside the archive: {name}"
        if archive.testzip() is not None:
            return "a damaged file in the zip"
    try:
        manifest = read_manifest(data)
    except (ValueError, KeyError, UnicodeDecodeError):
        return "an unreadable manifest"
    if manifest is None:
        return "no plugin manifest"
    if manifest.get("id") != plugin_id:
        return f"the package is {manifest.get('id')}, not {plugin_id}"
    return None


def load_blocklist(path: pathlib.Path | None) -> tuple[set[str], set[str]]:
    if path is None or not path.exists():
        return set(), set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {value.lower() for value in data.get("ids", [])}, {value.lower() for value in data.get("repositories", [])}


def load_previous(directory: pathlib.Path | None) -> dict[str, dict]:
    if directory is None or not (directory / "catalog.json").exists():
        return {}
    return {entry["id"]: entry for entry in json.loads((directory / "catalog.json").read_text(encoding="utf-8"))}


def run(network: Network, catalogue: list, *, previous: dict[str, dict], blocked_ids: set[str], blocked_repositories: set[str],
        max_bytes: int, repository: str, dry_run: bool, limit: int | None, report=print) -> tuple[list[dict], dict[str, bytes], list[str]]:
    """The new catalog entries, the packages to upload (asset name → bytes) and the assets to remove."""
    entries: list[dict] = []
    uploads: dict[str, bytes] = {}
    downloads = 0

    for plugin in sorted((p for p in catalogue if isinstance(p, dict) and p.get("id")), key=lambda p: p["id"]):
        plugin_id = str(plugin["id"])
        version = str(plugin.get("version") or "")
        name = str(plugin.get("name") or plugin_id)
        entry = {
            "id": plugin_id,
            "name": name,
            "author": plugin.get("author"),
            "version": version,
            "icon": ARCHIVE_ICON.format(id=urllib.request.quote(plugin_id, safe="")),
            "marketplace_url": MARKETPLACE_SEARCH.format(name=urllib.request.quote(name)),
            "license": None,
            "source_repository": None,
            "sha256": None,
            "download_url": None,
            "mirrored": False,
        }
        source = source_repository(plugin.get("link"))
        before = previous.get(plugin_id)
        if source:
            entry["source_repository"] = f"https://github.com/{source[0]}/{source[1]}"

        # A plugin only gets a package when nothing speaks against it; the reason is reported.
        reason = None
        if plugin_id.lower() in blocked_ids or (source and f"{source[0]}/{source[1]}".lower() in blocked_repositories):
            reason = "on the blocklist"
        elif not source:
            reason = "no GitHub repository to read a license from"
        else:
            try:
                entry["license"] = network.license(*source)
            except Exception as error:  # a failed lookup is not "no license": keep what was mirrored
                if before and before.get("mirrored") and before.get("version") == version:
                    report(f"keep  {plugin_id} {version}: license lookup failed ({error}), the earlier copy stays")
                    entries.append(before)
                    continue
                reason = f"license lookup failed ({error})"
            else:
                if entry["license"] is None:
                    reason = "no license"
                elif entry["license"] not in ALLOWED_LICENSES:
                    reason = f"license {entry['license']} isn't on the allowlist"

        if reason is None and before and before.get("mirrored") and before.get("version") == version and before.get("sha256"):
            entry.update({key: before[key] for key in ("sha256", "download_url")} | {"mirrored": True})
            report(f"have  {plugin_id} {version} ({entry['license']})")
        elif reason is None:
            if limit is not None and downloads >= limit:
                report(f"later {plugin_id} {version}: this run's limit of {limit} packages is reached")
            elif dry_run:
                report(f"would mirror {plugin_id} {version} ({entry['license']})")
            else:
                downloads += 1
                try:
                    data = network.package(plugin_id, max_bytes)
                except Exception as error:
                    report(f"skip  {plugin_id} {version}: download failed ({error})")
                else:
                    problem = check_package(data, plugin_id)
                    if problem:
                        report(f"skip  {plugin_id} {version}: {problem}")
                    else:
                        asset = asset_name(plugin_id, version)
                        uploads[asset] = data
                        entry["sha256"] = hashlib.sha256(data).hexdigest()
                        entry["download_url"] = f"https://github.com/{repository}/releases/download/{RELEASE_TAG}/{asset}"
                        entry["mirrored"] = True
                        report(f"new   {plugin_id} {version} ({entry['license']}, {len(data) // 1024} KiB)")
        else:
            report(f"list  {plugin_id} {version}: {reason}")
        entries.append(entry)

    wanted = {entry["download_url"].rsplit("/", 1)[-1] for entry in entries if entry.get("download_url")}
    stale = sorted({entry["download_url"].rsplit("/", 1)[-1] for entry in previous.values() if entry.get("download_url")} - wanted)
    return entries, uploads, stale


def notice(entries: list[dict], repository: str) -> str:
    mirrored = [entry for entry in entries if entry["mirrored"]]
    lines = [
        "# Elgato plugin mirror",
        "",
        "Copies of Stream Deck plugins from the Elgato Marketplace, kept for DeckHost's plugin store.",
        "",
        "We are not the authors of these plugins and are not affiliated with Elgato. Each package is the",
        f"copy kept by the community archive ({COMMUNITY_ARCHIVE}) of what Elgato's servers served,",
        "passed on unchanged apart from the checks below, and stored with its SHA-256. Every plugin keeps",
        "its own license and its author, named in the table.",
        "",
        "A plugin is copied only when its source repository has an open-source license that allows",
        "passing it on. The others are listed with a link to the Elgato Marketplace and are not copied.",
        "",
        f"Don't want a plugin here? Open an issue in https://github.com/{repository} (or send a pull request",
        "adding its id to `mirror/blocklist.json`). It is removed from the mirror, and its package deleted,",
        "by the next run (weekly) or at once when a maintainer runs the workflow.",
        "",
        f"{len(mirrored)} of {len(entries)} plugins are copied:",
        "",
        "| Plugin | Author | Version | License | Source |",
        "|---|---|---|---|---|",
    ]
    for entry in sorted(mirrored, key=lambda e: e["name"].lower()):
        cells = [entry["name"], entry["author"] or "", entry["version"], entry["license"], entry["source_repository"] or ""]
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def write_output(directory: pathlib.Path, entries: list[dict], uploads: dict[str, bytes], stale: list[str], repository: str) -> None:
    (directory / "upload").mkdir(parents=True, exist_ok=True)
    for asset, data in uploads.items():
        (directory / "upload" / asset).write_bytes(data)
    (directory / "catalog.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (directory / "NOTICE.md").write_text(notice(entries, repository), encoding="utf-8")
    (directory / "remove.txt").write_text("".join(f"{asset}\n" for asset in stale), encoding="utf-8")


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run")
    command.add_argument("--out", type=pathlib.Path)
    command.add_argument("--previous", type=pathlib.Path)
    command.add_argument("--catalogue", default=ARCHIVE_CATALOGUE)
    command.add_argument("--blocklist", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent.parent / "mirror" / "blocklist.json")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--limit", type=int)
    command.add_argument("--max-size", type=int, default=DEFAULT_MAX_MB, help="megabytes")
    args = parser.parse_args(arguments)
    if not args.dry_run and args.out is None:
        parser.error("--out is required (unless --dry-run)")

    repository = os.environ.get("GITHUB_REPOSITORY", "vman26/deckhost-plugins")
    network = Network()
    blocked_ids, blocked_repositories = load_blocklist(args.blocklist)
    entries, uploads, stale = run(
        network, network.catalogue(args.catalogue), previous=load_previous(args.previous), blocked_ids=blocked_ids,
        blocked_repositories=blocked_repositories, max_bytes=args.max_size * 1024 * 1024, repository=repository,
        dry_run=args.dry_run, limit=args.limit)
    mirrored = sum(1 for entry in entries if entry["mirrored"])
    print(f"{len(entries)} plugins, {mirrored} mirrored, {len(uploads)} new packages, {len(stale)} to remove")
    if not args.dry_run:
        write_output(args.out, entries, uploads, stale, repository)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
