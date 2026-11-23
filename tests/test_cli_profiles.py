"""Integration with upstream's real root CLI; no Apple Music requests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from gamdl.cli.main import main
from gamdl.cli.profiles import current_profile

URL = "https://music.apple.com/us/song/example/123"


class CliProfileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in ("ab", "pl"):
            (self.root / f"config-{name}.yml").write_text(
                yaml.safe_dump({"output": {"directory": str(self.root / name)}})
            )
        self.patches = [
            patch("gamdl.cli.profiles.profile_dir", return_value=self.root),
            patch(
                "gamdl.cli.main.default_config_path",
                return_value=self.root / "config.yml",
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_explicit_alias_and_default_route_reach_official_downloader(self):
        received = []

        async def fake_run(options):
            received.append(str(options.output_dir))
            return 0

        runner = CliRunner()
        with patch("gamdl.cli.commands.runner.run_rip", side_effect=fake_run):
            once = runner.invoke(main, ["pl", URL])
            self.assertEqual(once.exit_code, 0, once.output)
            self.assertEqual(received[-1], str(self.root / "pl"))
            selected = runner.invoke(main, ["use", "ab"])
            self.assertEqual(selected.exit_code, 0, selected.output)
            default = runner.invoke(main, [URL])
            self.assertEqual(default.exit_code, 0, default.output)
            self.assertEqual(received[-1], str(self.root / "ab"))
            self.assertTrue((self.root / "config.yml").is_symlink())

    def test_invalid_default_does_not_block_switch_to_valid_profile(self):
        (self.root / "config.yml").write_text("output: [invalid]\n")
        result = CliRunner().invoke(main, ["use", "pl"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(current_profile(self.root), "pl")

    def test_migration_validates_and_preserves_ini(self):
        for path in self.root.glob("config-*.yml"):
            path.unlink()
        (self.root / "config.ini").write_text(
            "[gamdl]\noutput_path = ./Music\nsong_codec_piority = aac-web\n"
        )
        (self.root / "config-pl.ini").write_text(
            "[gamdl]\noutput_path = ./Music\nalbum_folder_template = Playlists/{playlist_title}\n"
        )
        result = CliRunner().invoke(
            main, ["profiles", "migrate", "--legacy-cwd", str(self.root)]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(current_profile(self.root), "ab")
        self.assertTrue((self.root / "config.ini").is_file())
        self.assertTrue((self.root / "config-pl.ini").is_file())
        repeated = CliRunner().invoke(main, ["profiles", "migrate"])
        self.assertNotEqual(repeated.exit_code, 0)
        self.assertIn("--force", repeated.output)


if __name__ == "__main__":
    unittest.main()
