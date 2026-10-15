"""主数据 patch 与 Diff 的纯领域行为测试。"""

import pytest

from app.domain.master_data.diff import (
    MasterDataDiffOperation,
    apply_master_data_patch,
    build_master_data_diff,
    validate_entity_identity,
)
from app.domain.master_data.errors import MasterDataDomainError


def test_patch_merges_top_level_values_and_replaces_nested_objects() -> None:
    """验证 patch 只合并顶层字段且嵌套对象整体替换。"""
    current = {"name": "Supplier", "profile": {"region": "east", "tier": 1}}
    patch = {"profile": {"region": "west"}, "enabled": None}

    after = apply_master_data_patch(current, patch)

    assert after == {
        "name": "Supplier",
        "profile": {"region": "west"},
        "enabled": None,
    }
    assert current["profile"] == {"region": "east", "tier": 1}
    assert patch["profile"] == {"region": "west"}


def test_patch_and_snapshot_reject_non_json_values() -> None:
    """验证主数据快照和 patch 仅接受 JSON-compatible 值。"""
    with pytest.raises(MasterDataDomainError):
        apply_master_data_patch({"items": (1, 2)}, {"enabled": True})
    with pytest.raises(MasterDataDomainError):
        apply_master_data_patch({"enabled": True}, {"items": {1: "bad-key"}})


def test_patch_rejects_empty_or_invalid_top_level_keys() -> None:
    """验证空 patch、空字段名和超长字段名使用领域错误拒绝。"""
    for patch in ({}, {"": 1}, {"x" * 129: 1}):
        with pytest.raises(MasterDataDomainError):
            apply_master_data_patch({"enabled": True}, patch)


def test_entity_identity_validation_preserves_valid_key_case() -> None:
    """验证 entity_type 格式约束与 entity_key 保留原有大小写。"""
    validate_entity_identity("supplier", "SUP-001")
    for entity_type in ("Supplier", "1supplier", "bad-name", "x" * 129):
        with pytest.raises(MasterDataDomainError):
            validate_entity_identity(entity_type, "supplier_a")
    for entity_key in ("", " supplier_a", "supplier_a ", "x" * 129):
        with pytest.raises(MasterDataDomainError):
            validate_entity_identity("supplier", entity_key)


def test_diff_has_add_update_only_and_is_sorted_independently_of_input_order() -> None:
    """验证 Diff 只输出 ADD/UPDATE 并按字段名稳定升序排列。"""
    diff = build_master_data_diff(
        {"zeta": 1, "keep": None, "nested": {"a": 1}},
        {"nested": {"a": 2}, "beta": None, "zeta": 2, "keep": None},
    )

    assert [(item.field, item.operation) for item in diff] == [
        ("beta", MasterDataDiffOperation.ADD),
        ("nested", MasterDataDiffOperation.UPDATE),
        ("zeta", MasterDataDiffOperation.UPDATE),
    ]
    assert diff[0].before is None and diff[0].after is None
    assert diff[1].before == {"a": 1} and diff[1].after == {"a": 2}


def test_diff_returns_empty_for_noop_and_does_not_alias_inputs() -> None:
    """验证无变化时 Diff 为空且输出快照不与输入对象共享引用。"""
    before = {"profile": {"name": "A"}}
    after = {"profile": {"name": "B"}}

    diff = build_master_data_diff(before, after)
    after["profile"]["name"] = "mutated"

    assert diff[0].after == {"name": "B"}
    assert build_master_data_diff(before, {"profile": {"name": "A"}}) == ()


def test_iterative_patch_recomputes_diff_against_original_snapshot() -> None:
    """验证多次 patch 累积到 after 且改回原值后对应 Diff 消失。"""
    before = {"currency": "CNY", "payment_terms": "PREPAID"}
    after = apply_master_data_patch(before, {"payment_terms": "NET_30"})
    after = apply_master_data_patch(after, {"currency": "USD"})
    after = apply_master_data_patch(after, {"payment_terms": "PREPAID"})

    diff = build_master_data_diff(before, after)

    assert after == {"currency": "USD", "payment_terms": "PREPAID"}
    assert [(item.field, item.operation) for item in diff] == [
        ("currency", MasterDataDiffOperation.UPDATE)
    ]
