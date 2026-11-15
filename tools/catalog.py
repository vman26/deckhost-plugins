#!/usr/bin/env python3
"""Checks plugin entries and builds catalog.json.

    python3 tools/catalog.py check [files…]      # the entries (all when none are given)
    python3 tools/catalog.py download <file> <dir> # download the package, check sha256, id and version
    python3 tools/catalog.py build <output.json>   # merge every entry into the catalog DeckHost reads
"""

import hashlib
import json
import pathlib
import re
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
SHA = re.compile(r"^[0-9a-fA-F]{64}$")
REQUIRED = ["id", "name", "author", "description", "version"]
KNOWN = set(REQUIRED) | {"category", "repository", "download_url", "sha256", "icon", "page", "permissions", "license", "purchase_url", "price"}
PERMISSIONS = {"network", "processes", "files", "input", "system", "clipboard"}


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
    with zipfile.ZipFile(target) as archive:
        try:
            manifest = json.loads(archive.read("plugin.json"))
        except KeyError:
            print(f"FAIL {entry['id']}: the package has no plugin.json at its root")
            return 1
    if manifest.get("id") != entry["id"] or manifest.get("version") != entry["version"]:
        print(f"FAIL {entry['id']}: plugin.json says {manifest.get('id')} {manifest.get('version')}, the entry {entry['id']} {entry['version']}")
        return 1
    print(f"ok   {entry['id']} {entry['version']}: checksum, id and version match ({target})")
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
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
