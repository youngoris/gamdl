# Profiles fork

The custom CLI and migration guide are in [docs/profiles.md](docs/profiles.md).
The upstream project remains `glomatico/gamdl`; this fork keeps its MIT license.

Development happens on `profiles`. Keep `4.0` identical to upstream and merge
upstream updates with `bash scripts/sync-upstream.sh` or the manual sync Action.
No scheduled workflow is installed.

Validation before publication:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_profiles.py -v
```

The 53 profile, playlist naming, count-first and upstream CLI runner tests have
passed against the actual Gamdl 4.1.1 runtime and its native extension. Both
migrated configurations passed the official validator. The existing upstream CI
runs the full suite on Python 3.10–3.14; check its result after publishing.
