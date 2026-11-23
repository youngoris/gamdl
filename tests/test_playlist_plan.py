"""Regression tests for playlist naming without M3U and count-first downloads."""

import asyncio
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from rich.console import Console

from gamdl.cli.commands.runner import run_rip
from gamdl.cli.options import RipOptions
from gamdl.cli.progress import RipProgress
from gamdl.download import Downloader, DownloadResult, MediaStatus, PathFormatter, Tags
from gamdl.media import MediaSource, Playlist, Song, SongCodec


class PlaylistNamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_name_and_original_index_do_not_require_playlist_file(self):
        api = Mock()
        api.get_library_playlist = AsyncMock(return_value={"data": [{"attributes": {"name": "RMH精选2-2", "curatorName": "Andre"}}]})
        playlist = Playlist("p.test", api=api, is_library=True)
        media = Song.from_metadata({"id": "123", "type": "songs", "attributes": {"name": "Example"}}, api=api)
        media.source = MediaSource("playlist_tracks", "p.test", 0, playlist)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Playlists/RMH精选2-2/001 Example - Artist.m4a"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"existing media")
            resolver = Mock()
            resolver.get_tags = AsyncMock(return_value=Tags(title="Example", artist="Artist", album="Album", album_artist="Artist", track_number=2))
            formatter = PathFormatter(root, album_folder_template="Playlists/{playlist_title}", single_disc_file_template="{playlist_track:03d} {title} - {artist}")
            downloader = Downloader(path_formatter=formatter, tag_resolver=resolver)
            result = await downloader.download(media, codec_priority=[SongCodec.AAC_WEB], save_playlist=False)
            self.assertEqual(result.path, target)
            self.assertEqual(result.status, MediaStatus.SKIPPED)
            api.get_library_playlist.assert_awaited_once()
            self.assertFalse(list(root.rglob("*.m3u8")))

    async def test_album_paths_do_not_fetch_playlist_metadata(self):
        api = Mock()
        api.get_playlist = AsyncMock()
        playlist = Playlist("pl.test", api=api)
        media = Song.from_metadata({"id": "123", "type": "songs", "attributes": {"name": "Example"}}, api=api)
        media.source = MediaSource("playlist_tracks", "pl.test", 0, playlist)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Artist/Album/02 Example.m4a"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"existing media")
            resolver = Mock(get_tags=AsyncMock(return_value=Tags(title="Example", artist="Artist", album="Album", album_artist="Artist", track_number=2)))
            downloader = Downloader(path_formatter=PathFormatter(root), tag_resolver=resolver)
            result = await downloader.download(media, codec_priority=[SongCodec.AAC_WEB], save_playlist=False)
            self.assertEqual(result.path, target)
            api.get_playlist.assert_not_awaited()


class CountFirstTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_all_albums_before_any_download(self):
        api = Mock(active_subscription=True, storefront="cn")
        songs = {}
        for album, count in (("first", 2), ("second", 3)):
            songs[album] = []
            for index in range(count):
                song = Song.from_metadata({"id": f"{album}-{index}", "type": "songs", "attributes": {"name": f"Song {index}", "albumName": album}}, api=api)
                song.source = MediaSource("album_tracks", album, index)
                songs[album].append(song)
        resolved = []

        class Resolver:
            def __init__(self, api, **kwargs):
                pass

            async def from_url(self, url):
                for song in songs[url]:
                    yield song
                resolved.append(url)

        received = []
        async def download(media, **kwargs):
            self.assertEqual(resolved, ["first", "second"])
            received.append(media.id)
            return DownloadResult(media.id, Path("test.m4a"), MediaStatus.DOWNLOADED, 1, SongCodec.AAC_WEB)

        output = StringIO()
        console = Console(file=output, force_terminal=False)
        progress = RipProgress(enabled=False, console=console)
        downloader = Mock(itunes_api=None, download=AsyncMock(side_effect=download))
        with (
            patch("gamdl.cli.commands.runner._authenticate", new=AsyncMock(return_value=(api, None))),
            patch("gamdl.cli.commands.runner.create_drm", new=AsyncMock()),
            patch("gamdl.cli.commands.runner.MediaResolver", Resolver),
            patch("gamdl.cli.commands.runner.Downloader", return_value=downloader),
            patch("gamdl.cli.commands.runner.output_console", return_value=console),
            patch("gamdl.cli.commands.runner.RipProgress", return_value=progress),
        ):
            code = await run_rip(RipOptions(("first", "second"), output_dir="Test Music", no_progress=True, count_first=True))
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(len(received), 5)
        text = output.getvalue()
        self.assertIn("2 张专辑，5 首曲目", text)
        self.assertLess(text.index("5 首曲目"), text.index("downloaded"))


if __name__ == "__main__":
    unittest.main()
