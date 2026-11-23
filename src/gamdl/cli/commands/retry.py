"""Retry recorded media IDs without expanding their original collections."""

import asyncio
import json
from collections import defaultdict
from dataclasses import fields
from pathlib import Path

import click

from ..config.loader import click_default_map, load_config
from ..failure_queue import FailureQueue
from ..options import RipOptions
from ..profiles import profile_path


def _line(value):
    return "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in str(value))


async def run_retries(entries, queue):
    from .runner import run_rip

    groups = defaultdict(list)
    for ticket in entries:
        groups[(ticket["config_path"], json.dumps(ticket["options"], sort_keys=True))].append(ticket)
    exit_code = 0
    allowed = {field.name for field in fields(RipOptions)}
    for (path, snapshot), tickets in groups.items():
        if path and not Path(path).is_file():
            raise ValueError(f"Original configuration is missing: {path}")
        defaults = click_default_map(load_config(Path(path) if path else None))
        values = {name: value for name, value in defaults.items() if name in allowed}
        values.update({name: value for name, value in json.loads(snapshot).items() if name in allowed})
        options = RipOptions(**values)
        options.urls = ()
        options.retry_entries = tickets
        options.failure_queue = queue
        options.failure_config_path = path
        options.count_first = False
        label = Path(path).stem if path else "default"
        click.echo(f"重试 {label}：{len(tickets)} 首失败或中断的曲目")
        result = await run_rip(options)
        exit_code = max(exit_code, result)
    return exit_code


@click.command("retry")
@click.option("--list", "list_only", is_flag=True, help="List pending/failed tracks without authenticating")
@click.option("--id", "media_ids", multiple=True, help="Retry only these recorded media IDs")
@click.option("--profile", type=click.Choice(("ab", "pl")), help="Filter by album or playlist profile")
@click.option("--queue", "queue_path", type=click.Path(path_type=Path), help="Use a specific failure queue")
@click.pass_context
def retry(ctx, list_only, media_ids, profile, queue_path):
    """Retry only failed IDs, preserving their original profile and playlist index."""
    queue = FailureQueue(queue_path)
    try:
        entries = queue.items()
        selected_profile = profile or ctx.find_root().meta.get("profile_alias")
        if selected_profile:
            target = str(profile_path(selected_profile).resolve())
            entries = [entry for entry in entries if entry["config_path"] == target]
        if media_ids:
            entries = [entry for entry in entries if entry["id"] in media_ids]
        if not entries:
            click.echo("没有匹配的失败或中断曲目。")
            return
        if list_only:
            for entry in entries:
                source = entry.get("source") or {}
                number = f"{source['index'] + 1:03d}" if "index" in source else "—"
                label = Path(entry["config_path"]).stem if entry["config_path"] else "default"
                click.echo(f"{entry['id']}  {label}  {number}  {_line(entry.get('title', entry['id']))}  {entry.get('status', 'failed')}")
            click.echo(f"共 {len(entries)} 首；使用 gamdl retry --id ID 重试指定曲目。")
            return
        result = asyncio.run(run_retries(entries, queue))
    except KeyboardInterrupt:
        raise click.Abort() from None
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    if result:
        raise SystemExit(result)
