"""Failed-only retries must never enumerate the original playlist or album."""

import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import click
import yaml
from click.testing import CliRunner
from rich.console import Console

from gamdl.cli.commands.retry import run_retries
from gamdl.cli.failure_queue import FailureQueue, restore_media, ticket_for
from gamdl.cli.main import main
from gamdl.cli.options import RipOptions
from gamdl.download import DownloadResult, MediaStatus
from gamdl.media import MediaSource, Playlist, Song, SongCodec
from gamdl.media.playlist import PlaylistMetadata


def fixture(root, *, index=33, media_id="423354412"):
    api = Mock(active_subscription=True, storefront="cn")
    api.get_playlist = AsyncMock()
    api.get_library_playlist = AsyncMock()
    api.get_song = AsyncMock(
        return_value={
            "data": [{"id": media_id, "type": "songs", "attributes": {"name": "Road"}}],
        }
    )
    playlist = Playlist("p.original", api=api, is_library=True)
    playlist._memo.seed(
        "metadata", PlaylistMetadata("p.original", "RMH精选2-2", "Andre")
    )
    media = Song.from_metadata(
        {"id": media_id, "type": "songs", "attributes": {"name": "Road"}},
        api=api,
    )
    media.source = MediaSource("playlist_tracks", "p.original", index, playlist)
    config = root / "config-pl.yml"
    if not config.exists():
        config.write_text(
            yaml.safe_dump(
                {
                    "auth": {"mode": "cookies", "cookies_path": "unused.txt"},
                    "output": {"directory": str(root / "changed-config-directory")},
                }
            )
        )
    options = RipOptions(
        output_dir=str(root / "original-directory"),
        song_codec_priority=("aac-web",),
        album_folder_template="Playlists/{playlist_title}",
        single_disc_file_template="{playlist_track:03d} {title} - {artist}",
    )
    return api, media, options, config


class FailureQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.api, self.media, self.options, self.config = fixture(self.root)
        self.queue = FailureQueue(self.root / "failed.json")
        self.ticket = ticket_for(self.media, "Road", self.options, self.config)

    def test_new_queue_is_read_only_until_an_attempt_starts(self):
        self.assertEqual(self.queue.items(), [])
        self.assertFalse(self.queue.path.exists())

    def test_attempt_survives_restart_and_completion_removes_only_that_ticket(self):
        key = self.queue.begin(self.ticket)
        self.queue.fail(key, "Temporary API failure")
        restarted = FailureQueue(self.queue.path)
        self.assertEqual(restarted.items()[0]["status"], "failed")
        restarted.begin(restarted.items()[0])
        self.assertEqual(restarted.items()[0]["attempts"], 2)
        restarted.complete(key)
        self.assertEqual(self.queue.items(), [])
        if os.name != "nt":
            self.assertEqual(self.queue.path.stat().st_mode & 0o777, 0o600)

    def test_separate_queue_instances_do_not_lose_each_others_entries(self):
        self.queue.begin(self.ticket)
        _, media, options, config = fixture(self.root, index=1, media_id="different")
        other = ticket_for(media, "Other", options, config)
        FailureQueue(self.queue.path).begin(other)
        self.assertEqual(len(self.queue.items()), 2)
        self.queue.complete(self.ticket["key"])
        self.assertEqual(self.queue.items()[0]["id"], "different")

    def test_duplicate_song_occurrences_keep_distinct_original_indices(self):
        self.queue.begin(self.ticket)
        self.media.source = MediaSource(
            "playlist_tracks", "p.original", 0, self.media.source.playlist
        )
        first = ticket_for(self.media, "Road", self.options, self.config)
        self.queue.begin(first)
        self.assertEqual(
            {item["source"]["index"] for item in self.queue.items()}, {0, 33}
        )

    def test_authentication_tokens_and_cookie_paths_are_not_recorded(self):
        self.options.media_user_token = "DO_NOT_SAVE_TOKEN"
        self.options.cookies_path = "/DO_NOT_SAVE_COOKIES"
        ticket = ticket_for(self.media, "Road", self.options, self.config)
        text = json.dumps(ticket)
        self.assertNotIn("DO_NOT_SAVE_TOKEN", text)
        self.assertNotIn("DO_NOT_SAVE_COOKIES", text)

    def test_invalid_existing_queue_is_not_overwritten(self):
        self.queue.path.write_text("{broken")
        with self.assertRaises(ValueError):
            self.queue.begin(self.ticket)
        self.assertEqual(self.queue.path.read_text(), "{broken")


class RetryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.api, self.media, self.options, self.config = fixture(self.root)
        self.queue = FailureQueue(self.root / "failed.json")
        self.ticket = ticket_for(self.media, "Road", self.options, self.config)
        self.queue.begin(self.ticket)
        self.queue.fail(self.ticket["key"], "Temporary failure")

    async def execute(self, *, fail=False, abort=False):
        received = []

        async def download(media, **kwargs):
            received.append(media.id)
            self.assertEqual(media.source.index, 33)
            metadata = await media.source.playlist.get_metadata()
            self.assertEqual(metadata.title, "RMH精选2-2")
            if abort:
                raise click.Abort()
            if fail:
                raise ValueError("DO_NOT_SAVE_SIGNED_URL")
            return DownloadResult(
                media.id,
                self.root / "original-directory" / "034 Road.m4a",
                MediaStatus.DOWNLOADED,
                1,
                SongCodec.AAC_WEB,
            )

        downloader = Mock(itunes_api=None, download=AsyncMock(side_effect=download))
        console = Console(file=StringIO(), force_terminal=False)
        resolver = Mock(side_effect=AssertionError("Collections must not be traversed"))
        with (
            patch(
                "gamdl.cli.commands.runner._authenticate",
                new=AsyncMock(return_value=(self.api, None)),
            ),
            patch("gamdl.cli.commands.runner.create_drm", new=AsyncMock()),
            patch("gamdl.cli.commands.runner.output_console", return_value=console),
            patch("gamdl.cli.commands.runner.Downloader", return_value=downloader),
            patch("gamdl.cli.commands.runner.MediaResolver", resolver),
        ):
            result = await run_retries(self.queue.items(), self.queue)
        resolver.assert_not_called()
        self.api.get_playlist.assert_not_awaited()
        self.api.get_library_playlist.assert_not_awaited()
        self.api.iter_playlist_tracks.assert_not_called()
        self.api.iter_album_tracks.assert_not_called()
        return result, received

    async def test_retry_downloads_only_failed_id_and_clears_queue(self):
        result, received = await self.execute()
        self.assertEqual(result, 0)
        self.assertEqual(received, ["423354412"])
        self.assertEqual(self.queue.items(), [])

    async def test_repeated_failure_stays_queued_without_sensitive_exception_text(self):
        result, received = await self.execute(fail=True)
        self.assertEqual(result, 1)
        self.assertEqual(received, ["423354412"])
        remaining = self.queue.items()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["status"], "failed")
        self.assertEqual(remaining[0]["attempts"], 2)
        self.assertNotIn("DO_NOT_SAVE_SIGNED_URL", self.queue.path.read_text())

    async def test_interrupted_retry_retains_the_inflight_ticket(self):
        with self.assertRaises(click.Abort):
            await self.execute(abort=True)
        self.assertEqual(len(self.queue.items()), 1)
        self.assertEqual(self.queue.items()[0]["status"], "pending")


class RetryCommandTests(unittest.TestCase):
    def test_list_and_id_selection_do_not_authenticate_or_expand_collections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _, media, options, config = fixture(root, index=0)
            queue = FailureQueue(root / "failed.json")
            queue.begin(ticket_for(media, "Road", options, config))
            with (
                patch(
                    "gamdl.cli.main.default_config_path",
                    return_value=root / "unused.yml",
                ),
                patch(
                    "gamdl.cli.commands.retry.run_retries",
                    new=AsyncMock(return_value=0),
                ) as run,
            ):
                result = CliRunner().invoke(
                    main, ["retry", "--queue", str(queue.path), "--list"]
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("423354412", result.output)
                self.assertIn("001", result.output)
                run.assert_not_awaited()
                selected = CliRunner().invoke(
                    main, ["retry", "--queue", str(queue.path), "--id", "423354412"]
                )
                self.assertEqual(selected.exit_code, 0, selected.output)
                self.assertEqual(run.await_args.args[0][0]["id"], "423354412")


if __name__ == "__main__":
    unittest.main()
