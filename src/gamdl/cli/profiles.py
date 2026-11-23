"""Album/playlist aliases and persistent, validated configuration selection."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import click

PROFILE_NAMES = ("ab", "pl")


def profile_dir() -> Path:
    return Path.home() / ".gamdl"


def resolve_config_path(path: Path) -> Path:
    """Normalize a selected symlink's target, preserving the selector itself."""
    path = path.expanduser()
    return path.resolve() if path.is_symlink() else path


def profile_path(name: str, directory: Path | None = None) -> Path:
    if name not in PROFILE_NAMES:
        raise click.BadParameter("Profile must be ab or pl")
    return (directory or profile_dir()) / f"config-{name}.yml"


def current_profile(directory: Path | None = None) -> str:
    root = directory or profile_dir()
    default = root / "config.yml"
    if not default.exists():
        return "unset"
    for name in PROFILE_NAMES:
        if default.resolve() == profile_path(name, root).resolve():
            return name
    return "custom"


def activate_profile(name: str, directory: Path | None = None, *, validator=None) -> Path:
    root = directory or profile_dir()
    target = profile_path(name, root)
    if not target.is_file():
        raise click.ClickException(
            f"Profile does not exist: {target}. Run 'gamdl profiles migrate' first."
        )
    if validator is None:
        from .config.loader import load_config

        validator = load_config
    # Validation must precede replacing either a regular file or a symlink.
    try:
        validator(target)
    except ValueError as exc:
        raise click.ClickException(f"Invalid profile: {exc}") from exc
    default = root / "config.yml"
    if default.exists() and not default.is_symlink():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = root / "backups" / f"config-before-profile-{stamp}.yml"
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(default, backup)
        backup.chmod(0o600)
    # The private temporary directory prevents collisions and is on the same
    # filesystem as the destination, making os.replace atomic.
    try:
        with tempfile.TemporaryDirectory(prefix=".profile-", dir=root) as temporary:
            link = Path(temporary) / "config.yml"
            link.symlink_to(target.name)
            os.replace(link, default)
    except OSError as exc:
        raise click.ClickException("Unable to switch profile") from exc
    return target


class ProfileRoutingMixin:
    """Rewrite aliases before Click resolves the root command's default map."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        values = list(args)
        index = 0
        explicit_config = False
        while index < len(values):
            token = values[index]
            if token == "--config":
                explicit_config = True
                index += 2
            elif token.startswith("--config=") or token == "--no-config":
                explicit_config = True
                index += 1
            elif token.startswith("-"):
                # Unknown options must retain Click's normal error handling.
                if token not in {"--help", "--version", "-v"}:
                    break
                index += 1
            else:
                break
        if index < len(values):
            command = values[index]
            if command in PROFILE_NAMES:
                if explicit_config:
                    raise click.UsageError(
                        "ab/pl cannot be combined with --config or --no-config", ctx
                    )
                remaining = values[index + 1 :]
                if not remaining:
                    values[index:] = ["use", command]
                else:
                    path = profile_path(command)
                    if not path.is_file():
                        raise click.ClickException(
                            f"Missing profile: {path}. Run 'gamdl profiles migrate'."
                        )
                    if remaining[0] not in self.commands:
                        remaining.insert(0, "rip")
                    if remaining[0] == "rip" and not any(
                        flag in remaining for flag in ("--count-first", "--stream-discovery")
                    ):
                        remaining.insert(1, "--count-first")
                    values[index:] = ["--config", str(path), *remaining]
            elif command.startswith(("https://", "http://", "isrc:")):
                values[index:index] = ["rip", "--count-first"]
        return super().parse_args(ctx, values)

    def format_epilog(self, ctx, formatter) -> None:
        super().format_epilog(ctx, formatter)
        formatter.write_paragraph()
        formatter.write_text(
            "Profiles: gamdl ab URL | gamdl pl URL | gamdl use ab/pl. "
            "A bare URL uses the current default."
        )


@click.command("use")
@click.argument("name", type=click.Choice(PROFILE_NAMES))
def use_profile(name: str) -> None:
    """Switch the persistent default to the ab or pl configuration."""
    path = activate_profile(name)
    click.echo(f"Default profile: {name} ({path})")


@click.group("profiles", invoke_without_command=True)
@click.pass_context
def profiles(ctx: click.Context) -> None:
    """Inspect profiles or migrate Gamdl 3.x INI configurations."""
    if ctx.invoked_subcommand is None:
        click.echo(f"Default profile: {current_profile()}")
        for name in PROFILE_NAMES:
            click.echo(f"{name}: {profile_path(name)}")
        if os.environ.get("GAMDL_CONFIG"):
            click.echo("GAMDL_CONFIG overrides the default; ab/pl select their own profile.")


@profiles.command("migrate")
@click.option(
    "--legacy-cwd",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path.home,
    help="Original working directory used by relative INI paths (default: home).",
)
@click.option("--force", is_flag=True, help="Back up and replace existing YAML profiles")
def migrate(legacy_cwd: Path, force: bool) -> None:
    """Migrate config-ab.ini/config.ini and config-pl.ini; default to ab."""
    from .profile_migration import migrate_profiles

    try:
        paths = migrate_profiles(profile_dir(), legacy_cwd.expanduser().absolute(), force=force)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    for path in paths:
        click.echo(f"Migrated profile: {path}")
    click.echo("Default profile: ab")
