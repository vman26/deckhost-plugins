#!/usr/bin/env python3
"""The Elgato mirror for DeckHost: our own snapshot of the former Stream Deck Store, with a copy of each
package whose source repository has an open-source license, and the live Elgato Marketplace listed with
links (never copied).

    python3 tools/mirror.py import --out DIR [--source URL|FILE]
    python3 tools/mirror.py run --out DIR [--previous DIR] [--catalogue FILE|URL] [--blocklist FILE]
                                 [--marketplace] [--dry-run] [--limit N] [--max-size MB]

import (once, by a maintainer): reads the community archive's catalogue of the old Stream Deck Store
(closed by Elgato in April 2024; https://plugins.amankhanna.me/catalogue.json by default) and writes
DIR/snapshot.json (id, name, author, link, version; it becomes mirror/snapshot.json on main) and
DIR/icons/<id>.png (checked PNGs, served from this repository's GitHub Pages). After that nothing is
read from the archive except packages that haven't been copied yet.

run (weekly): for each plugin of the snapshot (mirror/snapshot.json; the archive's catalogue until it
exists):

  - listed in plugins/ (the reviewed catalog, with real updates): left out, its copy is removed;
  - on the blocklist (mirror/blocklist.json): never mirrored, listed without a package;
  - no GitHub repository in its link, or no license on it, or one that isn't on the allowlist
    (ALLOWED_LICENSES): listed without a package (name, author, version and a Marketplace link);
  - otherwise: a copy made by an earlier run is kept; a new one is downloaded from the archive while
    it still has one, checked (a valid zip of at most --max-size MB, no paths outside the archive, a
    Stream Deck or DeckHost manifest with the same id), hashed and staged as <id>-<version>.zip.

With --marketplace it also reads the Elgato Marketplace's public plugin list (no login, nothing is
downloaded). Each plugin is listed with a link to its Marketplace page; one that is also in the
snapshot (same name and author) becomes one entry with the Marketplace's details and the snapshot's
copy, if there is one. When the Marketplace can't be read, last run's list is used.

Writes DIR/catalog.json (what DeckHost reads), DIR/NOTICE.md (who and which license),
DIR/marketplace.json (the Marketplace list, for the next run), DIR/upload/ (packages for the rolling
release) and DIR/remove.txt (release assets that no longer belong). --previous is the DIR of the last
run (with icons/): a copied version is never replaced under its checksum, and a license lookup that
merely failed keeps what was there. --dry-run says what would happen without downloading packages or
writing anything.

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
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from catalog import read_manifest  # noqa: E402  (the same package reader the catalog checks use)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "mirror" / "snapshot.json"
PLUGINS = ROOT / "plugins"
ARCHIVE_CATALOGUE = "https://plugins.amankhanna.me/catalogue.json"
ARCHIVE_PACKAGE = "https://plugins.amankhanna.me/rezipped/{id}.zip"
ARCHIVE_ICON = "https://plugins.amankhanna.me/icons/{id}.png"
COMMUNITY_ARCHIVE = "https://github.com/nekename/ElgatoAppStoreArchive"
MARKETPLACE_SEARCH = "https://marketplace.elgato.com/search?q={name}"
MARKETPLACE_PRODUCT = "https://marketplace.elgato.com/product/{slug}"
MARKETPLACE_API_HOST = "mp-gateway.elgato.com"
MARKETPLACE_PRODUCTS = f"https://{MARKETPLACE_API_HOST}/products?extension=plugins&limit=100&offset=0&ordering=-last_published"
MARKETPLACE_MAX_PAGES = 200
MARKETPLACE_ID_PREFIX = "marketplace:"
RELEASE_TAG = "elgato-mirror"
USER_AGENT = "deckhost-plugins-mirror"
DEFAULT_MAX_MB = 50
ICON_MAX_BYTES = 512 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}

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
SNAPSHOT_FIELDS = ("id", "name", "author", "link", "version")


class Network:
    """What the mirror reads from the network; tests replace this."""

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    def _get(self, url: str, headers: dict | None = None, limit: int | None = None) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
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
        return self._get(ARCHIVE_PACKAGE.format(id=urllib.parse.quote(plugin_id, safe="")), limit=limit)

    def icon(self, plugin_id: str, limit: int) -> bytes:
        return self._get(ARCHIVE_ICON.format(id=urllib.parse.quote(plugin_id, safe="")), limit=limit)

    def marketplace_page(self, url: str) -> dict:
        return json.loads(self._get(url, {"Accept": "application/json"}))


def source_repository(link: str | None) -> tuple[str, str] | None:
    """(owner, repository) from a link when it points at a GitHub repository."""
    match = GITHUB_REPOSITORY.match((link or "").strip())
    return (match.group(1), match.group(2)) if match else None


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def asset_name(plugin_id: str, version: str) -> str:
    return safe_name(f"{plugin_id}-{version}") + ".zip"


def icon_name(plugin_id: str) -> str:
    return safe_name(plugin_id) + ".png"


def pages_url(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}"


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


def load_icons(directory: pathlib.Path | None) -> set[str]:
    if directory is None or not (directory / "icons").is_dir():
        return set()
    return {path.name for path in (directory / "icons").glob("*.png")}


def load_listed_ids(directory: pathlib.Path = PLUGINS) -> set[str]:
    """Plugin ids in the reviewed catalog (plugins/*.json)."""
    ids = set()
    for path in directory.glob("*.json"):
        plugin_id = json.loads(path.read_text(encoding="utf-8")).get("id")
        if plugin_id:
            ids.add(str(plugin_id).lower())
    return ids


# --- import: our own snapshot of the archive ---------------------------------------------------------

def snapshot_entries(catalogue: list) -> list[dict]:
    """The archive's catalogue as our snapshot: its fields without the dead download and icon URLs, by id."""
    plugins = (plugin for plugin in catalogue if isinstance(plugin, dict) and plugin.get("id"))
    return sorted(({field: plugin.get(field) for field in SNAPSHOT_FIELDS} for plugin in plugins), key=lambda entry: str(entry["id"]))


def import_snapshot(network: Network, catalogue: list, report=print) -> tuple[list[dict], dict[str, bytes]]:
    """The snapshot and its icons (file name → PNG bytes)."""
    entries = snapshot_entries(catalogue)
    icons: dict[str, bytes] = {}
    for entry in entries:
        plugin_id = str(entry["id"])
        try:
            data = network.icon(plugin_id, ICON_MAX_BYTES)
        except Exception as error:
            report(f"no icon {plugin_id}: {error}")
            continue
        if not data.startswith(PNG_SIGNATURE):
            report(f"no icon {plugin_id}: not a PNG")
            continue
        icons[icon_name(plugin_id)] = data
    report(f"{len(entries)} plugins, {len(icons)} icons")
    return entries, icons


def write_import(directory: pathlib.Path, entries: list[dict], icons: dict[str, bytes]) -> None:
    (directory / "icons").mkdir(parents=True, exist_ok=True)
    for name, data in icons.items():
        (directory / "icons" / name).write_bytes(data)
    (directory / "snapshot.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- run: the snapshot, licence-gated copies ---------------------------------------------------------

def run(network: Network, catalogue: list, *, previous: dict[str, dict], blocked_ids: set[str], blocked_repositories: set[str],
        max_bytes: int, repository: str, dry_run: bool, limit: int | None, icons: set[str] = frozenset(),
        listed_ids: set[str] = frozenset(), report=print) -> tuple[list[dict], dict[str, bytes], list[str]]:
    """The new catalog entries, the packages to upload (asset name → bytes) and the assets to remove."""
    entries: list[dict] = []
    uploads: dict[str, bytes] = {}
    downloads = 0
    icon_base = f"{pages_url(repository)}/mirror/icons/"

    for plugin in sorted((p for p in catalogue if isinstance(p, dict) and p.get("id")), key=lambda p: str(p["id"])):
        plugin_id = str(plugin["id"])
        version = str(plugin.get("version") or "")
        name = str(plugin.get("name") or plugin_id)
        if plugin_id.lower() in listed_ids:
            report(f"live  {plugin_id}: listed in plugins/, left out of the mirror")
            continue
        entry = {
            "id": plugin_id,
            "name": name,
            "author": plugin.get("author"),
            "version": version,
            "icon": icon_base + icon_name(plugin_id) if icon_name(plugin_id) in icons else None,
            "marketplace_url": MARKETPLACE_SEARCH.format(name=urllib.parse.quote(name)),
            "license": None,
            "source_repository": None,
            "sha256": None,
            "download_url": None,
            "mirrored": False,
            "source": "snapshot",
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
                    report(f"skip  {plugin_id} {version}: download failed ({error}), no copy kept")
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

    return entries, uploads, stale_assets(previous, entries)


def stale_assets(previous: dict[str, dict], entries: list[dict]) -> list[str]:
    wanted = {entry["download_url"].rsplit("/", 1)[-1] for entry in entries if entry.get("download_url")}
    return sorted({entry["download_url"].rsplit("/", 1)[-1] for entry in previous.values() if entry.get("download_url")} - wanted)


# --- the live Elgato Marketplace: listed with links ------------------------------------------------

class MarketplaceError(Exception):
    pass


def marketplace_price(variant: dict) -> str | None:
    price = variant.get("price") or next(iter(variant.get("prices") or []), None)
    if not isinstance(price, dict) or not isinstance(price.get("unit_amount"), int):
        return None
    amount = price.get("unit_amount_discounted") or price["unit_amount"]
    currency = str(price.get("currency") or "")
    text = f"{CURRENCY_SYMBOLS[currency]}{amount / 100:.2f}" if currency in CURRENCY_SYMBOLS else f"{amount / 100:.2f} {currency}".strip()
    plan = price.get("plan")
    return text if plan in (None, "once") else f"{text}/{plan}"


def normalize_marketplace(product: dict) -> dict:
    """One Marketplace product as a list entry (raises MarketplaceError when it lacks id, name or slug)."""
    if not isinstance(product, dict) or not all(isinstance(product.get(key), str) and product[key] for key in ("id", "name", "slug")):
        raise MarketplaceError("a product without an id, name or slug")
    variants = [variant for variant in product.get("variants") or [] if isinstance(variant, dict)]
    variant = next((v for v in variants if v.get("id") == product.get("default_variant_id")), None) or next(
        (v for v in variants if v.get("is_default")), None) or (variants[0] if variants else {})
    links = [link.get("url") for link in product.get("additional_links") or [] if isinstance(link, dict)]
    source = next((s for s in map(source_repository, links) if s), None)
    platforms = next((tagset.get("tags") for tagset in product.get("tagsets") or []
                      if isinstance(tagset, dict) and tagset.get("name") == "OS"), None)
    is_free = bool(variant.get("is_free"))
    return {
        "marketplace_id": product["id"],
        "name": product["name"],
        "author": product.get("org_name"),
        "description": product.get("seo"),
        "icon": product.get("app_icon_cdn"),
        "marketplace_url": MARKETPLACE_PRODUCT.format(slug=urllib.parse.quote(product["slug"], safe="")),
        "is_free": is_free,
        "price": None if is_free else marketplace_price(variant),
        "platforms": platforms if isinstance(platforms, list) else None,
        "source_repository": f"https://github.com/{source[0]}/{source[1]}" if source else None,
        "last_published": product.get("last_published"),
    }


def read_marketplace(network: Network, start: str = MARKETPLACE_PRODUCTS, max_pages: int = MARKETPLACE_MAX_PAGES) -> list[dict]:
    """Every plugin on the Marketplace, following the API's `next` links. Raises MarketplaceError when a page
    fails or isn't what we know, so a partial list is never used."""
    products: list[dict] = []
    url: str | None = start
    for _ in range(max_pages):
        if url is None:
            break
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != MARKETPLACE_API_HOST:
            raise MarketplaceError(f"a next page outside {MARKETPLACE_API_HOST}: {url}")
        try:
            page = network.marketplace_page(url)
        except MarketplaceError:
            raise
        except Exception as error:
            raise MarketplaceError(f"reading {url} failed ({error})") from error
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise MarketplaceError("a page without results")
        products.extend(normalize_marketplace(product) for product in page["results"])
        url = page.get("next") or None
    else:
        if url is not None:
            raise MarketplaceError(f"more than {max_pages} pages")
    if not products:
        raise MarketplaceError("no plugins")
    unique = {product["marketplace_id"]: product for product in products}
    return sorted(unique.values(), key=lambda product: product["marketplace_id"])


def match_key(name: str | None, author: str | None) -> tuple[str, str]:
    def simple(value):
        return re.sub(r"[^a-z0-9]", "", (value or "").lower())
    return simple(name), simple(author)


def merge(entries: list[dict], marketplace: list[dict], blocked_ids: set[str], blocked_repositories: set[str], report=print) -> list[dict]:
    """The snapshot's entries with the Marketplace's plugins: one entry per plugin, by name and author."""
    by_key: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        by_key.setdefault(match_key(entry["name"], entry.get("author")), []).append(entry)
    merged_ids: set[str] = set()
    result: list[dict] = []
    for product in marketplace:
        entry_id = MARKETPLACE_ID_PREFIX + product["marketplace_id"]
        repository = (product["source_repository"] or "").removeprefix("https://github.com/").lower()
        if entry_id.lower() in blocked_ids or (repository and repository in blocked_repositories):
            report(f"hide  {product['name']}: on the blocklist")
            continue
        candidates = [entry for entry in by_key.get(match_key(product["name"], product["author"]), []) if entry["id"] not in merged_ids]
        snapshot = candidates[0] if len(candidates) == 1 else None
        details = {key: product[key] for key in ("marketplace_id", "description", "marketplace_url", "is_free", "price", "platforms", "last_published")}
        if snapshot:
            merged_ids.add(snapshot["id"])
            entry = dict(snapshot) | details | {
                "name": product["name"],
                "author": product["author"] or snapshot.get("author"),
                "icon": product["icon"] or snapshot.get("icon"),
                "source_repository": snapshot.get("source_repository") or product["source_repository"],
                "download_version": snapshot["version"] if snapshot.get("mirrored") else None,
                "source": "marketplace",
            }
        else:
            entry = details | {
                "id": entry_id,
                "name": product["name"],
                "author": product["author"],
                "version": None,
                "icon": product["icon"],
                "license": None,
                "source_repository": product["source_repository"],
                "sha256": None,
                "download_url": None,
                "mirrored": False,
                "source": "marketplace",
            }
        result.append(entry)
    result.extend(entry for entry in entries if entry["id"] not in merged_ids)
    report(f"marketplace: {len(marketplace)} plugins, {len(merged_ids)} matched with the snapshot")
    return sorted(result, key=lambda entry: entry["id"])


def load_marketplace(directory: pathlib.Path | None) -> list[dict] | None:
    if directory is None or not (directory / "marketplace.json").exists():
        return None
    return json.loads((directory / "marketplace.json").read_text(encoding="utf-8"))


# --- output ------------------------------------------------------------------------------------------

def notice(entries: list[dict], repository: str) -> str:
    mirrored = [entry for entry in entries if entry["mirrored"]]
    listed = sum(1 for entry in entries if entry.get("source") == "marketplace")
    lines = [
        "# Elgato plugin mirror",
        "",
        "Stream Deck plugins for DeckHost's plugin store, from two places:",
        "",
        "- **The former Stream Deck Store** (closed by Elgato in April 2024): our own snapshot of it, taken",
        f"  from the community archive ({COMMUNITY_ARCHIVE}) of what Elgato's servers served.",
        "  A plugin is copied only when its source repository has an open-source license that allows",
        "  passing it on; the copy is unchanged apart from the checks below and stored with its SHA-256.",
        "  The others are listed with a link to the Elgato Marketplace and are not copied.",
        "- **The Elgato Marketplace**: its public plugin list, each plugin with a link to its Marketplace page.",
        "  Nothing is downloaded from the Marketplace or copied: you get a plugin there.",
        "",
        "We are not the authors of these plugins and are not affiliated with Elgato. Every plugin keeps",
        "its own license and its author, named in the table.",
        "",
        f"Don't want a plugin here? Open an issue in https://github.com/{repository} (or send a pull request",
        "adding its id to `mirror/blocklist.json`). It is removed from the mirror, and its package deleted,",
        "by the next run (weekly) or at once when a maintainer runs the workflow.",
        "",
        f"{listed} plugins are listed from the Elgato Marketplace. {len(mirrored)} of {len(entries)} plugins are copied:",
        "",
        "| Plugin | Author | Version | License | Source |",
        "|---|---|---|---|---|",
    ]
    for entry in sorted(mirrored, key=lambda e: e["name"].lower()):
        version = entry.get("download_version") or entry["version"]
        cells = [entry["name"], entry["author"] or "", version, entry["license"], entry["source_repository"] or ""]
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def write_output(directory: pathlib.Path, entries: list[dict], uploads: dict[str, bytes], stale: list[str], repository: str,
                 marketplace: list[dict] | None = None) -> None:
    (directory / "upload").mkdir(parents=True, exist_ok=True)
    for asset, data in uploads.items():
        (directory / "upload" / asset).write_bytes(data)
    (directory / "catalog.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (directory / "NOTICE.md").write_text(notice(entries, repository), encoding="utf-8")
    (directory / "remove.txt").write_text("".join(f"{asset}\n" for asset in stale), encoding="utf-8")
    if marketplace is not None:
        (directory / "marketplace.json").write_text(json.dumps(marketplace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    imported = commands.add_parser("import")
    imported.add_argument("--out", type=pathlib.Path, required=True)
    imported.add_argument("--source", default=ARCHIVE_CATALOGUE)
    command = commands.add_parser("run")
    command.add_argument("--out", type=pathlib.Path)
    command.add_argument("--previous", type=pathlib.Path)
    command.add_argument("--catalogue", help="the snapshot (default: mirror/snapshot.json, else the archive's catalogue)")
    command.add_argument("--blocklist", type=pathlib.Path, default=ROOT / "mirror" / "blocklist.json")
    command.add_argument("--marketplace", action="store_true", help="also list the Elgato Marketplace's plugins")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--limit", type=int)
    command.add_argument("--max-size", type=int, default=DEFAULT_MAX_MB, help="megabytes")
    args = parser.parse_args(arguments)
    network = Network()

    if args.command == "import":
        entries, icons = import_snapshot(network, network.catalogue(args.source))
        write_import(args.out, entries, icons)
        return 0

    if not args.dry_run and args.out is None:
        parser.error("--out is required (unless --dry-run)")
    catalogue_source = args.catalogue or (str(SNAPSHOT) if SNAPSHOT.exists() else ARCHIVE_CATALOGUE)
    print(f"snapshot: {catalogue_source}")
    repository = os.environ.get("GITHUB_REPOSITORY", "vman26/deckhost-plugins")
    blocked_ids, blocked_repositories = load_blocklist(args.blocklist)
    previous = load_previous(args.previous)
    entries, uploads, _ = run(
        network, network.catalogue(catalogue_source), previous=previous, blocked_ids=blocked_ids,
        blocked_repositories=blocked_repositories, max_bytes=args.max_size * 1024 * 1024, repository=repository,
        dry_run=args.dry_run, limit=args.limit, icons=load_icons(args.previous), listed_ids=load_listed_ids())

    marketplace = None
    if args.marketplace:
        try:
            marketplace = read_marketplace(network)
        except MarketplaceError as error:
            marketplace = load_marketplace(args.previous)
            print(f"marketplace: not read ({error}); {'last run' if marketplace is not None else 'nothing'}'s list is used")
        if marketplace is not None:
            entries = merge(entries, marketplace, blocked_ids, blocked_repositories)
    stale = stale_assets(previous, entries)

    mirrored = sum(1 for entry in entries if entry["mirrored"])
    print(f"{len(entries)} plugins, {mirrored} mirrored, {len(uploads)} new packages, {len(stale)} to remove")
    if not args.dry_run:
        write_output(args.out, entries, uploads, stale, repository, marketplace)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
