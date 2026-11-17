# DeckHost plugins

The plugin catalog of [DeckHost](https://github.com/vman26/DeckHost): every plugin listed here shows up
in DeckHost under **Plugins → Store** (source "DeckHost"). Each plugin is one file in
[`plugins/`](plugins); a workflow checks every submission and publishes the catalog to this
repository's GitHub Pages (`catalog.json`), which DeckHost reads.

DeckHost also lists OpenAction and Stream Deck plugins from their own catalogs; this one is for
**DeckHost plugins** (`plugin.json` packages).

## How listing works

| | What you do | Review |
|---|---|---|
| **New plugin** | Open an **Add a plugin** issue with your repository (or a pull request) | Once, by a maintainer |
| **New version** | Publish a GitHub release with the plugin file | None: listed within about six hours |
| **New name, description, author** | Change them in your `plugin.json` (or `manifest.json`) and release | None: taken from the package |
| **New permissions** | Declare them in `plugin.json` and release | A maintainer approves the update |
| **Other changes** (category, icon, repository) | A pull request to your entry | A maintainer merges it |

A plugin runs on people's computers, so a maintainer looks at it once before it's listed. After
that, the repository it was reviewed with is trusted: the **update** workflow checks every listed
plugin's newest release every six hours and lists it by itself when the release is the same plugin
with a higher version, asks for no new permissions and passes the same checks as a pull request
(checksum, id, version, DeckHost loads it). Anything else becomes an issue labelled
`review-update` for a maintainer.

`main` only takes reviewed pull requests, so these new versions are kept on the `catalog-updates`
branch (`updates/<id>.json`: version, download, checksum, name, author, description), and the
**publish** workflow applies them on top of the reviewed entries. When an entry on `main` gets a
version as new (a pull request), the update file for it is dropped.

## Add your plugin

**The quick way:** open an issue with **Add a plugin** and give your plugin's GitHub repository. A
workflow reads the package of its newest release (id, name, version, author, description), computes
its SHA-256 and opens a pull request with the entry; a maintainer reviews it once. Later releases
are picked up by themselves. The plugin template
(`dotnet new deckhost-plugin`) makes such releases for you. Maintainers can do the same with
`python3 tools/catalog.py from-release https://github.com/you/your-plugin` or **Actions → submit**.

**By hand:**

1. Build your plugin and check it: `DeckHost --check-plugin your-plugin.zip`
   ([how to write one](https://vman26.github.io/DeckHost/maintainers/plugins/)).
2. Publish the `.zip` somewhere it stays: a GitHub release of your plugin's repository is best.
3. Add `plugins/<your plugin id>.json` in a pull request (copy [the OBS entry](plugins/deckhost.plugin.obs.json)):

```json
{
  "id": "your.plugin.id",
  "name": "Your Plugin",
  "author": "You",
  "description": "One or two sentences: what it does.",
  "version": "1.0.0",
  "category": "Streaming",
  "repository": "https://github.com/you/your-plugin",
  "download_url": "https://github.com/you/your-plugin/releases/download/v1.0.0/your.plugin.id.zip",
  "sha256": "the package's SHA-256",
  "icon": "https://raw.githubusercontent.com/you/your-plugin/main/icon.png",
  "permissions": ["network"],
  "license": "MIT"
}
```

Optional: `"auto_update": false` to update by pull request only (see Updating).

`sha256`: `sha256sum your.plugin.id.zip` (Linux), `shasum -a 256` (macOS) or
`Get-FileHash -Algorithm SHA256` (Windows). DeckHost refuses a download that doesn't match.

The check downloads your package, compares the checksum and the id and version with its
`plugin.json` (or a Stream Deck package's `manifest.json`), and loads it with DeckHost's own
`--check-plugin`. A maintainer then reviews it.

### Found on GitHub

Once a week the **discover** workflow looks for repositories with the topics `deckhost-plugin`,
`streamdeck-plugin`, `stream-deck-plugin` or `openaction-plugin` that have a plugin file in a release
and aren't listed yet, and opens one pull request with their entries. Give your repository one of
these topics to be found. Nothing is listed until a maintainer merges it.

### Updating

Publish a release; nothing else. The catalog follows your newest release (stable before
pre-release; a file named after your plugin id is picked first when a release has several).
Releases that need a look (a new permission, a lower version, a different plugin id, a package
that fails the checks) wait for a maintainer, who approves them with **Actions → update → Run
workflow** and the plugin id under *approve*.

A plugin not on GitHub, or one whose maintainers want to check every version, sets
`"auto_update": false` in its entry and updates through pull requests (change `version`,
`download_url` and `sha256`).

### Paid plugins

Paid plugins are welcome. Leave out `download_url` and `sha256` and add where to buy it:

```json
  "purchase_url": "https://you.example/buy",
  "price": "€5"
```

DeckHost shows a **Buy** button that opens the page; buyers install the file they get with
**Install plugin…**. Payment, licensing and delivery are up to you.

## Elgato mirror

DeckHost's store also lists the Elgato Marketplace through a community archive. The **mirror**
workflow (`tools/mirror.py`, weekly) keeps a catalog of that archive here and **copies a plugin's
package only when its source repository has an open-source license that allows it** (MIT, Apache-2.0,
BSD, ISC, MPL-2.0, the GPL family, Unlicense, CC0; see `ALLOWED_LICENSES`). A plugin without a GitHub
repository, without a license or with another one (non-commercial, "all rights reserved") is listed
with its Marketplace link and never copied. A copy is checked (a valid zip with a plugin manifest of the
same id, at most 50 MB, no paths outside the archive) and kept with its SHA-256; DeckHost refuses a
download that doesn't match.

- The files: `mirror/catalog.json` and `mirror/NOTICE.md` (authors, licenses, sources) on this
  repository's GitHub Pages; the packages are assets of the `elgato-mirror` release.
- **Takedown:** open an issue, or a pull request adding the plugin id (or its repository) to
  [`mirror/blocklist.json`](mirror/blocklist.json). The next run deletes the package and lists the
  plugin without a download.
- **Off by default.** The workflow runs only with the repository variable `ELGATO_MIRROR_ENABLED` set
  to `true`, which waits until Elgato's terms for the Marketplace are confirmed. **Actions → mirror →
  Run workflow** with *dry run* works either way and only reports what it would do.

## Rules

- The plugin works with the current DeckHost and does what its description says.
- `permissions` lists what it does beyond drawing and reacting to keys (network, starting
  programs, files, input, system settings, clipboard).
- No malware, tracking without consent, or hidden network access. Plugins that break these rules
  are removed.
- One file per plugin; the file name is the plugin id.

## License

This repository (the catalog files and the tools) is licensed under the GPL-3.0, like DeckHost
([LICENSE](LICENSE)). Each plugin has its own license.
