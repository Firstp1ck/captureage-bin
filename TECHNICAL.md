# CaptureAge technical reference

Advanced setup, configuration, recovery, and troubleshooting.

[Back to README](README.md) · [Development guide](DEVELOPMENT.md)

## Commands

- `captureage` launches CaptureAge in AoE II: DE's Proton environment.
- `captureage --help` lists usage and supported Steam overrides.
- `captureage --register` enables the game's **Spectate with CA** integration.

## Game-path settings and recovery

Before each launch, the launcher discovers the game's Steam library and Proton
prefix, including libraries on other drives. Missing or invalid saved game-path
settings are repaired; a valid existing path and all other settings are preserved.

Settings are stored inside the game's Proton prefix at
`drive_c/users/steamuser/AppData/Roaming/CaptureAge/persistedState_prod.json`.
Before a repair, existing settings are backed up beside that file as
`persistedState_prod.json.backup-*`. Changes run as the launching user.

If the game files, drive mapping, or prefix are unavailable, or the settings file
is malformed, the launcher reports an error and stops without replacing existing
settings. Mount the Steam library and launch the game through Steam at least once
before trying again.

## Optional game and browser integration

To enable the in-game **Spectate with CA** button, close the game and run:

```sh
captureage --register
```

This imports the included registry file into the current user's AoE II
Proton prefix. It registers the packaged executable and `captureage://`
protocol. It is optional and must be repeated for a new prefix. If you
also want browser `captureage://` links to open the Linux launcher:

```sh
xdg-mime default captureage.desktop x-scheme-handler/captureage
```

`aoe2de://` links use the game's separate `AOEURLHelper.exe` and are not
handled by this launcher.

## Custom Steam installations and troubleshooting

Protontricks discovers Steam libraries and the game's selected Proton
version. Its standard overrides apply, for example:

```sh
STEAM_DIR='/path/to/Steam' captureage
PROTON_VERSION='Proton - Experimental' captureage
STEAM_COMPAT_DATA_PATH='/path/to/compatdata/813780' captureage
```

Use the same overrides for `captureage --register` if necessary. This
package targets native Steam and native Protontricks; Flatpak Steam needs
separate sandbox permissions and is not covered by these launch commands.

If a missing Visual C++ runtime error occurs, close the game and install
the runtime in its prefix with `protontricks 813780 vcrun2022`. Older
setups may need `protontricks 813780 d3dcompiler_47` or Windows 10 selected
with `protontricks 813780 winecfg`. Apply these only when troubleshooting
the corresponding issue: they change the game's shared prefix.

Update the system installation through the AUR package. CaptureAge's own
Windows updater cannot replace root-owned files in `/opt/captureage`.
User settings remain in the game's Proton prefix. Removing the package
does not remove those settings or the optional per-user registry entries.

## Further reading

- [CaptureAge documentation](https://captureage.com/cade/docs)
- [Protontricks usage and environment variables](https://github.com/Matoking/protontricks)
