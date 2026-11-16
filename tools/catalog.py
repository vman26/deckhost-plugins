#!/usr/bin/env python3
"""Checks plugin entries and builds catalog.json.

    python3 tools/catalog.py check [files…]       # the entries (all when none are given)
    python3 tools/catalog.py download <file> <dir> # download the package, check sha256, id and version
    python3 tools/catalog.py build <output.json>    # merge every entry into the catalog DeckHost reads
    python3 tools/catalog.py from-release <github repository URL>
                                                  # write plugins/<id>.json from its newest release
    python3 tools/catalog.py discover [topics…]    # entries for GitHub repositories with these topics
                                                  # that aren't listed yet (default: see TOPICS)

GitHub requests use the GH_TOKEN (or GITHUB_TOKEN) environment variable when it's set.
"""

import hashlib
import io
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
SHA = re.compile(r"^[0-9a-fA-F]{64}$")
REQUIRED = ["id", "name", "author", "description", "version"]
KNOWN = set(REQUIRED) | {"category", "repository", "download_url", "sha256", "icon", "page", "permissions", "license", "purchase_url", "price"}
PERMISSIONS = {"network", "processes", "files", "input", "system", "clipboard"}
TOPICS = ["deckhost-plugin", "streamdeck-plugin", "stream-deck-plugin", "openaction-plugin"]
PACKAGE_SUFFIXES = (".streamdeckplugin", ".dhplugin", ".zip")


def load(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def problems(path: pathlib.Path) -> list[str]:
    try:
        entry = load(path)
    except json.JSONDecodeError as error:
        return [f"not valid JSON: {error}"]
    if not isinstance(entry, dict):
        return ["the file must hold one JSON object"]

    found = []
    for key in REQUIRED:
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            found.append(f"'{key}' is missing")
    for key in entry:
        if key not in KNOWN:
            found.append(f"unknown field '{key}'")
    plugin_id = entry.get("id", "")
    if not ID.match(plugin_id):
        found.append("'id' must be 3-64 characters: lowercase letters, digits, '.', '-' or '_'")
    if path.stem != plugin_id:
        found.append(f"the file must be named {plugin_id}.json")
    for key in ("repository", "download_url", "icon", "page", "purchase_url"):
        value = entry.get(key)
        if value is not None and not (isinstance(value, str) and value.startswith("https://")):
            found.append(f"'{key}' must be an https:// address")
    paid = "purchase_url" in entry
    if "download_url" in entry:
        if not SHA.match(str(entry.get("sha256", ""))):
            found.append("'sha256' (64 hex characters) is required with 'download_url'")
    elif not paid:
        found.append("give 'download_url' and 'sha256', or 'purchase_url' for a paid plugin")
    for permission in entry.get("permissions", []):
        if permission not in PERMISSIONS:
            found.append(f"unknown permission '{permission}' (known: {', '.join(sorted(PERMISSIONS))})")
    return found


def check(paths: list[pathlib.Path]) -> int:
    failed = 0
    for path in paths:
        issues = problems(path)
        print(f"{'FAIL' if issues else 'ok  '} {path.relative_to(ROOT)}")
        for issue in issues:
            print(f"     - {issue}")
        failed += bool(issues)
    return 1 if failed else 0


def download(path: pathlib.Path, directory: pathlib.Path) -> int:
    entry = load(path)
    if "download_url" not in entry:
        print(f"{entry['id']}: paid plugin, nothing to download")
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{entry['id']}.zip"
    request = urllib.request.Request(entry["download_url"], headers={"User-Agent": "deckhost-plugins-check"})
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as file:
        file.write(response.read())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest.lower() != entry["sha256"].lower():
        print(f"FAIL {entry['id']}: sha256 is {digest}, the entry says {entry['sha256']}")
        return 1
    manifest = read_manifest(target.read_bytes())
    if manifest is None:
        print(f"FAIL {entry['id']}: the package has no plugin.json at its root and no <id>.sdPlugin/manifest.json")
        return 1
    if manifest["id"] != entry["id"] or manifest["version"] != entry["version"]:
        print(f"FAIL {entry['id']}: the package says {manifest['id']} {manifest['version']}, the entry {entry['id']} {entry['version']}")
        return 1
    print(f"ok   {entry['id']} {entry['version']}: checksum, id and version match ({target})")
    return 0


def read_manifest(package: bytes) -> dict | None:
    """id, name, version, author and description from a DeckHost package (plugin.json at the root)
    or a Stream Deck / OpenAction / StreamDock package (<id>.sdPlugin/manifest.json)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile:
        return None
    with archive:
        names = archive.namelist()
        if "plugin.json" in names:
            data = json.loads(archive.read("plugin.json").decode("utf-8-sig"))
            return {key: data.get(key) for key in ("id", "name", "version", "author", "description")} | {"permissions": data.get("permissions") or []}
        for name in names:
            parts = name.split("/")
            if len(parts) == 2 and parts[0].lower().endswith(".sdplugin") and parts[1].lower() == "manifest.json":
                data = json.loads(archive.read(name).decode("utf-8-sig"))
                return {
                    "id": parts[0][: -len(".sdPlugin")],
                    "name": data.get("Name"),
                    "version": data.get("Version"),
                    "author": data.get("Author"),
                    "description": data.get("Description"),
                    "permissions": [],
                }
    return None


def github(path: str):
    """GET api.github.com/<path> as JSON (with GH_TOKEN / GITHUB_TOKEN when set)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "deckhost-plugins"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def newest_package(owner: str, repository: str):
    """(release, asset) of the newest non-draft release with a plugin file (stable first)."""
    releases = [release for release in github(f"repos/{owner}/{repository}/releases?per_page=20") if not release.get("draft")]
    for release in [r for r in releases if not r.get("prerelease")] + [r for r in releases if r.get("prerelease")]:
        assets = sorted(
            (asset for asset in release.get("assets", []) if asset["name"].lower().endswith(PACKAGE_SUFFIXES)),
            key=lambda asset: [suffix for suffix in PACKAGE_SUFFIXES if asset["name"].lower().endswith(suffix)][0] != ".streamdeckplugin",
        )
        if assets:
            return release, assets[0]
    return None, None


def entry_from_release(url: str) -> dict:
    """A catalog entry for a GitHub repository's newest release: the package's own manifest gives
    the id, name, version, author and description; the download gives the sha256."""
    match = re.match(r"^https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?(?:[#?].*)?$", url.strip())
    if not match:
        raise ValueError(f"{url} isn't a GitHub repository address (https://github.com/owner/repository)")
    owner, repository = match.groups()
    release, asset = newest_package(owner, repository)
    if asset is None:
        raise ValueError(f"{owner}/{repository} has no release with a .streamDeckPlugin, .dhplugin or .zip file")
    request = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "deckhost-plugins"})
    with urllib.request.urlopen(request, timeout=300) as response:
        package = response.read()
    manifest = read_manifest(package)
    if manifest is None or not manifest.get("id"):
        raise ValueError(f"{asset['name']} isn't a DeckHost or Stream Deck plugin package")
    repo = github(f"repos/{owner}/{repository}")
    entry = {
        "id": manifest["id"].lower(),
        "name": manifest.get("name") or repository,
        "author": manifest.get("author") or owner,
        "description": manifest.get("description") or repo.get("description") or "",
        "version": manifest.get("version") or release["tag_name"].lstrip("vV"),
        "repository": f"https://github.com/{owner}/{repository}",
        "download_url": asset["browser_download_url"],
        "sha256": hashlib.sha256(package).hexdigest(),
    }
    if manifest.get("permissions"):
        entry["permissions"] = manifest["permissions"]
    if (repo.get("license") or {}).get("spdx_id") not in (None, "NOASSERTION"):
        entry["license"] = repo["license"]["spdx_id"]
    return entry


def from_release(url: str) -> int:
    entry = entry_from_release(url)
    path = PLUGINS / f"{entry['id']}.json"
    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}: {entry['name']} {entry['version']}")
    return check([path])


def discover(topics: list[str]) -> int:
    """Entries for repositories with plugin topics and a plugin file in a release, not listed yet."""
    listed = {load(path).get("repository", "").lower() for path in PLUGINS.glob("*.json")}
    written = 0
    for topic in topics:
        query = urllib.parse.quote(f"topic:{topic} archived:false")
        for repo in github(f"search/repositories?q={query}&sort=updated&per_page=50").get("items", []):
            url = repo["html_url"]
            if url.lower() in listed:
                continue
            listed.add(url.lower())
            try:
                entry = entry_from_release(url)
            except (ValueError, OSError, json.JSONDecodeError, KeyError) as error:
                print(f"skip {url}: {error}")
                continue
            path = PLUGINS / f"{entry['id']}.json"
            if path.exists():
                print(f"skip {url}: {entry['id']} is already listed")
                continue
            path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"new  {entry['id']} {entry['version']} from {url}")
            written += 1
    print(f"{written} new plugin(s)")
    return 0


def build(output: pathlib.Path) -> int:
    paths = sorted(PLUGINS.glob("*.json"))
    if check(paths):
        return 1
    catalog = {}
    for path in paths:
        entry = load(path)
        catalog[entry.pop("id")] = entry
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(catalog)} plugins to {output}")
    return 0


def main(arguments: list[str]) -> int:
    if not arguments:
        print(__doc__)
        return 2
    command, rest = arguments[0], arguments[1:]
    if command == "check":
        return check([pathlib.Path(item).resolve() for item in rest] or sorted(PLUGINS.glob("*.json")))
    if command == "download" and len(rest) == 2:
        return download(pathlib.Path(rest[0]).resolve(), pathlib.Path(rest[1]))
    if command == "build" and len(rest) == 1:
        return build(pathlib.Path(rest[0]))
    if command == "from-release" and len(rest) == 1:
        try:
            return from_release(rest[0])
        except (ValueError, OSError) as error:
            print(f"FAIL {error}")
            return 1
    if command == "discover":
        return discover(rest or TOPICS)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
