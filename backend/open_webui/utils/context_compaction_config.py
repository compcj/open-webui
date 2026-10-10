"""Validated settings shared by the V2 engine and admin configuration API.

Importing this module does not initialize database/configuration state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# API name -> (model field, persistent key, default). Keep legacy keys independent.
CONFIG_FIELDS = {
    'CONTEXT_COMPACTION_MODE': ('mode', 'chat.context_compaction.mode', None),
    'CONTEXT_COMPACTION_DEFAULT_CONTEXT_WINDOW': (
        'default_context_window',
        'chat.context_compaction.default_context_window',
        262144,
    ),
    'CONTEXT_COMPACTION_V2_EFFECTIVE_WINDOW_PERCENT': (
        'effective_window_percent',
        'chat.context_compaction.v2.effective_window_percent',
        95,
    ),
    'CONTEXT_COMPACTION_V2_TRIGGER_PERCENT': ('trigger_percent', 'chat.context_compaction.v2.trigger_percent', 90),
    'CONTEXT_COMPACTION_V2_REMINDER_PERCENT': ('reminder_percent', 'chat.context_compaction.v2.reminder_percent', 10),
    'CONTEXT_COMPACTION_V2_RECENT_TOKENS': ('recent_tokens', 'chat.context_compaction.v2.recent_tokens', 20000),
    'CONTEXT_COMPACTION_V2_MODEL': ('summary_model', 'chat.context_compaction.v2.model', ''),
    'CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS': (
        'summary_max_output_tokens',
        'chat.context_compaction.v2.max_output_tokens',
        None,
    ),
    'CONTEXT_COMPACTION_V2_SUMMARY_OUTPUT_PERCENT': (
        'summary_output_percent',
        'chat.context_compaction.v2.summary_output_percent',
        5,
    ),
    'CONTEXT_COMPACTION_V2_SUMMARY_PROMPT': ('summary_prompt', 'chat.context_compaction.v2.summary_prompt', ''),
    'CONTEXT_COMPACTION_V2_REMINDER_ENABLED': ('reminder_enabled', 'chat.context_compaction.v2.reminder_enabled', True),
    'CONTEXT_COMPACTION_V2_REMINDER_PROMPT': ('reminder_prompt', 'chat.context_compaction.v2.reminder_prompt', ''),
    'CONTEXT_COMPACTION_V2_TIMEOUT_SECONDS': ('timeout_seconds', 'chat.context_compaction.v2.timeout_seconds', 60),
    'CONTEXT_COMPACTION_V2_MAX_RETRIES': ('max_retries', 'chat.context_compaction.v2.max_retries', 1),
    'CONTEXT_COMPACTION_V2_TOOL_BUDGET_ENABLED': (
        'tool_budget_enabled',
        'chat.context_compaction.v2.tool_budget_enabled',
        True,
    ),
    'CONTEXT_COMPACTION_V2_TOOL_RESULT_PERCENT': (
        'tool_result_percent',
        'chat.context_compaction.v2.tool_result_percent',
        10,
    ),
    'CONTEXT_COMPACTION_V2_TOOL_BATCH_PERCENT': (
        'tool_batch_percent',
        'chat.context_compaction.v2.tool_batch_percent',
        30,
    ),
    'CONTEXT_COMPACTION_V2_TOOL_SUMMARY_PROMPT': (
        'tool_summary_prompt',
        'chat.context_compaction.v2.tool_summary_prompt',
        '',
    ),
}


class CompactionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    mode: Literal['off', 'legacy', 'v2'] | None = None
    default_context_window: int = Field(strict=True, default=262144, gt=0)
    effective_window_percent: int = Field(strict=True, default=95, gt=0, le=100)
    trigger_percent: int = Field(strict=True, default=90, gt=0, le=100)
    reminder_percent: int = Field(strict=True, default=10, ge=0, le=100)
    recent_tokens: int = Field(strict=True, default=20000, ge=0)
    summary_model: str = ''
    summary_max_output_tokens: int | None = Field(strict=True, default=None, gt=0)
    summary_output_percent: int = Field(strict=True, default=5, ge=1, le=100)
    summary_prompt: str = ''
    reminder_enabled: bool = True
    reminder_prompt: str = ''
    timeout_seconds: int = Field(strict=True, default=60, ge=1, le=600)
    max_retries: int = Field(strict=True, default=1, ge=0, le=5)
    tool_budget_enabled: bool = True
    tool_result_percent: int = Field(strict=True, default=10, ge=1, le=100)
    tool_batch_percent: int = Field(strict=True, default=30, ge=1, le=100)
    tool_summary_prompt: str = ''

    @model_validator(mode='after')
    def validate_budget(self):
        if self.tool_result_percent > self.tool_batch_percent:
            raise ValueError('tool_result_percent must not exceed tool_batch_percent')
        if self.trigger_percent > self.effective_window_percent:
            raise ValueError('trigger_percent must not exceed effective_window_percent')
        if self.recent_tokens >= self.default_context_window * self.effective_window_percent // 100:
            raise ValueError('recent_tokens must be smaller than the default effective context window')
        return self

    @classmethod
    def from_config(cls, config: dict, legacy_enabled: bool = False):
        values = {field: config.get(key, default) for field, key, default in CONFIG_FIELDS.values()}
        # A missing/null mode preserves the original administrator enable switch.
        if values['mode'] is None:
            enabled = config.get('chat.context_compaction.enable', legacy_enabled)
            values['mode'] = 'legacy' if enabled else 'off'
        return cls(**values)


async def load_compaction_settings() -> CompactionSettings:
    from open_webui.models.config import Config

    values = await Config.get_many('chat.context_compaction.enable', *(v[1] for v in CONFIG_FIELDS.values()))
    return CompactionSettings.from_config(values)
