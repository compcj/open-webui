import base64
import logging
import re
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.skills import (
    SkillAccessListResponse,
    SkillAccessResponse,
    SkillForm,
    SkillModel,
    SkillResponse,
    Skills,
    SkillUserResponse,
)
from open_webui.utils.access_control import filter_allowed_access_grants, has_permission
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.skills_runtime import (
    clawhub_download_url,
    clawhub_skill_api_url,
    format_clawhub_ambiguity,
    parse_clawhub_ref,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

PAGE_ITEM_COUNT = 30

router = APIRouter()


############################
# GetSkills
############################


@router.get('/', response_model=list[SkillUserResponse])
async def get_skills(
    request: Request,
    query: Optional[str] = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL:
        skills = await Skills.get_skills(db=db)
    else:
        skills = await Skills.get_skills(db=db, user_id=user.id)

    if query:
        q = query.casefold()
        skills = [skill for skill in skills if q in (skill.name or '').casefold()]

    return skills


############################
# GetSkillList
############################


@router.get('/list', response_model=SkillAccessListResponse)
async def get_skill_list(
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    limit = PAGE_ITEM_COUNT

    page = max(1, page)
    skip = (page - 1) * limit

    filter = {}
    if query:
        filter['query'] = query
    if view_option:
        filter['view_option'] = view_option
    if order_by:
        filter['order_by'] = order_by
    if direction:
        filter['direction'] = direction

    is_bypass_admin = user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL
    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, db=db)}

    if not is_bypass_admin:
        filter['group_ids'] = user_group_ids
        filter['user_id'] = user.id

    result = await Skills.search_skills(user.id, filter=filter, skip=skip, limit=limit, db=db)

    writable_skill_ids = await AccessGrants.get_accessible_resource_ids(
        user_id=user.id,
        resource_type='skill',
        resource_ids=[skill.id for skill in result.items],
        permission='write',
        user_group_ids=user_group_ids,
        db=db,
    )

    return SkillAccessListResponse(
        items=[
            SkillAccessResponse(
                **skill.model_dump(),
                write_access=(is_bypass_admin or user.id == skill.user_id or skill.id in writable_skill_ids),
            )
            for skill in result.items
        ],
        total=result.total,
    )


############################
# ExportSkills
############################


@router.get('/export', response_model=list[SkillModel])
async def export_skills(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if user.role != 'admin' and not await has_permission(
        user.id,
        'workspace.skills_export',
        await Config.get('user.permissions'),
        db=db,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    if user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL:
        return await Skills.get_skills(db=db)
    else:
        return await Skills.get_skills(db=db, user_id=user.id)


############################
# LoadSkillFromUrl
############################

MAX_SKILL_DOWNLOAD_BYTES = 32 * 1024 * 1024  # 32 MiB


class LoadSkillUrlForm(BaseModel):
    url: str


def github_url_to_skill_url(url: str) -> str:
    # Handle 'tree' (folder) URLs (add SKILL.md at the end)
    m1 = re.match(r'https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.*)', url)
    if m1:
        org, repo, branch, path = m1.groups()
        return f'https://raw.githubusercontent.com/{org}/{repo}/refs/heads/{branch}/{path.rstrip("/")}/SKILL.md'

    # Handle 'blob' (file) URLs
    m2 = re.match(r'https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)', url)
    if m2:
        org, repo, branch, path = m2.groups()
        return f'https://raw.githubusercontent.com/{org}/{repo}/refs/heads/{branch}/{path}'

    # No match; return as-is
    return url


async def resolve_clawhub_skill(url_or_ref: str) -> tuple[str, str, str]:
    """Resolve a ClawHub skill ref to (download_url, slug, latest version).

    Verified against the public API: GET /api/v1/skills/{slug} (optionally with
    ?owner= to disambiguate shared slugs) returns
    {'skill': ..., 'latestVersion': {'version': ...}, 'owner': {'handle': ...}};
    GET /api/v1/download?slug=...&version=...[&owner=...] returns the skill zip.
    Both endpoints answer 409 AMBIGUOUS_SKILL_SLUG when the slug is shared.
    """
    slug, owner = parse_clawhub_ref(url_or_ref)
    if not slug:
        raise HTTPException(status_code=400, detail='Invalid ClawHub skill reference')

    try:
        async with aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        ) as session:
            async with session.get(clawhub_skill_api_url(slug, owner), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status == 409:
                    payload = await resp.json(content_type=None)
                    raise HTTPException(status_code=400, detail=format_clawhub_ambiguity(slug, payload))
                if resp.status != 200:
                    raise HTTPException(status_code=400, detail=f"ClawHub skill '{slug}' not found")
                data = await resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ERROR_MESSAGES.DEFAULT(e, 'Error resolving ClawHub skill'),
        )

    version = (data.get('latestVersion') or {}).get('version')
    if not version:
        raise HTTPException(status_code=400, detail=f"ClawHub skill '{slug}' has no published version")
    owner = owner or (data.get('owner') or {}).get('handle')
    full_slug = f'@{owner}/{slug}' if owner else slug
    return clawhub_download_url(slug, str(version), owner), full_slug, str(version)


@router.post('/load/url', response_model=dict)
async def load_skill_from_url(
    request: Request,
    form_data: LoadSkillUrlForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    # NOTE: This is NOT a SSRF vulnerability:
    # This endpoint is restricted to admins and users with the skills_import
    # permission, meant for *trusted* internal use, and does NOT accept
    # untrusted user input. Access is enforced by authentication.
    if user.role != 'admin' and not await has_permission(
        user.id,
        'workspace.skills_import',
        await Config.get('user.permissions'),
        db=db,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    url = (form_data.url or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='Please enter a valid URL')

    source = {'type': 'url', 'url': url}
    file_name = None
    slug, _ = parse_clawhub_ref(url)
    if slug:
        download_url, full_slug, version = await resolve_clawhub_skill(url)
        source = {'type': 'clawhub', 'slug': full_slug, 'version': version, 'url': url}
        file_name = f'{full_slug.split("/")[-1]}.zip'
        url = download_url
    else:
        raw_url = github_url_to_skill_url(url)
        if raw_url != url:
            source = {'type': 'github', 'url': url}
            url = raw_url

    try:
        async with aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        ) as session:
            async with session.get(
                url, headers={'Content-Type': 'application/json'}, ssl=AIOHTTP_CLIENT_SESSION_SSL
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail='Failed to fetch the skill')
                content_type = resp.headers.get('Content-Type', '').split(';')[0].strip().lower()
                is_zip = (
                    source['type'] == 'clawhub' or 'zip' in content_type or url.split('?')[0].lower().endswith('.zip')
                )
                if is_zip:
                    content_length = resp.headers.get('Content-Length')
                    if content_length and content_length.isdigit() and int(content_length) > MAX_SKILL_DOWNLOAD_BYTES:
                        raise HTTPException(status_code=400, detail='Skill archive exceeds the 32 MiB limit')
                    data = bytearray()
                    async for chunk in resp.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_SKILL_DOWNLOAD_BYTES:
                            raise HTTPException(status_code=400, detail='Skill archive exceeds the 32 MiB limit')
                    if not data:
                        raise HTTPException(status_code=400, detail='No data received from the URL')
                    if not file_name:
                        file_name = url.split('?')[0].rstrip('/').split('/')[-1] or 'skill.zip'
                    return {
                        'format': 'zip',
                        'fileName': file_name,
                        'content': base64.b64encode(bytes(data)).decode('ascii'),
                        'source': source,
                    }
                text = await resp.text()
                if not text:
                    raise HTTPException(status_code=400, detail='No data received from the URL')
                if not file_name:
                    file_name = url.split('?')[0].rstrip('/').split('/')[-1] or 'SKILL.md'
                return {
                    'format': 'markdown',
                    'fileName': file_name,
                    'content': text,
                    'source': source,
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ERROR_MESSAGES.DEFAULT(e, 'Error fetching skill'),
        )


############################
# InstallSkillDeps
############################


class InstallSkillDepsForm(BaseModel):
    terminal_id: str


@router.post('/id/{id}/install_deps', response_model=dict)
async def install_skill_deps(
    request: Request,
    id: str,
    form_data: InstallSkillDepsForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        skill.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='skill',
            resource_id=skill.id,
            permission='write',
            db=db,
        )
        and user.role != 'admin'
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    if not form_data.terminal_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('terminal_id is required'),
        )

    from open_webui.utils.skills_runtime import (
        gating_requirements,
        openclaw_frontmatter,
        openclaw_meta,
        run_install_specs,
    )

    req = gating_requirements(openclaw_frontmatter(openclaw_meta(skill)))
    if not req.get('install'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Skill declares no install specs'),
        )

    return await run_install_specs(request, user, form_data.terminal_id, skill)


############################
# CreateNewSkill
############################


@router.post('/create', response_model=Optional[SkillResponse])
async def create_new_skill(
    request: Request,
    form_data: SkillForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if user.role != 'admin' and not (
        await has_permission(user.id, 'workspace.skills', await Config.get('user.permissions'), db=db)
        or await has_permission(user.id, 'workspace.skills_import', await Config.get('user.permissions'), db=db)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    form_data.id = form_data.id.lower().replace(' ', '-')

    # The id goes into /id/{id}/... paths, so anything outside the slug charset is unreachable once stored.
    if not re.fullmatch(r'[a-z0-9_-]+', form_data.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Invalid skill ID'),
        )

    existing = await Skills.get_skill_by_id(form_data.id, db=db)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )

    # Strip public/user grants the requesting user is not permitted to assign
    # (matches the channel/notes/calendar pattern). Without this, a user with
    # workspace.skills permission could attach principal_id='*' read/write
    # grants in the create payload, bypassing the sharing.public_skills gate
    # that the dedicated /access/update endpoint already enforces.
    form_data.access_grants = await filter_allowed_access_grants(
        await Config.get('user.permissions'),
        user.id,
        user.role,
        form_data.access_grants,
        'sharing.public_skills',
    )

    try:
        skill = await Skills.insert_new_skill(user.id, form_data, db=db)
        if skill:
            await publish_event(
                request,
                EVENTS.SKILL_CREATED,
                actor=user,
                subject_id=skill.id,
                data={'name': skill.name},
            )
            return skill
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error creating skill'),
            )
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f'Failed to create skill: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e, 'Error creating skill'),
        )


############################
# GetSkillById
############################


@router.get('/id/{id}', response_model=Optional[SkillAccessResponse])
async def get_skill_by_id(id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    skill = await Skills.get_skill_by_id(id, db=db)

    if skill:
        if (
            user.role == 'admin'
            or skill.user_id == user.id
            or await AccessGrants.has_access(
                user_id=user.id,
                resource_type='skill',
                resource_id=skill.id,
                permission='read',
                db=db,
            )
        ):
            return SkillAccessResponse(
                **skill.model_dump(),
                write_access=(
                    (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL)
                    or user.id == skill.user_id
                    or await AccessGrants.has_access(
                        user_id=user.id,
                        resource_type='skill',
                        resource_id=skill.id,
                        permission='write',
                        db=db,
                    )
                ),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateSkillById
############################


@router.post('/id/{id}/update', response_model=Optional[SkillModel])
async def update_skill_by_id(
    request: Request,
    id: str,
    form_data: SkillForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        skill.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='skill',
            resource_id=skill.id,
            permission='write',
            db=db,
        )
        and user.role != 'admin'
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    # Strip public/user grants the requesting user is not permitted to assign
    # (matches the channel/notes/calendar pattern). The access check above only
    # restricts WHO can write to the skill; this filter restricts WHICH grants
    # they may set, so a non-admin owner cannot make their own skill publicly
    # readable/writable without sharing.public_skills permission.
    form_data.access_grants = await filter_allowed_access_grants(
        await Config.get('user.permissions'),
        user.id,
        user.role,
        form_data.access_grants,
        'sharing.public_skills',
    )

    try:
        updated = {
            **form_data.model_dump(exclude={'id'}),
        }

        skill = await Skills.update_skill_by_id(id, updated, db=db)

        if skill:
            await publish_event(
                request,
                EVENTS.SKILL_UPDATED,
                actor=user,
                subject_id=skill.id,
                data={'name': skill.name},
            )
            return skill
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error updating skill'),
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e, 'Error updating skill'),
        )


############################
# UpdateSkillAccessById
############################


class SkillAccessGrantsForm(BaseModel):
    access_grants: list[dict]


@router.post('/id/{id}/access/update', response_model=Optional[SkillModel])
async def update_skill_access_by_id(
    request: Request,
    id: str,
    form_data: SkillAccessGrantsForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        skill.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='skill',
            resource_id=skill.id,
            permission='write',
            db=db,
        )
        and user.role != 'admin'
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    form_data.access_grants = await filter_allowed_access_grants(
        await Config.get('user.permissions'),
        user.id,
        user.role,
        form_data.access_grants,
        'sharing.public_skills',
    )

    await AccessGrants.set_access_grants('skill', id, form_data.access_grants, db=db)

    skill = await Skills.get_skill_by_id(id, db=db)
    await publish_event(
        request,
        EVENTS.SKILL_UPDATED,
        actor=user,
        subject_id=id,
        data={'access_updated': True, 'name': skill.name if skill else None},
    )
    return skill


############################
# ToggleSkillById
############################


@router.post('/id/{id}/toggle', response_model=Optional[SkillModel])
async def toggle_skill_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await Skills.get_skill_by_id(id, db=db)
    if skill:
        if (
            user.role == 'admin'
            or skill.user_id == user.id
            or await AccessGrants.has_access(
                user_id=user.id,
                resource_type='skill',
                resource_id=skill.id,
                permission='write',
                db=db,
            )
        ):
            skill = await Skills.toggle_skill_by_id(id, db=db)

            if skill:
                await publish_event(
                    request,
                    EVENTS.SKILL_ENABLED if skill.is_active else EVENTS.SKILL_DISABLED,
                    actor=user,
                    subject_id=skill.id,
                    data={'name': skill.name},
                )
                return skill
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT('Error toggling skill'),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.UNAUTHORIZED,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# DeleteSkillById
############################


@router.delete('/id/{id}/delete', response_model=bool)
async def delete_skill_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        skill.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='skill',
            resource_id=skill.id,
            permission='write',
            db=db,
        )
        and user.role != 'admin'
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    result = await Skills.delete_skill_by_id(id, db=db)
    if result:
        await publish_event(
            request,
            EVENTS.SKILL_DELETED,
            actor=user,
            subject_id=id,
            data={'name': skill.name},
        )
    return result
