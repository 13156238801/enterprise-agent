"""实现不依赖数据库或 Web 框架的主数据 patch 与 Diff。"""

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum

from app.domain.master_data.errors import MasterDataDomainError, MasterDataDomainErrorCode

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_ENTITY_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,127}\Z", re.ASCII)


class MasterDataDiffOperation(StrEnum):
    """表示 Diff 中新增字段或更新字段的操作种类。"""

    ADD = "ADD"
    UPDATE = "UPDATE"


@dataclass(frozen=True)
class MasterDataDiffItem:
    """保存单个字段变更的名称、操作以及 before/after 值。"""

    field: str
    operation: MasterDataDiffOperation
    before: JsonValue
    after: JsonValue


def validate_entity_identity(entity_type: object, entity_key: object) -> None:
    """校验主数据类型和稳定业务键，不对输入自动改写大小写。"""
    if not isinstance(entity_type, str) or _ENTITY_TYPE_PATTERN.fullmatch(entity_type) is None:
        raise MasterDataDomainError(
            MasterDataDomainErrorCode.INVALID_ENTITY_TYPE,
            "entity_type 必须是 lowercase snake_case 且长度不超过 128。",
        )
    if (
        not isinstance(entity_key, str)
        or not 1 <= len(entity_key) <= 128
        or entity_key != entity_key.strip()
    ):
        raise MasterDataDomainError(
            MasterDataDomainErrorCode.INVALID_ENTITY_KEY,
            "entity_key 长度必须为 1 至 128 且不能含首尾空格。",
        )


def apply_master_data_patch(
    base: dict[str, JsonValue], changes: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """应用顶层 merge patch 并返回独立的完整 after snapshot。"""
    base_copy = validate_json_object(base, "data_json")
    patch_copy = validate_json_object(changes, "changes")
    if not patch_copy:
        raise MasterDataDomainError(
            MasterDataDomainErrorCode.INVALID_CHANGE, "changes 不能为空对象。"
        )
    for key in patch_copy:
        if not key or len(key) > 128:
            raise MasterDataDomainError(
                MasterDataDomainErrorCode.INVALID_CHANGE,
                "patch 字段名必须为非空字符串且长度不超过 128。",
            )
    return {**base_copy, **patch_copy}


def build_master_data_diff(
    before: dict[str, JsonValue], after: dict[str, JsonValue]
) -> tuple[MasterDataDiffItem, ...]:
    """生成按字段升序排列且值不与输入共享引用的结构化 Diff。"""
    before_copy = validate_json_object(before, "before")
    after_copy = validate_json_object(after, "after")
    items: list[MasterDataDiffItem] = []
    for field in sorted(before_copy.keys() | after_copy.keys()):
        in_before = field in before_copy
        in_after = field in after_copy
        before_value = before_copy.get(field)
        after_value = after_copy.get(field)
        if in_before == in_after and before_value == after_value:
            continue
        items.append(
            MasterDataDiffItem(
                field=field,
                operation=(
                    MasterDataDiffOperation.ADD if not in_before else MasterDataDiffOperation.UPDATE
                ),
                before=deepcopy(before_value),
                after=deepcopy(after_value),
            )
        )
    return tuple(items)


def validate_json_object(value: object, label: str) -> dict[str, JsonValue]:
    """验证顶层 JSON object 及递归值类型后返回深拷贝。"""
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        code = (
            MasterDataDomainErrorCode.DATA_NOT_OBJECT
            if not isinstance(value, dict)
            else MasterDataDomainErrorCode.DATA_NOT_JSON_COMPATIBLE
        )
        raise MasterDataDomainError(code, f"{label} 必须是以字符串键组成的 JSON object。")
    if not _is_json_value(value, set()):
        raise MasterDataDomainError(
            MasterDataDomainErrorCode.DATA_NOT_JSON_COMPATIBLE,
            f"{label} 含有非 JSON-compatible 值。",
        )
    return deepcopy(value)


def _is_json_value(value: object, active_ids: set[int]) -> bool:
    """递归判断值是否符合 JSON 类型、有限数值和无循环结构约束。"""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active_ids:
            return False
        active_ids.add(identity)
        try:
            if isinstance(value, list):
                return all(_is_json_value(item, active_ids) for item in value)
            return all(
                isinstance(key, str) and _is_json_value(item, active_ids)
                for key, item in value.items()
            )
        finally:
            active_ids.remove(identity)
    return False
