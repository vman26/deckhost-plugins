#!/usr/bin/env python3
"""Checks plugin entries and builds catalog.json.

    python3 tools/catalog.py check [files…]       # the entries (all when none are given)
    python3 tools/catalog.py download <file> <dir> # download the package, check sha256, id and version
    python3 tools/catalog.py build <output.json> [--overrides DIR]
                                                  # merge every entry (and newer versions from the
                                                  # update workflow) into the catalog DeckHost reads
    python3 tools/catalog.py from-release <github repository URL>
                                                  # write plugins/<id>.json from its newest release
    python3 tools/catalog.py discover [topics…]    # entries for GitHub repositories with these topics
                                                  # that aren't listed yet (default: see TOPICS)
    python3 tools/catalog.py update [ids…] [--approve id,id] [--overrides DIR]
                                                  # new releases of listed plugins, written to DIR
                                                  # (the catalog-updates branch) when nothing needs
                                                  # a review (see update())

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
KNOWN = set(REQUIRED) | {"category", "repository", "download_url", "sha256", "icon", "page", "permissions", "license", "purchase_url", "price", "auto_update"}
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
    if "auto_update" in entry and not isinstance(entry["auto_update"], bool):
        found.append("'auto_update' must be true or false")
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


def newest_package(owner: str, repository: str, plugin_id: str | None = None):
    """(release, asset) of the newest non-draft release with a plugin file (stable first). A file
    named after the plugin id comes first, then .streamDeckPlugin, .dhplugin, .zip."""
    releases = [release for release in github(f"repos/{owner}/{repository}/releases?per_page=20") if not release.get("draft")]
    for release in [r for r in releases if not r.get("prerelease")] + [r for r in releases if r.get("prerelease")]:
        assets = sorted(
            (asset for asset in release.get("assets", []) if asset["name"].lower().endswith(PACKAGE_SUFFIXES)),
            key=lambda asset: (
                not (plugin_id and plugin_id.lower() in asset["name"].lower()),
                PACKAGE_SUFFIXES.index([suffix for suffix in PACKAGE_SUFFIXES if asset["name"].lower().endswith(suffix)][0]),
            ),
        )
        if assets:
            return release, assets[0]
    return None, None


def entry_from_release(url: str, plugin_id: str | None = None) -> dict:
    """A catalog entry for a GitHub repository's newest release: the package's own manifest gives
    the id, name, version, author and description; the download gives the sha256."""
    match = re.match(r"^https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?(?:[#?].*)?$", url.strip())
    if not match:
        raise ValueError(f"{url} isn't a GitHub repository address (https://github.com/owner/repository)")
    owner, repository = match.groups()
    release, asset = newest_package(owner, repository, plugin_id)
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


SEARCH_PAGE_SIZE = 100
SEARCH_MAX_PAGES = 10  # GitHub's search returns at most 1000 results


def search_repositories(query: str):
    """Every repository the search finds, page by page (newest updates first)."""
    quoted = urllib.parse.quote(query)
    for page in range(1, SEARCH_MAX_PAGES + 1):
        items = github(f"search/repositories?q={quoted}&sort=updated&per_page={SEARCH_PAGE_SIZE}&page={page}").get("items", [])
        yield from items
        if len(items) < SEARCH_PAGE_SIZE:
            return


def discover(topics: list[str]) -> int:
    """Entries for repositories with plugin topics and a plugin file in a release, not listed yet."""
    listed = {load(path).get("repository", "").lower() for path in PLUGINS.glob("*.json")}
    written = 0
    for topic in topics:
        for repo in search_repositories(f"topic:{topic} archived:false"):
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
            issues = problems(path)
            if issues:
                # Leave out what the check workflow would reject (an empty description, a bad id):
                # one such entry would fail the whole pull request.
                path.unlink()
                print(f"skip {url}: {'; '.join(issues)}")
                continue
            print(f"new  {entry['id']} {entry['version']} from {url}")
            written += 1
    print(f"{written} new plugin(s)")
    return 0


def compare_versions(left: str, right: str) -> int:
    """Like DeckHost's PluginVersion.Compare: numbers part by part, a pre-release below its release,
    build metadata ignored. Negative when left is older."""
    def parse(version):
        text = (version or "").strip().lstrip("vV").split("+", 1)[0]
        core, _, label = text.partition("-")
        numbers = [int(re.match(r"\d*", part).group() or 0) for part in core.split(".")]
        return numbers, label or None

    (left_numbers, left_label), (right_numbers, right_label) = parse(left), parse(right)
    width = max(len(left_numbers), len(right_numbers))
    left_numbers += [0] * (width - len(left_numbers))
    right_numbers += [0] * (width - len(right_numbers))
    if left_numbers != right_numbers:
        return -1 if left_numbers < right_numbers else 1
    if left_label == right_label:
        return 0
    if left_label is None or right_label is None:
        return 1 if left_label is None else -1
    for a, b in zip(left_label.split("."), right_label.split(".")):
        if a != b:
            if a.isdigit() and b.isdigit():
                return -1 if int(a) < int(b) else 1
            return -1 if a < b else 1
    return (len(left_label.split(".")) > len(right_label.split("."))) - (len(left_label.split(".")) < len(right_label.split(".")))


UPDATE_FIELDS = ("version", "download_url", "sha256", "name", "author", "description", "permissions")
DEFAULT_OVERRIDES = ROOT / "overrides" / "updates"


def with_override(entry: dict, overrides: pathlib.Path | None) -> dict:
    """The entry as listed: the reviewed file from main, with a newer version from the update
    workflow (overrides/<id>.json) when there is one. A version on main that is as new or newer wins."""
    if overrides is None:
        return entry
    path = overrides / f"{entry['id']}.json"
    if not path.exists():
        return entry
    override = load(path)
    if compare_versions(override.get("version", ""), entry.get("version", "")) <= 0:
        return entry
    return entry | {key: override[key] for key in UPDATE_FIELDS if key in override}


def update(ids: list[str], approved: set[str] | None = None, overrides: pathlib.Path = DEFAULT_OVERRIDES) -> int:
    """New releases of listed plugins, from the repository the entry was reviewed with.

    main is protected (pull requests with a review), so a new version doesn't change the entry
    there: it is written to `overrides/<id>.json` (the catalog-updates branch), which `build`
    applies on top. That happens when the newest release has the same plugin id, a higher version
    and no permission the entry doesn't already list; the version, download_url and sha256 come
    with it, and name, author and description from the package's own manifest. Anything else is
    reported as needing a review. Entries with "auto_update": false, paid plugins and plugins not on
    GitHub are skipped. Ids in `approved` skip the version and permission rules (a maintainer looked
    at them); a different plugin id is never taken. Overrides that main has caught up with are
    removed. Writes update-report.json (updated / review) for the workflow."""
    approved = approved or set()
    overrides.mkdir(parents=True, exist_ok=True)
    report = {"updated": [], "review": []}
    for path in sorted(PLUGINS.glob("*.json")):
        reviewed = load(path)
        stale = overrides / f"{reviewed['id']}.json"
        if stale.exists() and compare_versions(load(stale).get("version", ""), reviewed.get("version", "")) <= 0:
            stale.unlink()
            print(f"main has {reviewed['id']} {reviewed['version']}: dropped the older override")
        entry = with_override(reviewed, overrides)
        if ids and entry["id"] not in ids:
            continue
        repository = entry.get("repository", "")
        if entry.get("auto_update") is False or "download_url" not in entry or not repository.startswith("https://github.com/"):
            continue
        try:
            fresh = entry_from_release(repository, entry["id"])
        except (ValueError, OSError, json.JSONDecodeError, KeyError) as error:
            print(f"skip {entry['id']}: {error}")
            continue
        if fresh["download_url"] == entry["download_url"] and fresh["sha256"].lower() == entry["sha256"].lower():
            continue
        reasons = []
        if fresh["id"] != entry["id"]:
            reasons.append(f"the new release is plugin '{fresh['id']}', not '{entry['id']}'")
        if compare_versions(fresh["version"], entry["version"]) <= 0:
            reasons.append(f"its version {fresh['version']} isn't higher than {entry['version']}")
        added = sorted(set(fresh.get("permissions", [])) - set(entry.get("permissions", [])))
        if added:
            reasons.append(f"it asks for new permissions: {', '.join(added)}")
        if reasons and entry["id"] in approved and fresh["id"] == entry["id"]:
            print(f"approved {entry['id']}: " + "; ".join(reasons))
            reasons = []
        if reasons:
            print(f"review {entry['id']}: " + "; ".join(reasons))
            report["review"].append({"id": entry["id"], "version": fresh["version"], "repository": repository, "reasons": reasons})
            continue
        override = {"id": entry["id"]}
        for key in ("version", "download_url", "sha256", "name", "author", "description"):
            if fresh.get(key):
                override[key] = fresh[key]
        if entry["id"] in approved and fresh.get("permissions"):
            override["permissions"] = fresh["permissions"]
        target = overrides / f"{entry['id']}.json"
        target.write_text(json.dumps(override, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"update {entry['id']} -> {override['version']}")
        report["updated"].append({"id": entry["id"], "version": override["version"], "file": str(target)})
    (ROOT / "update-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{len(report['updated'])} updated, {len(report['review'])} need a review")
    return 0


def build(output: pathlib.Path, overrides: pathlib.Path | None = None) -> int:
    paths = sorted(PLUGINS.glob("*.json"))
    if check(paths):
        return 1
    catalog = {}
    for path in paths:
        entry = with_override(load(path), overrides)
        entry.pop("auto_update", None)
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
    if command == "build" and rest:
        overrides = None
        if "--overrides" in rest:
            index = rest.index("--overrides")
            overrides = pathlib.Path(rest[index + 1]).resolve()
            rest = rest[:index] + rest[index + 2:]
        if len(rest) == 1:
            return build(pathlib.Path(rest[0]), overrides)
    if command == "from-release" and len(rest) == 1:
        try:
            return from_release(rest[0])
        except (ValueError, OSError) as error:
            print(f"FAIL {error}")
            return 1
    if command == "discover":
        return discover(rest or TOPICS)
    if command == "update":
        approved = set()
        overrides = DEFAULT_OVERRIDES
        if "--approve" in rest:
            index = rest.index("--approve")
            approved = {item.strip() for item in (rest[index + 1] if index + 1 < len(rest) else "").split(",") if item.strip()}
            rest = rest[:index] + rest[index + 2:]
        if "--overrides" in rest:
            index = rest.index("--overrides")
            overrides = pathlib.Path(rest[index + 1]).resolve()
            rest = rest[:index] + rest[index + 2:]
        return update(rest, approved, overrides)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
