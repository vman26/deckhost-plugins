#!/usr/bin/env python3
"""Which systems each plugin in DeckHost's store is made for: platforms.json, read from every package's manifest.

    python3 tools/platforms.py run --out DIR [--previous DIR] [--limit N] [--max-size MB]

Neither the OpenAction catalogue nor the Elgato community archive says which systems a plugin runs on: only the
plugin's own manifest does (a Stream Deck manifest's "OS" list, a DeckHost plugin.json's optional "platforms";
without it a DeckHost plugin runs everywhere DeckHost does). This reads the package of every plugin the store
lists:

  - DeckHost: plugins/*.json that have a download_url;
  - OpenAction: the catalogue (OPENACTION_CATALOGUE), each repository's newest GitHub release;
  - Elgato community archive: the catalogue (ARCHIVE_CATALOGUE) and its re-zipped copies.

and writes DIR/platforms.json, which DeckHost's store reads (PluginStore.PlatformsIndexUrl):

  {"plugins": {"<id>": {"source": "openaction", "version": "<what was read>", "platforms": ["windows", "macos"]}}}

"platforms" is left out when the manifest lists no systems (unknown, not "none"). --previous DIR is the last run's
output: a plugin whose version (or, for OpenAction, release file) is unchanged isn't downloaded again, and one that
can't be read this time keeps what was found before. --limit N downloads at most N packages a run; the rest wait
for the next run.

GitHub requests use the GH_TOKEN (or GITHUB_TOKEN) environment variable when it's set.
"""

import argparse
import io
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import catalog  # noqa: E402  (the plugin entries and GitHub release lookup the catalog uses)

OPENACTION_CATALOGUE = "https://openactionapi.github.io/plugins/catalogue.json"
ARCHIVE_CATALOGUE = "https://plugins.amankhanna.me/catalogue.json"
ARCHIVE_PACKAGE = "https://plugins.amankhanna.me/rezipped/{id}.zip"
DEFAULT_LIMIT = 200
DEFAULT_MAX_MB = 80
USER_AGENT = "deckhost-plugins-platforms"
OUTPUT = "platforms.json"

DECKHOST = "deckhost"
OPENACTION = "openaction"
ELGATO = "elgato"

# The order DeckHost shows them in.
ALL_PLATFORMS = ["windows", "macos", "linux"]
PLATFORM_NAMES = {
    "windows": "windows", "win": "windows", "win32": "windows", "win64": "windows",
    "mac": "macos", "macos": "macos", "osx": "macos", "darwin": "macos",
    "linux": "linux",
}
GITHUB_REPOSITORY = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?].*)?$")


class Network:
    """What this reads from the network; tests replace it."""

    def get(self, url: str, limit: int | None = None) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            if limit is None:
                return response.read()
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"larger than {limit} bytes")
            return data

    def json(self, url: str):
        return json.loads(self.get(url))

    def newest_release_file(self, owner: str, repository: str, plugin_id: str) -> str | None:
        """The download URL of the newest release's plugin file, the way the catalog picks it."""
        _, asset = catalog.newest_package(owner, repository, plugin_id)
        return asset["browser_download_url"] if asset else None


def normalize(names) -> list[str] | None:
    """The systems among names ("mac", "Windows", …), in display order; None when there are none."""
    found = {PLATFORM_NAMES.get(str(name).strip().lower()) for name in names or []} - {None}
    return [platform for platform in ALL_PLATFORMS if platform in found] or None


def platforms_of(package: bytes) -> list[str] | None:
    """The systems a package's manifest names: a Stream Deck manifest's OS list, or a DeckHost plugin.json's
    "platforms" (all of them when it has none: DeckHost plugins are .NET and run wherever DeckHost does).
    None when it's no plugin package or the manifest names no systems."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile:
        return None
    with archive:
        names = archive.namelist()
        if "plugin.json" in names:
            data = json.loads(archive.read("plugin.json").decode("utf-8-sig"))
            return normalize(data.get("platforms")) or list(ALL_PLATFORMS)
        for name in names:
            parts = name.replace("\\", "/").split("/")
            if len(parts) == 2 and parts[0].lower().endswith(".sdplugin") and parts[1].lower() == "manifest.json":
                data = json.loads(archive.read(name).decode("utf-8-sig"))
                return normalize(os.get("Platform") for os in data.get("OS") or [] if isinstance(os, dict))
    return None


def wanted(network: Network) -> list[dict]:
    """Every plugin to look at: {id, source, version, url} (url None: look it up when it's downloaded)."""
    plugins = []
    for path in sorted(catalog.PLUGINS.glob("*.json")):
        entry = catalog.load(path)
        if entry.get("download_url"):
            plugins.append({"id": entry["id"], "source": DECKHOST, "version": entry.get("version") or entry["download_url"], "url": entry["download_url"]})

    openaction = network.json(OPENACTION_CATALOGUE)
    for plugin_id, plugin in sorted((openaction or {}).items()):
        repository = GITHUB_REPOSITORY.match((plugin or {}).get("repository") or "")
        download = (plugin or {}).get("download_url")
        if download:
            plugins.append({"id": plugin_id, "source": OPENACTION, "version": download, "url": download})
        elif repository:
            plugins.append({"id": plugin_id, "source": OPENACTION, "version": None, "url": None, "repository": repository.groups()})

    for plugin in network.json(ARCHIVE_CATALOGUE) or []:
        if isinstance(plugin, dict) and plugin.get("id"):
            plugins.append({
                "id": plugin["id"],
                "source": ELGATO,
                "version": plugin.get("version") or "",
                "url": ARCHIVE_PACKAGE.format(id=urllib.parse.quote(plugin["id"], safe="")),
            })
    return plugins


def run(out: pathlib.Path, previous: pathlib.Path | None, limit: int, max_bytes: int, network: Network | None = None) -> int:
    network = network or Network()
    before = {}
    if previous and (previous / OUTPUT).exists():
        before = json.loads((previous / OUTPUT).read_text(encoding="utf-8")).get("plugins", {})

    result = {}
    downloaded = kept = failed = waiting = 0
    for plugin in wanted(network):
        plugin_id = plugin["id"]
        if plugin_id in result:  # listed by two sources: the first (DeckHost, then OpenAction) wins
            continue
        old = before.get(plugin_id)
        try:
            if plugin["url"] is None:
                # OpenAction: the version is the newest release's file, which takes a GitHub lookup.
                plugin["url"] = plugin["version"] = network.newest_release_file(*plugin["repository"], plugin_id)
                if plugin["url"] is None:
                    continue
            if old and old.get("version") == plugin["version"] and old.get("source") == plugin["source"]:
                result[plugin_id] = old
                kept += 1
                continue
            if downloaded >= limit:
                waiting += 1
                if old:
                    result[plugin_id] = old
                continue
            downloaded += 1
            found = {"source": plugin["source"], "version": plugin["version"]}
            if platforms := platforms_of(network.get(plugin["url"], limit=max_bytes)):
                found["platforms"] = platforms
            result[plugin_id] = found
        except Exception as error:  # noqa: BLE001  (one plugin failing must not stop the others)
            failed += 1
            print(f"could not read {plugin_id} ({plugin['source']}): {error}")
            if old:
                result[plugin_id] = old

    out.mkdir(parents=True, exist_ok=True)
    index = {"plugins": {plugin_id: result[plugin_id] for plugin_id in sorted(result)}}
    (out / OUTPUT).write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    known = sum(1 for entry in result.values() if entry.get("platforms"))
    print(f"{len(result)} plugins ({known} with their systems): {downloaded} read, {kept} unchanged, {failed} failed, {waiting} wait for the next run")
    return 0


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run")
    command.add_argument("--out", type=pathlib.Path, required=True)
    command.add_argument("--previous", type=pathlib.Path)
    command.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    command.add_argument("--max-size", type=int, default=DEFAULT_MAX_MB, help="MB")
    options = parser.parse_args(arguments)
    return run(options.out, options.previous, options.limit, options.max_size * 1024 * 1024)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
