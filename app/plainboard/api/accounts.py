"""Provider accounts: CRUD, a connectivity check, and the sign-in of subscriptions in the browser, kept per
subscription (``accounts.kept_login``); the installation's from before #84 still in the runner's home until
it is taken over (``Account.home_login``)."""

from __future__ import annotations

from fastapi import APIRouter
from plainboard_protocol import CompletionSpec, ProviderAccount
from pydantic import BaseModel, Field

from ..orchestration.runner_client import RunnerError
from ..schemas import AccountCreate, AccountOut, AccountUpdate, CheckOut
from ..services import accounts as account_service
from ..services import limits as limits_service
from ..services import permissions
from ..services import settings as settings_service
from ..services.errors import ServiceError
from .deps import Runner, Runs, Session, Who

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
async def list_accounts(session: Session, who: Who) -> list[AccountOut]:
    """An admin: every account. Anyone else: what they and their projects may use, the secrets of none."""
    accounts = await account_service.visible(session, who.user) if who.user else await account_service.list_accounts(session)
    names = await account_service.owner_names(session)
    return [account_service.to_out(a, who.user, names) for a in accounts]


@router.post("", response_model=AccountOut, status_code=201)
async def create(body: AccountCreate, session: Session, runner: Runner, runs: Runs, who: Who) -> AccountOut:
    name = ""
    if body.provider not in account_service.AUTH_MODES:  # an endpoint: which ones exist is the runner's list
        try:
            found = next((p for p in await runner.providers() if p.name == body.provider and p.endpoint is not None), None)
        except RunnerError as exc:
            raise ServiceError(str(exc), 502) from exc
        if found is None:
            raise ServiceError(f"Unknown provider '{body.provider}'")
        name = found.label
    account = await account_service.create(session, body, name, who.user)
    if account.enabled and account_service.has_plan(account) and body.secret:
        runs.schedule_account_limits(account.id)
    return account_service.to_out(account, who.user, await account_service.owner_names(session))


@router.patch("/{account_id}", response_model=AccountOut)
async def update(account_id: int, body: AccountUpdate, session: Session, runs: Runs, who: Who) -> AccountOut:
    account = await account_service.update(session, await account_service.managed(session, account_id, who.user), body)
    if account.enabled and account_service.has_plan(account) and (body.secret or body.enabled is True):
        runs.schedule_account_limits(account.id)
    return account_service.to_out(account, who.user, await account_service.owner_names(session))


@router.delete("/{account_id}", status_code=204)
async def delete(account_id: int, session: Session, runner: Runner, who: Who) -> None:
    account = await account_service.managed(session, account_id, who.user)
    if account_service.kept_login(account):  # one still in the runner's home stays there as it is
        try:  # don't leave a sign-in waiting on the runner
            await runner.provider_account(account.provider, account.key, "logout", own=True)
        except RunnerError:
            pass
    key = account.key
    await account_service.delete(session, account)
    await limits_service.clear(session, key)


class CheckIn(BaseModel):
    model: str


@router.post("/{account_id}/check", response_model=CheckOut)
async def check(account_id: int, body: CheckIn, session: Session, runner: Runner, who: Who) -> CheckOut:
    account = await account_service.managed(session, account_id, who.user)
    spec = CompletionSpec(
        provider=account.provider,
        model=body.model,
        system_prompt="You are a connectivity check. Reply with exactly: OK",
        prompt="Reply with OK.",
        credentials=settings_service.credentials(await settings_service.get_all(session), account=account_service.fields(account)),
    )
    result = await runner.check_provider(spec)
    if account_service.kept_login(account):
        await account_service.take_sign_ins(session, runner)  # the check may have refreshed it
    return CheckOut(ok=result.ok, detail=result.detail)


class ModelsOut(BaseModel):
    models: list[str]


@router.post("/{account_id}/models", response_model=ModelsOut)
async def offered_models(account_id: int, session: Session, runner: Runner, who: Who) -> ModelsOut:
    """What the account's endpoint says it offers, to choose from. Nothing is stored."""
    account = await account_service.managed(session, account_id, who.user)
    credentials = settings_service.credentials(await settings_service.get_all(session), account=account_service.fields(account))
    try:
        return ModelsOut(models=await runner.provider_models(account.provider, credentials))
    except RunnerError as exc:
        raise ServiceError(str(exc), 502) from exc


async def _signin(session: Session, runner: Runner, runs: Runs, who: Who, account_id: int, action: str | None = None, code: str | None = None) -> ProviderAccount:
    """A sign-in is made in a private folder on the runner; once it is done the app keeps its files and the runner
    forgets them. Signed out: they are gone here too. One still in the runner's home (``home_login``) that is
    signed in anew is kept from then on."""
    account = await account_service.managed(session, account_id, who.user)
    if action == "login" and account.owner_id is not None and who.user is not None and not who.user.is_admin and account.id in await permissions.switched_off(session):
        raise ServiceError(account_service.OWN_SWITCHED_OFF, 403)
    if action in ("login", "logout") and account.home_login and account.auth_mode in account_service.LOGIN_MODES:
        account.home_login, account.secret_enc = False, None  # the home's copy stays as it is, unused
        await session.commit()
    own = account_service.kept_login(account)
    had_own = bool(account.secret_enc)
    if account.home_login and account.provider == "claude_code" and action is None:
        return ProviderAccount()  # in the runner's terminal until it is taken over, which the job watcher does
    try:
        if code is not None:
            state = await runner.provider_login_code(account.provider, account.key, code)
        else:
            state = await runner.provider_account(account.provider, account.key, action, own=own)
    except RunnerError as exc:
        raise ServiceError(str(exc), 502) from exc
    if code is not None or action == "logout":
        runner.forget_providers()
    if action == "logout":
        if own:
            account.secret_enc = None
            await session.commit()
        await limits_service.clear(session, account.key)
        return state.model_copy(update={"signed_in": False}) if own else state
    if own:
        await account_service.take_sign_ins(session, runner)
        await session.refresh(account)
        state = state.model_copy(update={"signed_in": bool(account.secret_enc) and not state.login_url})
    if state.signed_in and account.enabled and account_service.has_plan(account):
        # Fetch plan limits right away after a completed sign-in so the chat status ring appears immediately.
        if code is not None or (own and not had_own):
            await runs.refresh_account_limits(account.id)
        elif action is None and not (await limits_service.windows(session)).get(account.key):
            runs.schedule_account_limits(account.id)
    return state


@router.get("/{account_id}/signin", response_model=ProviderAccount)
async def signin_state(account_id: int, session: Session, runner: Runner, runs: Runs, who: Who) -> ProviderAccount:
    return await _signin(session, runner, runs, who, account_id)


@router.post("/{account_id}/signin", response_model=ProviderAccount)
async def signin(account_id: int, session: Session, runner: Runner, runs: Runs, who: Who) -> ProviderAccount:
    """Starts a sign-in: the answer has the URL to open and the code to enter (Codex), or asks for the code the page
    shows after signing in (Claude, Antigravity, ``needs_code``)."""
    return await _signin(session, runner, runs, who, account_id, "login")


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=2000)


@router.post("/{account_id}/signin/code", response_model=ProviderAccount)
async def signin_code(account_id: int, body: CodeIn, session: Session, runner: Runner, runs: Runs, who: Who) -> ProviderAccount:
    return await _signin(session, runner, runs, who, account_id, code=body.code)


@router.delete("/{account_id}/signin", response_model=ProviderAccount)
async def signout(account_id: int, session: Session, runner: Runner, runs: Runs, who: Who) -> ProviderAccount:
    return await _signin(session, runner, runs, who, account_id, "logout")
