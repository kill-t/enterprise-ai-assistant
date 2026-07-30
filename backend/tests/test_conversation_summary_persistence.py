import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.conversation.entity.conversation_summary import (
    ConversationSummaryEntity,
)
from app.modules.conversation.repository.conversation_summary_repository import (
    ConversationSummaryRepository,
)
from app.modules.conversation.schema.conversation_summary_schema import (
    ConversationSummaryContent,
)


def build_summary() -> ConversationSummaryEntity:
    """创建带覆盖游标的摘要实体，供 Repository 测试复用。"""
    covered_at = datetime.now(timezone.utc)
    return ConversationSummaryEntity(
        conversation_id=uuid4(),
        content="## 用户目标与背景\n准备项目会议",
        covered_until_created_at=covered_at,
        covered_until_message_id=uuid4(),
        token_count=12,
        version=1,
        created_at=covered_at,
        updated_at=covered_at,
    )


def test_summary_uses_conversation_id_as_single_primary_key() -> None:
    """会话 ID 作为主键，应从数据库层保证一个会话最多一条摘要。"""
    table = ConversationSummaryEntity.__table__

    assert list(table.primary_key.columns.keys()) == ["conversation_id"]
    conversation_fk = next(iter(table.c.conversation_id.foreign_keys))
    assert conversation_fk.target_fullname == "conversations.id"
    assert conversation_fk.ondelete == "CASCADE"


def test_summary_cursor_message_is_nullable_and_cleared_with_message() -> None:
    """游标消息被删除时允许 ID 置空，时间游标仍能保留诊断边界。"""
    column = ConversationSummaryEntity.__table__.c.covered_until_message_id
    cursor_fk = next(iter(column.foreign_keys))

    assert column.nullable is True
    assert cursor_fk.target_fullname == "messages.id"
    assert cursor_fk.ondelete == "SET NULL"


def test_structured_summary_renders_fixed_chinese_sections() -> None:
    """结构化摘要应按固定栏目生成可直接放入 Prompt 的文本。"""
    content = ConversationSummaryContent(
        user_goal_and_context="准备季度项目会议",
        confirmed_facts_and_decisions="会议时间确定为周五上午",
        completed_items="已经收集项目进度",
        open_questions_and_todos="需要确认风险负责人",
        useful_preferences="用户希望简报简洁",
    )

    rendered = content.to_prompt_text()

    assert rendered.index("## 用户目标与背景") < rendered.index(
        "## 已确认的事实、约束和决定"
    )
    assert "准备季度项目会议" in rendered
    assert "用户希望简报简洁" in rendered


def test_structured_summary_rejects_empty_sections() -> None:
    """固定摘要栏目不得为空，避免生成无法支持后续对话的空壳摘要。"""
    with pytest.raises(ValidationError):
        ConversationSummaryContent(
            user_goal_and_context="",
            confirmed_facts_and_decisions="已确认事实",
            completed_items="暂无",
            open_questions_and_todos="暂无",
            useful_preferences="暂无",
        )


def test_repository_creates_summary_without_committing_transaction() -> None:
    """Repository 只刷新实体，事务提交仍应由后续 Service 控制。"""
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    repository = ConversationSummaryRepository(db)
    summary = build_summary()

    result = asyncio.run(repository.create(summary))

    assert result is summary
    db.add.assert_called_once_with(summary)
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(summary)
    assert not hasattr(db, "commit")


def test_repository_queries_summary_by_conversation_id() -> None:
    """摘要查询必须使用会话 ID，使后续 Service 能先复用会话所有权保护。"""
    summary = build_summary()
    db = SimpleNamespace(scalar=AsyncMock(return_value=summary))
    repository = ConversationSummaryRepository(db)

    result = asyncio.run(repository.get_by_conversation_id(summary.conversation_id))
    statement = db.scalar.await_args.args[0]

    assert result is summary
    assert "conversation_summaries.conversation_id" in str(statement)


def test_repository_updates_summary_without_incrementing_version_implicitly() -> None:
    """Repository 不擅自执行版本业务规则，只刷新上层已修改的摘要实体。"""
    db = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())
    repository = ConversationSummaryRepository(db)
    summary = build_summary()
    summary.version = 2

    result = asyncio.run(repository.update(summary))

    assert result.version == 2
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(summary)


def test_summary_migration_follows_current_head_and_defines_constraints() -> None:
    """迁移必须接在当前 head，并显式包含级联、游标外键和数据约束。"""
    migration = Path(
        "migrations/versions/d91e7f3a2c40_create_conversation_summaries_table.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "a4d8f2c7e615"' in migration
    assert '"conversation_summaries"' in migration
    assert 'ondelete="CASCADE"' in migration
    assert 'ondelete="SET NULL"' in migration
    assert '"ck_conversation_summaries_token_count_nonnegative"' in migration
    assert '"ck_conversation_summaries_version_positive"' in migration
