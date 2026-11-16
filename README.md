# DeckHost plugins

The plugin catalog of [DeckHost](https://github.com/vman26/DeckHost): every plugin listed here shows up
in DeckHost under **Plugins → Store** (source "DeckHost"). Each plugin is one file in
[`plugins/`](plugins); a workflow checks every submission and publishes the catalog to this
repository's GitHub Pages (`catalog.json`), which DeckHost reads.

DeckHost also lists OpenAction and Stream Deck plugins from their own catalogs; this one is for
**DeckHost plugins** (`plugin.json` packages).

## Add your plugin

**The quick way:** open an issue with **Add a plugin** and give your plugin's GitHub repository. A
workflow reads the package of its newest release (id, name, version, author, description), computes
its SHA-256 and opens a pull request with the entry; a maintainer reviews it. The plugin template
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

Change `version`, `download_url` and `sha256` in your file, in a new pull request.

### Paid plugins

Paid plugins are welcome. Leave out `download_url` and `sha256` and add where to buy it:

```json
  "purchase_url": "https://you.example/buy",
  "price": "€5"
```

DeckHost shows a **Buy** button that opens the page; buyers install the file they get with
**Install plugin…**. Payment, licensing and delivery are up to you.

## Rules

- The plugin works with the current DeckHost and does what its description says.
- `permissions` lists what it does beyond drawing and reacting to keys (network, starting
  programs, files, input, system settings, clipboard).
- No malware, tracking without consent, or hidden network access. Plugins that break these rules
  are removed.
- One file per plugin; the file name is the plugin id.

## License

The catalog files are CC0 (public domain). Each plugin has its own license.
