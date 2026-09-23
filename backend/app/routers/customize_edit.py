"""rule / command / hook 在线编辑 API。"""

from fastapi import APIRouter

from app.auth import CurrentUser, DbSession, OptionalUser
from app.schemas import HookEditOut, HookEditSave, MarkdownKindEditOut, MarkdownKindEditSave
from app.services.capabilities import to_capability_out
from app.services import hook_editor, markdown_kind

rule_router = APIRouter(prefix="/api/rules", tags=["rule-edit"])
command_router = APIRouter(prefix="/api/commands", tags=["command-edit"])
hook_router = APIRouter(prefix="/api/hooks", tags=["hook-edit"])


def _md_out(cap, body: str, files: list[dict], base_version: str, always: bool, globs: str) -> MarkdownKindEditOut:
    return MarkdownKindEditOut(
        capability=to_capability_out(cap),
        body=body,
        files=files,
        base_version=base_version,
        always_apply=always,
        globs=globs,
    )


@rule_router.get("/{name}/edit", response_model=MarkdownKindEditOut)
async def edit_rule(name: str, db: DbSession, user: OptionalUser):
    cap, body, files, base_version, always, globs = await markdown_kind.get_editable(
        db, user, name, "rule"
    )
    return _md_out(cap, body, files, base_version, always, globs)


@rule_router.put("/{name}/edit", response_model=MarkdownKindEditOut)
async def save_rule(name: str, data: MarkdownKindEditSave, db: DbSession, user: CurrentUser):
    cap, body, files = await markdown_kind.save_version(db, user, name, "rule", data)
    _, _, _, base_version, always, globs = await markdown_kind.get_editable(db, user, name, "rule")
    return _md_out(cap, body, files, base_version, always, globs)


@command_router.get("/{name}/edit", response_model=MarkdownKindEditOut)
async def edit_command(name: str, db: DbSession, user: OptionalUser):
    cap, body, files, base_version, always, globs = await markdown_kind.get_editable(
        db, user, name, "command"
    )
    return _md_out(cap, body, files, base_version, always, globs)


@command_router.put("/{name}/edit", response_model=MarkdownKindEditOut)
async def save_command(name: str, data: MarkdownKindEditSave, db: DbSession, user: CurrentUser):
    cap, body, files = await markdown_kind.save_version(db, user, name, "command", data)
    _, _, _, base_version, always, globs = await markdown_kind.get_editable(
        db, user, name, "command"
    )
    return _md_out(cap, body, files, base_version, always, globs)


@hook_router.get("/{name}/edit", response_model=HookEditOut)
async def edit_hook(name: str, db: DbSession, user: OptionalUser):
    cap, hooks_json, files, base_version = await hook_editor.get_editable(db, user, name)
    return HookEditOut(
        capability=to_capability_out(cap),
        hooks_json=hooks_json,
        files=files,
        base_version=base_version,
    )


@hook_router.put("/{name}/edit", response_model=HookEditOut)
async def save_hook(name: str, data: HookEditSave, db: DbSession, user: CurrentUser):
    cap, hooks_json, files = await hook_editor.save_version(db, user, name, data)
    _, _, _, base_version = await hook_editor.get_editable(db, user, name)
    return HookEditOut(
        capability=to_capability_out(cap),
        hooks_json=hooks_json,
        files=files,
        base_version=base_version,
    )
