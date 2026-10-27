"""Immutable, authenticated context identities and explicit storage bindings.

Only server authentication/dispatch code constructs these objects. They belong
to Request.state, never to a model payload or a browser-submitted metadata bag.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal


@dataclass(frozen=True)
class ContextScope:
    kind: Literal['saved', 'temporary']
    context_id: str
    actor_id: str
    epoch: str | None = None
    revision: int | None = None
    branch_id: str | None = None
    reply_id: str | None = None
    model_id: str | None = None
    task_id: str | None = None
    execution_fence: int | None = None

    @property
    def store_key(self) -> str:
        return f'temporary:{self.context_id}' if self.kind == 'temporary' else self.context_id

    def for_reply(self, *, reply_id, model_id, task_id, fence=None):
        return replace(
            self, branch_id=reply_id, reply_id=reply_id, model_id=model_id, task_id=task_id, execution_fence=fence
        )

    def identity(self) -> dict:
        return {'id': self.context_id, 'epoch': self.epoch, 'revision': self.revision, 'branch_id': self.branch_id}


@dataclass(frozen=True)
class ContextStore:
    scope: ContextScope
    checkpoints: Any
    results: Any
    selections: Any
    history: Any = None
    executions: Any = None
    events: Any = None


def request_context_store(request) -> ContextStore | None:
    value = getattr(getattr(request, 'state', None), 'context_store', None)
    return value if isinstance(value, ContextStore) else None


def temporary_request_store(request) -> ContextStore | None:
    value = request_context_store(request)
    return value if value is not None and value.scope.kind == 'temporary' else None
