# Elgato plugin mirror

Stream Deck plugins for DeckHost's plugin store, from two places:

- **The former Stream Deck Store** (closed by Elgato in April 2024): our own snapshot of it, taken
  from the community archive (https://github.com/nekename/ElgatoAppStoreArchive) of what Elgato's servers served.
  A plugin is copied only when its source repository has an open-source license that allows
  passing it on; the copy is unchanged apart from the checks below and stored with its SHA-256.
  The others are listed with a link to the Elgato Marketplace and are not copied.
- **The Elgato Marketplace**: its public plugin list, each plugin with a link to its Marketplace page.
  Nothing is downloaded from the Marketplace or copied: you get a plugin there.

We are not the authors of these plugins and are not affiliated with Elgato. Every plugin keeps
its own license and its author, named in the table.

Don't want a plugin here? Open an issue in https://github.com/vman26/deckhost-plugins (or send a pull request
adding its id to `mirror/blocklist.json`). It is removed from the mirror, and its package deleted,
by the next run (weekly) or at once when a maintainer runs the workflow.

1899 plugins are listed from the Elgato Marketplace. 46 of 1977 plugins are copied:

| Plugin | Author | Version | License | Source |
|---|---|---|---|---|
| AI Paints | f00d4tehg0dz | 1.0.2 | MIT | https://github.com/f00d4tehg0dz/elgato-streamdeck-ai-paints |
| AzureDevOps | Panu Oksala | 1.4.0 | MIT | https://github.com/panuoksala/streamdeck-azuredevops-plugin |
| Billables Status | Emma Litwa-Vulcu | 0.5.1 | GPL-3.0 | https://github.com/reiniiriarios/stream-deck-billables-harvest |
| Bitwarden Tools | TheJoeIaut | 1.0 | MIT | https://github.com/TheJoeIaut/Bitwarden-Streamdeck-Plugin |
| Bluetooth Monitor | James Rumkee | 1.0.0 | MIT | https://github.com/Rumkee/StreamDeckBluetoothMonitor |
| Bluetooth Switcher | Nikita Sobolev | 1.1.0 | MIT | https://github.com/sobolevn/bluetooth-streamdeck-plugin |
| Clipboard slot | Commit-La-Grenouille | 0.7.0 | GPL-3.0 | https://github.com/Commit-La-Grenouille/streamdeck-clipboard-buddy |
| Discord Volume Mixer | Danol | 2.0.1.7 | GPL-3.0 | https://github.com/CZDanol/StreamDeck-DiscordVolumeMixer2 |
| DMS Github Plugin | Rafael Dohms | 0.2.0 | MIT | https://github.com/rdohms/com.dms.streamdeck.github |
| Focused Application Volume | dlprows | 1.1.2 | MIT | https://github.com/dlprows/FocusVolumeControl |
| FxCommands | EggRP | 1.1 | MIT | https://github.com/EggRP/fxcommands |
| Godot Utilities | Boyne Games | 1.0.0 | MIT | https://github.com/BoyneGames/streamdeck-godot-plugin |
| Home Assistant | Christoph Giesche | 2.3.1 | MIT | https://github.com/cgiesche/streamdeck-homeassistant |
| Homebridge | Sergey Tihon | 1.4.0 | MIT | https://github.com/sergey-tihon/streamdeck-homebridge |
| HWiNFO | exension | 2.0.5 | GPL-3.0 | https://github.com/exension/hwinfo-streamdeck |
| Japanese Reviews | Ascend - Will Eddins | 1.0.2 | MIT | https://github.com/ascendedguard/streamdeck-japanesereviews |
| JetBrains IDE | Jacky Liu | 1.0.1 | Apache-2.0 | https://github.com/JetBrains/intellij-streamdeck-plugin |
| Jira | Daniel Pfeiffer | 1.3.0 | MIT | https://github.com/mediabounds/streamdeck-jira |
| League Observer Tool | RCVolus | 1.2.0 | MIT | https://github.com/RCVolus/league-observer-tool-sdPlugin |
| McControl | Mitch McKean | 0.7.2 | MIT | https://github.com/MitchMcKean/mccontrol |
| Microsoft 365 Actions | Marcell Spies | 1.0 | MIT | https://github.com/marcells/streamdeck-microsoft365 |
| MM.StreamDeck | Gabriel Mongeon | 0.1 | MIT | https://github.com/mongeon/MattermostAPI |
| Multi OBS Controller | the_ca11 | 0.2.0 | MIT | https://github.com/theca11/multi-obs-controller |
| My IP | Nuagic | 1.3 | MIT | https://github.com/Nuagic/streamdeck-myip |
| Obsideck | Jordan Duabe | 1.1.0 | MIT | https://github.com/j4ckofalltrades/obsideck |
| On Air Clock | Marc Herrmann | 1.1.1 | MIT | https://github.com/wortkrieg/streamdeck-onairclock |
| OSA Script | Gabriel Perales | 1.0.0 | MIT | https://github.com/gabrielperales/streamdeck-osascript |
| Roku Remote | Jasper Cools | 1.0 | Apache-2.0 | https://github.com/FUNKYJASPER2/StreamDeck-Roku-Remote-Plugin |
| Sensibo | Alhazmy13 | 1.1.0 | MIT | https://github.com/alhazmy13/streamdeck-sensibo |
| Serial IR Blaster | FeyDelight | 1.2 | MIT | https://github.com/feydelight/IRDecoderAndBlaster |
| Shortcuts | SENTINELITE | 1.0.7.1 | AGPL-3.0 | https://github.com/SENTINELITE/StreamDeck-Shortcuts |
| Stocks | exension | 1.1.3 | MIT | https://github.com/exension/stock-ticker-stream-deck-plugin |
| Task Timer | 75py | 1.0.0 | MIT | https://github.com/75py/streamdeck-tasktimer |
| TeamSpeak 5 | LeonMarcel | 1.0.0 | MIT | https://github.com/LeonMarcel-HD/StreamDeck-TeamSpeak5 |
| TeslaFi | f00d4tehg0dz | 2.4 | MIT | https://github.com/f00d4tehg0dz/Teslafi-Status-Plugin-for-Eglato-Streamdeck |
| Tomato Timer | Galloway Labs | 0.6.0 | MIT | https://github.com/gallowaylabs/streamdeck-tomato-timer |
| Ultraschall (Alternative) | Sven Wiegand | 2.1.0 | MIT | https://github.com/svenwiegand/streamdeck-ultraschall |
| Visual Studio | Nicollas R. | 3.3.3 | MIT | https://github.com/nicollasricas/streamdeckvs |
| Wakatime | Morphclue | 1.0.11 | MIT | https://github.com/Morphclue/streamdeck-wakatime |
| Web Requests | Adrian Mullings | 0.1.0 | MIT | https://github.com/data-enabler/streamdeck-web-requests |
| Window Actions | Arky Asmal | 3.0.0 | MIT | https://github.com/aasmal97/Window-Actions |
| Windows Display Settings | GeekyEggo | 1.0.0 | GPL-3.0 | https://github.com/GeekyEggo/windows-display-settings |
| WLED | Dave Kimura | 1.0.5 | MIT | https://github.com/kobaltz/streamdeck-wled |
| Xcode | Daniel Kennett | 1.0.1 | MIT | https://github.com/iKenndac/xcode-streamdeck-plugin |
| YouTrack Ticket Counter | Konstantin Annikov | 1.0.6 | Apache-2.0 | https://github.com/JetBrains/youtrack-streamdeck-plugin |
| YouTube Music Desktop Connector | XeroxDev | 1.7.0 | MIT | https://github.com/XeroxDev/YTMD-StreamDeck |
