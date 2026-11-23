"""Count selected source occurrences before any media transfer begins."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import aclosing

import click
from rich.text import Text

from gamdl.exceptions import GamdlError


def _single_line(value):
    return "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in str(value))


async def replay_source(items):
    for item in items:
        yield item


async def collect_plan(resolver, urls, *, progress, console, check_stopped, source_failed):
    """Fetch collection pages once, preserving order and duplicate occurrences.

    No playback, license or audio requests are made here. The original media
    objects (including playlist context and cached metadata) are replayed later.
    """
    plan = []
    found = 0
    incomplete = False
    groups = OrderedDict()
    for url in urls:
        items = []
        plan.append(items)
        check_stopped()
        try:
            async with aclosing(resolver.from_url(url)) as source:
                async for media in source:
                    check_stopped()
                    items.append(media)
                    found += 1
                    progress.discovered(found)
                    context = media.source
                    if context is not None and context.kind in {"playlist_tracks", "album_tracks"}:
                        key = (context.kind, context.id)
                        if key not in groups:
                            label = context.id
                            try:
                                if context.playlist is not None:
                                    label = (await context.playlist.get_metadata()).title
                                else:
                                    attributes = (await media.get_metadata()).get("attributes", {})
                                    label = attributes.get("albumName") or label
                            except GamdlError:
                                pass
                            groups[key] = [label, 0]
                        groups[key][1] += 1
        except click.Abort:
            raise
        except Exception as error:
            incomplete = True
            source_failed(error)
    for (kind, _), (label, count) in groups.items():
        category = "播放列表" if kind == "playlist_tracks" else "专辑"
        console.print(Text(f"{category}：{_single_line(label)} · {count} 首"))
    album_count = sum(kind == "album_tracks" for kind, _ in groups)
    prefix = f"{album_count} 张专辑，" if album_count else ""
    suffix = "（部分来源读取失败）" if incomplete else ""
    console.print(Text(f"本次合计：{prefix}{found} 首曲目{suffix}"))
    return plan
