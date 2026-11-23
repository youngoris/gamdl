"""Offline profile routing, persistent selection and legacy conversion tests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import click
from click.testing import CliRunner

from gamdl.cli.profile_migration import convert
from gamdl.cli.profiles import (
    ProfileRoutingMixin,
    activate_profile,
    current_profile,
    resolve_config_path,
)

URL = "https://music.apple.com/us/song/example/123"


class ProfileGroup(ProfileRoutingMixin, click.Group):
    pass


@click.group(cls=ProfileGroup)
@click.option("--config", "config_file", type=click.Path(path_type=Path))
@click.option("--no-config", is_flag=True)
@click.pass_context
def app(ctx, config_file, no_config):
    ctx.obj = {"config": str(config_file), "no_config": no_config}


@app.command()
@click.option("--count-first/--stream-discovery", default=False)
@click.option("--song-codec-priority", default="aac")
@click.argument("urls", nargs=-1)
@click.pass_context
def rip(ctx, count_first, song_codec_priority, urls):
    click.echo(
        json.dumps(
            {
                **ctx.obj,
                "count_first": count_first,
                "codec": song_codec_priority,
                "urls": urls,
            }
        )
    )


@app.command("config")
@click.argument("action")
@click.pass_context
def inspect_config(ctx, action):
    click.echo(json.dumps({**ctx.obj, "action": action}))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in ("ab", "pl"):
            (self.root / f"config-{name}.yml").write_text("config_version: 1\n")
        self.mock_root = patch("gamdl.cli.profiles.profile_dir", return_value=self.root)
        self.mock_root.start()
        self.addCleanup(self.mock_root.stop)

    def invoke(self, args):
        result = CliRunner().invoke(app, args)
        self.assertEqual(result.exit_code, 0, result.output)
        return json.loads(result.output)

    def test_alias_selects_profile_before_command_defaults(self):
        for name in ("ab", "pl"):
            with self.subTest(name=name):
                result = self.invoke([name, URL])
                self.assertEqual(
                    result["config"], str(self.root / f"config-{name}.yml")
                )
                self.assertEqual(result["urls"], [URL])
                self.assertTrue(result["count_first"])

    def test_profile_accepts_rip_options_before_url(self):
        result = self.invoke(["pl", "--song-codec-priority", "aac-web", URL])
        self.assertEqual(result["codec"], "aac-web")

    def test_explicit_rip_is_not_inserted_twice(self):
        self.assertEqual(self.invoke(["ab", "rip", URL])["urls"], [URL])

    def test_official_profile_command_is_preserved(self):
        result = self.invoke(["pl", "config", "show"])
        self.assertEqual(result["action"], "show")
        self.assertEqual(result["config"], str(self.root / "config-pl.yml"))

    def test_legacy_url_and_isrc_inputs(self):
        self.assertEqual(self.invoke([URL])["urls"], [URL])
        self.assertEqual(
            self.invoke(["isrc:USUM71703085"])["urls"], ["isrc:USUM71703085"]
        )

    def test_explicit_custom_config_is_preserved(self):
        result = self.invoke(["--config", "custom.yml", URL])
        self.assertEqual(result["config"], "custom.yml")

    def test_conflicting_config_flags_are_rejected(self):
        for prefix in (
            ["--config", "other.yml"],
            ["--config=other.yml"],
            ["--no-config"],
        ):
            result = CliRunner().invoke(app, [*prefix, "pl", URL])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("cannot be combined", result.output)

    def test_missing_profile_has_actionable_error(self):
        (self.root / "config-pl.yml").unlink()
        result = CliRunner().invoke(app, ["pl", URL])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("profiles migrate", result.output)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in ("ab", "pl"):
            (self.root / f"config-{name}.yml").write_text(name)

    def test_persistent_switch_resolves_target_without_replacing_link(self):
        for name in ("pl", "ab"):
            activate_profile(name, self.root, validator=lambda path: None)
            self.assertEqual(current_profile(self.root), name)
            self.assertTrue((self.root / "config.yml").is_symlink())
            self.assertEqual(
                resolve_config_path(self.root / "config.yml"),
                self.root / f"config-{name}.yml",
            )

    def test_invalid_profile_leaves_previous_default_untouched(self):
        activate_profile("ab", self.root, validator=lambda path: None)

        def reject(path):
            raise ValueError("invalid YAML")

        with self.assertRaises(click.ClickException):
            activate_profile("pl", self.root, validator=reject)
        self.assertEqual(current_profile(self.root), "ab")

    def test_existing_regular_config_is_backed_up(self):
        default = self.root / "config.yml"
        default.write_text("original configuration")
        activate_profile("pl", self.root, validator=lambda path: None)
        backups = list((self.root / "backups").glob("config-before-profile-*.yml"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "original configuration")
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_atomic_replace_failure_keeps_previous_link(self):
        activate_profile("ab", self.root, validator=lambda path: None)
        with patch("gamdl.cli.profiles.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(click.ClickException):
                activate_profile("pl", self.root, validator=lambda path: None)
        self.assertEqual(current_profile(self.root), "ab")
        self.assertFalse(list(self.root.glob(".profile-*")))

    def test_unknown_name_and_missing_file_do_not_write(self):
        with self.assertRaises(click.BadParameter):
            activate_profile("invalid", self.root, validator=lambda path: None)
        (self.root / "config-pl.yml").unlink()
        with self.assertRaises(click.ClickException):
            activate_profile("pl", self.root, validator=lambda path: None)
        self.assertFalse((self.root / "config.yml").exists())


class ConversionTests(unittest.TestCase):
    def test_legacy_best_uploaded_video_quality_becomes_default_priority(self):
        for old in (
            {},
            {"uploaded_video_quality": "best"},
            {"uploaded_video_quality": "null"},
        ):
            with self.subTest(old=old):
                self.assertIsNone(
                    convert(old, Path("/example/home"))["download"][
                        "uploaded_video_quality"
                    ]
                )
        explicit = convert(
            {"uploaded_video_quality": "720pHdVideo"}, Path("/example/home")
        )
        self.assertEqual(explicit["download"]["uploaded_video_quality"], "720pHdVideo")

    def test_legacy_values_and_relative_paths_are_preserved(self):
        old = {
            "cookies_path": "./Cookies/apple.com_cookies.txt",
            "output_path": "./Music/GAMDL_Downloads",
            "song_codec_piority": "aac-web",
            "cover_size": "2000",
            "overwrite": "false",
            "playlist_file_template": "Playlists/{playlist_title}",
            "single_disc_file_template": "{playlist_track:03d} {title} - {artist}",
        }
        result = convert(old, Path("/example/home"))
        self.assertEqual(
            result["auth"]["cookies_path"],
            "/example/home/Cookies/apple.com_cookies.txt",
        )
        self.assertEqual(
            result["output"]["directory"], "/example/home/Music/GAMDL_Downloads"
        )
        self.assertEqual(result["download"]["song_codec_priority"], ["aac-web"])
        self.assertFalse(result["download"]["overwrite"])
        self.assertEqual(result["output"]["embedded_cover_size"], 2000)
        self.assertEqual(result["output"]["playlist_folder_template"], "Playlists")
        self.assertEqual(result["output"]["playlist_file_template"], "{playlist_title}")
        self.assertEqual(
            result["output"]["single_disc_file_template"],
            old["single_disc_file_template"],
        )

    def test_legacy_database_is_not_reused(self):
        result = convert({"database_path": "old.sqlite3"}, Path("/example/home"))
        self.assertFalse(result["database"]["disabled"])
        self.assertEqual(result["database"]["path"], "/example/home/old-v4.sqlite3")

    def test_legacy_all_tag_exclusion_becomes_no_tag(self):
        result = convert({"exclude_tags": "all"}, Path("/example/home"))
        self.assertTrue(result["output"]["no_tag"])
        self.assertEqual(result["output"]["exclude_tags"], [])


if __name__ == "__main__":
    unittest.main()
