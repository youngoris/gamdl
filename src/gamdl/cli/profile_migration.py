"""Preserve Gamdl 3.x album/playlist settings in validated 4.x profiles."""

from __future__ import annotations

import configparser
import os
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

def read_ini(path):
    parser = configparser.ConfigParser(interpolation=None)
    if not path.is_file():
        raise ValueError("缺少原配置：" + str(path))
    parser.read(path, encoding="utf-8")
    if not parser.has_section("gamdl"):
        raise ValueError("原配置缺少 [gamdl]：" + str(path))
    return dict(parser["gamdl"])


def value(raw):
    if raw is None or str(raw).lower() in {"null", "none"}:
        return None
    if str(raw).lower() in {"true", "false"}:
        return str(raw).lower() == "true"
    return raw


def csv(raw):
    return [part.strip() for part in str(raw).split(",") if part.strip()] if value(raw) is not None else []


def absolute(raw, legacy_cwd):
    if value(raw) is None:
        return None
    path = Path(raw).expanduser()
    return str(path if path.is_absolute() else legacy_cwd / path)


def convert(old, legacy_cwd):
    if value(old.get("synced_lyrics_only", "false")):
        raise ValueError("synced_lyrics_only=true 需要单独处理；停止以避免改变行为。")
    output = {
        "directory": absolute(old.get("output_path", "./Music/GAMDL_Downloads"), legacy_cwd),
        "temp_path": absolute(old.get("temp_path"), legacy_cwd),
        "embedded_cover_size": int(old.get("cover_size", "1200")),
        "file_cover_size": int(old.get("cover_size", "1200")),
        "exclude_tags": csv(old.get("exclude_tags")),
        "lyrics_file_format": old.get("synced_lyrics_format", "lrc"),
        "no_lyrics_file": bool(value(old.get("no_synced_lyrics", "false"))),
        "music_video_container": old.get("music_video_remux_format", "m4v"),
    }
    for key in ("save_cover", "save_playlist", "cover_format", "date_tag_template",
                "album_folder_template", "compilation_folder_template", "no_album_folder_template",
                "single_disc_file_template", "multi_disc_file_template", "no_album_file_template"):
        if key in old:
            output[key] = value(old[key])
    output["truncate"] = int(old["truncate"]) if value(old.get("truncate")) is not None else None
    if "all" in output["exclude_tags"]:
        output["no_tag"] = True
        output["exclude_tags"] = []
    # 3.x 把完整播放列表路径存在 file_template；4.x 分成 folder/file。
    playlist_path = old.get("playlist_file_template", "Playlists/{playlist_artist}/{playlist_title}")
    if "/" in playlist_path:
        output["playlist_folder_template"], output["playlist_file_template"] = playlist_path.rsplit("/", 1)
    else:
        output["playlist_folder_template"] = old.get("playlist_folder_template", "Playlists/{playlist_artist}")
        output["playlist_file_template"] = playlist_path
    download = {
        "overwrite": bool(value(old.get("overwrite", "false"))),
        "use_album_date": bool(value(old.get("use_album_date", "false"))),
        "song_codec_priority": csv(old.get("song_codec_piority", old.get("song_codec_priority", "aac-web"))),
        "music_video_codec_priority": csv(old.get("music_video_codec_priority", "h264,h265")),
        "max_height": int(old.get("music_video_resolution", "1080p").rstrip("p")),
        # 4.x represents automatic best-available quality with null.
        "uploaded_video_quality": (
            None
            if value(old.get("uploaded_video_quality", "best")) in (None, "best")
            else value(old["uploaded_video_quality"])
        ),
        "artist_auto_select": csv(old.get("artist_auto_select")),
        "concurrent_downloads": 1,
    }
    auth = {
        "mode": "wrapper" if value(old.get("use_wrapper", "false")) else "cookies",
        "cookies_path": absolute(old.get("cookies_path", "./cookies.txt"), legacy_cwd),
        "language": old.get("language", "zh-CN"),
        "drm_backend": "widevine",
        "device_path": absolute(old.get("wvd_path"), legacy_cwd),
    }
    if "wrapper_url" in old:
        auth["wrapper_url"] = old["wrapper_url"]
    db_path = absolute(old.get("database_path"), legacy_cwd)
    database = {"disabled": db_path is None}
    if db_path:
        # 4.x 不能打开 3.x 索引，给新索引独立路径，旧索引保留。
        path = Path(db_path)
        database["path"] = str(path.with_name(path.stem + "-v4" + path.suffix))
    return {"config_version": 1, "auth": auth, "download": download, "output": output,
            "database": database, "ui": {"no_tracebacks": bool(value(old.get("no_exceptions", "false")))}}


def migrate_profiles(root: Path, legacy_cwd: Path, *, force: bool = False) -> list[Path]:
    """Validate both candidates before writing; preserve originals on failure."""
    import yaml

    from .config.loader import load_config
    from .config.models import AppConfig
    from .profiles import activate_profile

    album = root / "config-ab.ini"
    if not album.is_file():
        album = root / "config.ini"
    plans = {
        "ab": convert(read_ini(album), legacy_cwd),
        "pl": convert(read_ini(root / "config-pl.ini"), legacy_cwd),
    }
    targets = [root / "config-ab.yml", root / "config-pl.yml"]
    if not force and any(path.exists() or path.is_symlink() for path in targets):
        raise ValueError("YAML profiles already exist; use --force to back up and replace them")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def merge(base, updates):
        for key, item in updates.items():
            if isinstance(item, dict):
                merge(base[key], item)
            else:
                base[key] = item

    with tempfile.TemporaryDirectory(prefix=".profile-migration-", dir=root) as temporary:
        stage = Path(temporary)
        for name, plan in plans.items():
            complete = asdict(AppConfig())
            merge(complete, plan)
            candidate = stage / f"config-{name}.yml"
            candidate.write_text(yaml.safe_dump(complete, sort_keys=False, allow_unicode=True), encoding="utf-8")
            candidate.chmod(0o600)
            load_config(candidate)
        changed = [*targets, root / "config.yml"]
        existed = {path: path.exists() or path.is_symlink() for path in changed}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = root / "backups" / f"profile-migration-{stamp}"
        backup.mkdir(parents=True, mode=0o700)
        for path in [*changed, album, root / "config-pl.ini"]:
            if path.exists() or path.is_symlink():
                shutil.copy2(path, backup / path.name, follow_symlinks=False)
        try:
            for path in targets:
                os.replace(stage / path.name, path)
            activate_profile("ab", root)
        except Exception:
            for path in changed:
                path.unlink(missing_ok=True)
                if existed[path]:
                    shutil.copy2(backup / path.name, path, follow_symlinks=False)
            raise
    return targets
