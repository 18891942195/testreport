"""明细 sheet 分组与 Excel 写出。"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from .config import ProjectConfig, SheetGroup

logger = logging.getLogger(__name__)

# Excel 工作表名长度上限
MAX_SHEET_NAME_LEN = 31
SUMMARY_SHEET_NAME = '汇总'
SPECIALTY_COLUMN = '专项名称'


@dataclass
class Sheet:
    """一个待写出的工作表。

    早先的实现把表名挂在 ``DataFrame.name`` 上，而这种自定义属性在 ``df[cols]``
    切片后会**静默丢失**（切片返回的是新对象），得靠调用方记得在最后一次切片之后
    再赋值。这里改成显式配对，表名不再依赖 DataFrame 的生命周期。
    """

    name: str
    frame: pd.DataFrame


def resolve_sheet_group(title: str, groups: Sequence[SheetGroup]) -> str | None:
    """为任务标题挑选明细 sheet。

    命中「最长」关键词的分组优先，避免短关键词抢匹配（如 MEB 抢走 MEB性能测试）；
    关键词等长时按 detail_sheet_groups 的配置顺序优先（靠严格大于比较实现）。

    Returns:
        sheet 名；没有任何关键词命中时返回 None。
    """
    best_sheet: str | None = None
    best_keyword_len = -1
    for group in groups:
        if not group.sheet_name:
            continue
        for keyword in group.title_keywords:
            if keyword in title and len(keyword) > best_keyword_len:
                best_keyword_len = len(keyword)
                best_sheet = group.sheet_name
    return best_sheet


def group_detail_sheets(
    sheets: Sequence[Sheet], project: ProjectConfig
) -> list[Sheet]:
    """按 detail_sheet_groups 把多个专项明细合并成少量 sheet（如北汽 5 大类）。

    未命中任何关键词的归入 detail_sheet_fallback；合并后每行会带上原专项名。
    「汇总」不参与分组，始终排在最后。未配置分组时原样返回。
    """
    groups = project.detail_sheet_groups
    if not groups:
        return list(sheets)

    summaries = [s for s in sheets if s.name == SUMMARY_SHEET_NAME]
    details = [s for s in sheets if s.name != SUMMARY_SHEET_NAME]
    if not details:
        return list(sheets)

    buckets: OrderedDict[str, list[Sheet]] = OrderedDict(
        (group.sheet_name, []) for group in groups if group.sheet_name
    )
    if not buckets:
        logger.warning('detail_sheet_groups 未配置有效 sheet_name，跳过分组合并')
        return list(sheets)

    fallback = project.detail_sheet_fallback
    if fallback not in buckets:
        fallback = next(iter(buckets))

    for sheet in details:
        assigned = resolve_sheet_group(sheet.name, groups)
        if assigned not in buckets:
            logger.warning(
                '任务标题未匹配任何分组，归入「%s」: %s', fallback, sheet.name[:120]
            )
            assigned = fallback
        buckets[assigned].append(sheet)

    merged: list[Sheet] = []
    for sheet_name, bucket in buckets.items():
        if not bucket:
            continue
        parts = []
        for sheet in bucket:
            frame = sheet.frame.copy()
            frame.insert(0, SPECIALTY_COLUMN, sheet.name)
            parts.append(frame)
        combined = pd.concat(parts, ignore_index=True)
        merged.append(Sheet(name=sheet_name, frame=combined))

    return merged + summaries


def _unique_sheet_name(name: str, used: set[str]) -> str:
    """截断到 Excel 上限，并在重名时加 _2、_3… 后缀。

    长标题截断后可能撞名，而重名会产出坏工作簿，所以这里必须去重。
    """
    candidate = (name or 'Unnamed')[:MAX_SHEET_NAME_LEN]
    if candidate not in used:
        used.add(candidate)
        return candidate

    for index in range(2, 1000):
        suffix = f'_{index}'
        renamed = candidate[: MAX_SHEET_NAME_LEN - len(suffix)] + suffix
        if renamed not in used:
            logger.warning(f'工作表名重复，「{candidate}」重命名为「{renamed}」')
            used.add(renamed)
            return renamed
    raise ValueError(f'无法为工作表生成唯一名称: {name}')


def write_workbook(sheets: Iterable[Sheet], path: Path | str) -> None:
    """把所有工作表写入一个 xlsx 文件。"""
    used: set[str] = set()
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        for sheet in sheets:
            sheet.frame.to_excel(
                writer, sheet_name=_unique_sheet_name(sheet.name, used), index=False
            )
