# CaptureAge development guide

Contributor guidance for packaging, validation, AUR publishing, and automation.

[Back to README](README.md) · [Advanced user reference](TECHNICAL.md)

## Local packaging

On Arch Linux with the standard `base-devel` tools:

```sh
git clone https://github.com/Firstp1ck/captureage-bin.git
cd captureage-bin
makepkg -si
```

`makepkg` downloads a specific release from CaptureAge and verifies its SHA-256
checksum. The build and package functions only extract and copy files; they do
not run Windows binaries or modify a Wine/Proton prefix. The Windows payload is
installed under `/opt/captureage` and launched with Protontricks in AoE II: DE's
Steam prefix (App ID `813780`).

## Game-path implementation

The launcher uses Protontricks to locate the game's Steam library and selected
Proton prefix, including custom `STEAM_DIR` and `STEAM_COMPAT_DATA_PATH` settings.
`configure_game.py` resolves the actual installation through the prefix's Wine
drive mappings and repairs a missing or invalid `lastUsedGameDirectory` setting,
including on first launch. A valid existing directory is preserved; existing
settings are backed up before repair and all other settings are retained.
See [the technical reference](TECHNICAL.md#game-path-settings-and-recovery) for
storage locations and user recovery guidance.

## Maintaining and submitting to AUR

The package name is `captureage-bin` because upstream distributes binaries.
Only commit packaging files to AUR. CaptureAge's terms prohibit
redistribution of the application; do not upload the downloaded archive
or built binary package. The recipe fetches the payload from upstream.

For a new release, update `pkgver`, reset `pkgrel` to `1`, update the
upstream archive checksum and the release version in `README.md`, then run:

```sh
updpkgsums
makepkg --printsrcinfo > .SRCINFO
makepkg --verifysource --force
makepkg --force
namcap PKGBUILD captureage-bin-*.pkg.tar.zst
```

Use the version-specific API URL in `PKGBUILD`. The `/latest` endpoint
changes over time, and direct Azure CDN redirects contain tokens that
expire after about 15 minutes. The version-specific API returns a fresh
redirect for the requested filename.

## GitHub and AUR automation

This is one source repository for both destinations:

- GitHub `main` contains packaging, maintenance scripts, tests and workflows.
- AUR `master` contains only `PKGBUILD`, `.SRCINFO`, the launcher, desktop
  entry, icon, game-path helper, registry file, license and `README.md`.
  Its Git history is preserved.

[Update and publish CaptureAge](https://github.com/Firstp1ck/captureage-bin/blob/main/.github/workflows/update.yml) runs daily at
03:17 UTC and can also be started from GitHub's Actions tab. On a new upstream
release it downloads the exact version, checks the version inside the archive,
updates `pkgver`, resets `pkgrel` to `1`, recalculates every source checksum,
updates `README.md` and regenerates `.SRCINFO` with Arch's `makepkg`.

Every run validates the source checksums, builds the package as a non-root
user in Arch Linux, checks metadata, runs the updater/publisher tests, and
lints the launcher and desktop entry. The build skips runtime dependency
installation because it only repackages files and does not run the app.
`namcap` warnings about no ELF binaries and dynamically invoked dependencies
are expected for the Windows payload; errors fail validation.

After validation, a separate job commits updates to GitHub and pushes the
packaging files to AUR. It retries the AUR sync even when the upstream
version is unchanged, so a failed AUR push can recover on the next run.
Normal pushes to `main` also validate and publish manual package changes.
Pull requests run validation only. Pushes made by `GITHUB_TOKEN` do not
trigger another workflow, so the scheduled job publishes directly.

### Initial setup

1. Create `Firstp1ck/captureage-bin` on GitHub and push this directory as its
   `main` branch. Use a separate Git repository for this directory.
2. Register a publishing SSH public key in your AUR account (`Firstpick`).
   The corresponding private key must work without an interactive passphrase.
3. Add these GitHub Actions repository secrets:
   - `AUR_SSH_PRIVATE_KEY`: the private key registered with AUR.
   - `AUR_KNOWN_HOSTS`: the trusted `aur.archlinux.org` SSH host-key entries.
4. Enable Actions. Run **Update and publish CaptureAge** manually once to
   create/synchronize the AUR package. Future runs are automatic.

For an existing local AUR SSH setup, the secrets can be uploaded without
printing the private key:

```sh
gh secret set AUR_SSH_PRIVATE_KEY --repo Firstp1ck/captureage-bin < ~/.ssh/aur
ssh-keygen -F aur.archlinux.org -f ~/.ssh/known_hosts |
  gh secret set AUR_KNOWN_HOSTS --repo Firstp1ck/captureage-bin
```

Only use a key whose public half is registered to the account that owns or
co-maintains `captureage-bin`. The workflow never force-pushes AUR and refuses
to replace a newer version already there. If AUR has a newer manual update,
incorporate it into GitHub before rerunning publishing. If GitHub rejects a
push because its branch changed, the run stops and the next run starts fresh.

GitHub schedules run on the default branch, can be delayed, and may be
disabled after 60 days without repository activity in public repositories.
Keep Actions enabled and check failed-run notifications. Branch protection
must allow the workflow's `GITHUB_TOKEN` to push updates to `main`.

### Automation health monitoring

The **Automation health** workflow runs daily at 04:47 UTC and after completed
update/publishing runs. It records the last successful upstream version check,
the last successful publishing run, workflow state and links to the relevant
runs in [`health.json`](https://github.com/Firstp1ck/captureage-bin/blob/automation-health/health.json)
on the separate `automation-health` branch. The README badge links to that status.
Successful package builds triggered by pushes do not count as upstream checks.

An upstream check older than 48 hours, a failed update/publishing run, a disabled
update workflow, or a run queued/running for more than two hours produces an
alert. The monitor opens or updates one **CaptureAge automation needs attention**
issue, reopens it on another incident, and closes it once health recovers.
The health workflow also fails when unhealthy, making its badge and ordinary
GitHub failed-run notifications reflect the problem. Watch repository issues
or configure GitHub notifications to receive alerts through your preferred channel.

Each health evaluation commits its timestamp to `automation-health`, keeping
repository activity separate from package releases. These commits do not trigger
the package workflow. On initial setup, run **Update and publish CaptureAge**
manually to establish the first upstream check; a new repository has a 48-hour
initialization grace period.

The watchdog uses GitHub Actions too: it cannot execute if all Actions are
disabled or GitHub is unavailable. For an independent scheduler, the same
read-only check is available locally or on another host with Python and `gh`:

```sh
python3 tools/health.py --repo Firstp1ck/captureage-bin
```

It prints JSON and exits with status `1` for unhealthy automation, without
creating issues or commits. An API/connection error also returns a nonzero
exit status. The default 48-hour threshold can be changed with `--stale-hours`.

### Local maintenance

```sh
python3 tools/update.py --check   # Read-only upstream version check
python3 tools/update.py           # Prepare an available upstream update
bash tools/validate.sh            # Validate and build on Arch Linux
python3 tools/publish_aur.py --dry-run
python3 tools/publish_aur.py       # Publish using your local AUR SSH setup
```

For launcher, desktop, registry or packaged README changes without an upstream
version change, increment `pkgrel`, run `updpkgsums`, regenerate `.SRCINFO`,
and commit the changes to `main`. The daily updater keeps your `pkgrel` when
no newer upstream version exists. Upstream license changes require a manual
review and update of `LICENSE`.

`README.md` is included in the package and has a source checksum. Keep the exact
`Unofficial AUR packaging of CaptureAge:DE <version>.` sentence: the updater uses
it to replace the version. `TECHNICAL.md` and `DEVELOPMENT.md` remain on GitHub;
the README links to their GitHub URLs so they are accessible from the installed
copy and the AUR repository.

## Research sources

Sources recorded in the original README on October 3, 2026:

- [Official download and offline installer](https://captureage.com/cade/get)
- [Official release notes](https://captureage.com/cade/updates)
- [Official documentation](https://captureage.com/cade/docs)
- [CaptureAge terms and conditions](https://captureage.com/terms)
- [Protontricks usage and environment variables](https://github.com/Matoking/protontricks)
- [Arch Protontricks package](https://archlinux.org/packages/extra/any/protontricks/)
- [Community Linux setup notes](https://gist.github.com/Kjir/dadb0a2bc1a71aa265cfdbecaf7569b8)
- [Community installer](https://github.com/aoe2ct/cade-linux-installer)

The archive's `resources/app/package.json` confirms its release version
and CaptureAge's proprietary copyright notice. Bundled third-party license
notices are preserved in `/opt/captureage`; upstream terms are installed
under `/usr/share/licenses/captureage-bin/LICENSE`.
