#!/usr/bin/env bash
# Checks the packages of the given entries (plugins/<id>.json): downloads each one (checksum, id and
# version against the entry) and loads it with DeckHost's own --check-plugin, from DeckHost's newest
# release (pre-releases too). Writes the entries that failed to failed.txt; exits 1 if any did.
# Needs GH_TOKEN for the DeckHost download.
set -uo pipefail

app=""
tag="$(gh release list --repo vman26/DeckHost --limit 1 --json tagName --jq '.[0].tagName' 2>/dev/null || true)"
if [[ -n "$tag" ]] && gh release download "$tag" --repo vman26/DeckHost --pattern '*linux-x64.tar.gz' --dir deckhost-app --clobber; then
  tar -xzf deckhost-app/*.tar.gz -C deckhost-app
  app="$(find deckhost-app -name DeckHost.App -type f | head -1)"
  chmod +x "$app"
else
  echo "::warning::No DeckHost release to load the plugins with; checking only checksums, ids and versions."
fi

failed=()
for file in "$@"; do
  if ! python3 tools/catalog.py download "$file" packages; then
    failed+=("$file")
    continue
  fi
  id="$(basename "$file" .json)"
  if [[ -n "$app" && -e "packages/$id.zip" ]] && ! "$app" --check-plugin "packages/$id.zip"; then
    failed+=("$file")
  fi
done

printf '%s\n' "${failed[@]}" > failed.txt
if [[ ${#failed[@]} -gt 0 ]]; then
  echo "Failed: ${failed[*]}"
  exit 1
fi
