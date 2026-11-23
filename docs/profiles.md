# Album and playlist profiles

This fork keeps the official downloader and adds profile routing directly to
the `gamdl` CLI. Package upgrades retain the commands; no executable wrapper is
installed into the virtual environment.

| Command | Behavior |
| --- | --- |
| `gamdl ab URL` | Download once using `~/.gamdl/config-ab.yml` |
| `gamdl pl URL` | Download once using `~/.gamdl/config-pl.yml` |
| `gamdl use ab` | Make album settings the persistent default |
| `gamdl use pl` | Make playlist settings the persistent default |
| `gamdl profiles` | Show the default and both configuration paths |
| `gamdl URL` | Download using the current default |
| `gamdl pl config show` | Inspect the playlist settings |

Bare `gamdl ab` and `gamdl pl` also switch the default. The original upstream
subcommands remain available, including `gamdl rip URL` and
`gamdl --config PATH rip URL`. Explicit `--config` or `--no-config` cannot be
combined with `ab`/`pl`. `GAMDL_CONFIG` retains its upstream override behavior;
an explicit `ab`/`pl` command overrides that environment variable.

Album/playlist aliases count all selected tracks before downloads begin. The
summary lists each album or playlist and the combined total, including repeated
playlist occurrences. The overall progress then shows completed/total tracks.
Use `--stream-discovery` after `ab`/`pl` to keep the original streaming mode.

Playlist directory names and `{playlist_track:03d}` filenames use the actual
playlist metadata even when `output.save_playlist` is false. That setting only
controls creation of the `.m3u8` file.

## Install and migrate

Install from this fork's `profiles` branch. Building from Git requires Rust:

```bash
python -m pip install 'gamdl[widevine] @ git+https://github.com/YOUR_USER/gamdl.git@profiles'
gamdl profiles migrate
gamdl profiles
```

Migration reads `config-ab.ini` (falling back to `config.ini`) and
`config-pl.ini`. It preserves the output directory, codecs, cover sizes,
lyrics settings and filename templates. Relative paths are resolved against
the home directory, matching the original invocation; use
`gamdl profiles migrate --legacy-cwd PATH` for another original directory.

The album profile is the initial default. Existing INI files and downloaded
music stay in place. Both YAML candidates are validated before either is
installed. Existing YAML profiles require `--force`; originals are retained
under `~/.gamdl/backups/`. A failed write restores the previous profiles.
The default `config.yml` is an atomically replaced symbolic link, and the CLI
loads its resolved target so configuration normalization preserves the link.
This selector supports macOS and Linux; Windows requires symlink privileges.

For example, the migrated playlist layout can be
`Playlists/{playlist_title}/{playlist_track:03d} {title} - {artist}.m4a`, while
album files retain `{album_artist}/{album}/{track:02d} {title}.m4a`.

## Keep the fork current

The fork's `4.0` branch tracks `glomatico/gamdl:4.0`. The `profiles` branch
contains the custom commands and merges upstream updates:

```bash
bash scripts/sync-upstream.sh
```

The same operation is available in **Actions → Sync upstream into profiles →
Run workflow**. It runs only when requested. Merge conflicts stop the operation
for review; neither branch is force-pushed. Upstream's full CI remains enabled.
To invoke the manual workflow with GitHub CLI:

```bash
gh workflow run sync-upstream.yml --ref profiles
```

## Retry failed tracks only

Failed and interrupted attempts are recorded automatically in the private
`~/.gamdl/failed-downloads.json` queue. Successful downloads and existing files
that finish normally are removed from the queue.

| Command | Behavior |
| --- | --- |
| `gamdl retry` | Retry all recorded failures using their original profiles |
| `gamdl retry --list` | Show failed IDs and original playlist positions |
| `gamdl retry --id 423354412` | Retry only a recorded media ID |
| `gamdl pl retry` | Retry only playlist-profile failures |
| `gamdl ab retry` | Retry only album-profile failures |

Retries reconstruct individual media IDs and preserve the original playlist
name, one-based position and download settings. They never enumerate the
original playlist, albums or artist collections. If a playlist name was not
available before an interruption, a single playlist-metadata request may be
needed; tracks are still resolved individually. Authentication comes from the
current local configuration; cookies, user tokens and playback URLs are not
stored in the retry queue. Previously unrecorded failures cannot be inferred
automatically from an old terminal log.
