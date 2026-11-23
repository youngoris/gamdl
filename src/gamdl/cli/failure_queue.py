"""Private, atomic retry tickets for individual media occurrences."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from gamdl.media import MediaSource, Playlist
from gamdl.media.base import Media
from gamdl.media.playlist import PlaylistMetadata

OPTION_NAMES = (
    "output_dir", "temp_path", "overwrite", "use_album_date", "song_codec",
    "song_codec_priority", "music_video_codec", "music_video_codec_priority",
    "music_video_container", "max_height", "uploaded_video_quality", "save_cover",
    "cover_format", "embedded_cover_size", "file_cover_size", "save_playlist",
    "no_tag", "exclude_tags", "no_lyrics_file", "lyrics_file_format",
    "embedded_lyrics_format", "date_tag_template", "album_folder_template",
    "compilation_folder_template", "no_album_folder_template", "single_disc_file_template",
    "multi_disc_file_template", "no_album_file_template", "playlist_folder_template",
    "playlist_file_template", "truncate", "database", "database_path", "save_metadata",
    "concurrent_downloads", "segment_concurrency", "language", "storefront",
    "ask_song_codec", "ask_music_video_streams", "ask_uploaded_video_quality",
)


def default_queue_path() -> Path:
    return Path.home() / ".gamdl" / "failed-downloads.json"


def ticket_for(media, title, options, config_path):
    source = media.source
    context = None
    if source is not None:
        context = {"kind": source.kind, "id": source.id, "index": source.index}
        playlist = source.playlist
        if playlist is not None:
            saved = {"id": playlist.id, "is_library": playlist.is_library}
            cached = getattr(getattr(playlist, "_memo", None), "values", {}).get("metadata")
            if cached is None:
                cached = getattr(playlist, "saved_metadata", None)
            if cached is not None:
                saved.update(title=cached.title, artist=cached.artist)
            context["playlist"] = saved
    snapshot = {name: getattr(options, name) for name in OPTION_NAMES if hasattr(options, name)}
    snapshot = json.loads(json.dumps(snapshot, default=str))
    ticket = {
        "id": media.id,
        "type": media.type,
        "is_library": media.is_library,
        "title": str(title),
        "source": context,
        "config_path": str(config_path) if config_path is not None else None,
        "options": snapshot,
    }
    identity = {key: ticket[key] for key in ("id", "type", "is_library", "config_path", "options")}
    if context is not None:
        identity["source"] = {key: context[key] for key in ("kind", "id", "index")}
    ticket["key"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return ticket


class SavedPlaylist:
    def __init__(self, record, api):
        self.id = record["id"]
        self.is_library = bool(record.get("is_library"))
        self.saved_metadata = (
            PlaylistMetadata(self.id, record["title"], record.get("artist"))
            if record.get("title") else None
        )
        self._playlist = Playlist(self.id, api=api, is_library=self.is_library)

    async def get_metadata(self):
        if self.saved_metadata is None:
            self.saved_metadata = await self._playlist.get_metadata()
        return self.saved_metadata


def restore_media(ticket, api):
    media = Media.from_id(
        ticket["id"], media_type=ticket["type"],
        is_library=ticket["is_library"], api=api,
    )
    source = ticket.get("source")
    if source is not None:
        playlist = SavedPlaylist(source["playlist"], api) if source.get("playlist") else None
        media.source = MediaSource(source["kind"], source["id"], source["index"], playlist)
    return media


class FailureQueue:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else default_queue_path()

    def items(self):
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("items"), list):
            raise ValueError("Invalid failed-download queue; the original file was retained")
        for item in data["items"]:
            if not isinstance(item, dict) or not all(
                key in item for key in ("key", "id", "type", "is_library", "config_path", "options")
            ):
                raise ValueError("Invalid failed-download ticket")
        return data["items"]

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+b") as stream:
            lock_path.chmod(0o600)
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _change(self, operation):
        with self._locked():
            items = {item["key"]: item for item in self.items()}
            operation(items)
            fd, temporary = tempfile.mkstemp(prefix=".failed-downloads-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump({"version": 1, "items": list(items.values())}, stream, ensure_ascii=False, indent=2)
                os.replace(temporary, self.path)
                self.path.chmod(0o600)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def begin(self, ticket):
        key = ticket["key"]

        def update(items):
            attempts = items.get(key, {}).get("attempts", 0) + 1
            items[key] = dict(
                ticket, attempts=attempts, status="pending",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )

        self._change(update)
        return key

    def fail(self, key, message, *, refreshed=None):
        def update(items):
            if key not in items:
                return
            if refreshed is not None:
                for name in ("title", "source"):
                    items[key][name] = refreshed[name]
            items[key].update(
                status="failed", error=str(message),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )

        self._change(update)

    def complete(self, key):
        self._change(lambda items: items.pop(key, None))
