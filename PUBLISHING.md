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

The full 779-test suite, including failed-only retries, passed on Python
3.10–3.14 against the actual Gamdl 4.1.1 runtime and native extension. Both
migrated configurations passed the official validator. Upstream CI remains
enabled; check its result after publishing.
