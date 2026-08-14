"""对比逻辑：指标分类、明细表构建、base/test 合并、通过率统计、任务配对。

本模块只处理数据，不发请求 —— 接口返回的原始记录由 api.PGSimClient 取得后传进来，
这样每个函数都能脱网单测。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .api import REPLAY_URL_PREFIX

logger = logging.getLogger(__name__)

# 预留给人工填写的列
ANALYSIS_COLUMNS = ('测试结果分析', '截图')

# 三个指标分类列
METRIC_STATUS_COLUMNS = ('pass_metrics', 'fail_metrics', 'unknown_metrics')

# 指标名在映射表里查不到时的显示值
UNKNOWN_METRIC_NAME = '未知'

# 单版本明细表的列（顺序即输出顺序）
RESULT_COLUMNS = [
    'scenarioId',
    'scenarioName',
    'failedReason',
    'sim_url',
    'gradingResult',
    *METRIC_STATUS_COLUMNS,
]

# 接口返回中必须存在的字段。failMetric 不进最终报表，但缺它说明返回结构变了，
# 因此保留校验（与历史行为一致：缺字段则整个任务跳过）。
_SOURCE_COLUMNS = [
    'gradingSubTaskId',
    'scenarioId',
    'scenarioName',
    'gradingResult',
    'failedReason',
    'failMetric',
    'metricList',
]

# 加 base_/test_ 前缀的列（scenarioId、scenarioName 与人工列保持原名）
_PREFIXED_COLUMNS = ('failedReason', 'sim_url', 'gradingResult', *METRIC_STATUS_COLUMNS)

# 对比结果列顺序
COMPARE_COLUMN_ORDER = [
    'scenarioId',
    'scenarioName',
    'base_failedReason',
    'base_sim_url',
    'base_gradingResult',
    'test_failedReason',
    'test_sim_url',
    'test_gradingResult',
    '对比',
    '测试结果分析',
    '截图',
    '新增_fail_metrics',
    'base_pass_metrics',
    'base_fail_metrics',
    'base_unknown_metrics',
    'test_pass_metrics',
    'test_fail_metrics',
    'test_unknown_metrics',
]


def classify_metrics(
    metric_list: Iterable[Mapping[str, Any]] | None,
    display_map: Mapping[str, str],
) -> dict[str, list[str]]:
    """按 metricStatus 把指标分成 pass / fail / unknown 三组，用展示名表示。"""
    classified: dict[str, list[str]] = {name: [] for name in METRIC_STATUS_COLUMNS}
    for metric in metric_list or ():
        name = display_map.get(metric.get('metricName'), UNKNOWN_METRIC_NAME)
        status = metric.get('metricStatus')
        if status == 'fail':
            classified['fail_metrics'].append(name)
        elif status == 'pass':
            classified['pass_metrics'].append(name)
        else:
            classified['unknown_metrics'].append(name)
    return classified


def build_result_frame(
    records: Sequence[Mapping[str, Any]],
    display_map: Mapping[str, str],
) -> pd.DataFrame | None:
    """把接口明细记录转成单版本结果表。

    Returns:
        结果表；缺必要字段时返回 None，records 为空时返回空表。
    """
    empty_columns = [*RESULT_COLUMNS, *ANALYSIS_COLUMNS]
    if not records:
        return pd.DataFrame(columns=empty_columns)

    df = pd.DataFrame(records)
    missing = [col for col in _SOURCE_COLUMNS if col not in df.columns]
    if missing:
        logger.warning(f'接口返回缺少列 {missing}')
        return None

    df = df[_SOURCE_COLUMNS].copy()
    # 仿真回放链接
    df['sim_url'] = REPLAY_URL_PREFIX + df['gradingSubTaskId'].astype(str)

    # 指标分类：classified 与 df 的行序都来自 records，按位置赋列即可。
    # （旧实现在这里对 scenarioId 做 merge —— 多余，且重复 scenarioId 会产生笛卡尔积。）
    classified = [classify_metrics(r.get('metricList'), display_map) for r in records]
    for column in METRIC_STATUS_COLUMNS:
        df[column] = pd.Series(
            [item[column] for item in classified], index=df.index, dtype=object
        )

    df = df[RESULT_COLUMNS].copy()
    for column in ANALYSIS_COLUMNS:
        df[column] = None
    return df


def prefix_columns(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """给版本相关列加上 base_ / test_ 前缀。"""
    return df.rename(columns={col: f'{prefix}_{col}' for col in _PREFIXED_COLUMNS})


def compare_frames(base_df: pd.DataFrame, test_df: pd.DataFrame) -> pd.DataFrame:
    """按 scenarioId 内连接 test 与 base，生成对比表。

    scenarioName 与人工列以 test 侧为准：先丢掉 base 侧的同名列，
    避免 merge 产生 scenarioName_x / scenarioName_y 之类的后缀列。
    """
    base_side = base_df.drop(
        columns=['scenarioName', *ANALYSIS_COLUMNS], errors='ignore'
    )
    merged = pd.merge(test_df, base_side, on='scenarioId', how='inner')
    if merged.empty:
        return merged

    merged['对比'] = (
        merged['base_gradingResult'] + ' -> ' + merged['test_gradingResult']
    )
    # 占位列：目前恒为 0，未计算「test 相比 base 新增的 fail 指标」
    merged['新增_fail_metrics'] = 0

    columns = [col for col in COMPARE_COLUMN_ORDER if col in merged.columns]
    return merged[columns].copy()


def calculate_statistics(merged_df: pd.DataFrame) -> dict[str, Any]:
    """统计一个专项的通过率与 pass/fail 流转情况。"""
    base = merged_df['base_gradingResult']
    test = merged_df['test_gradingResult']
    base_is_pass = base == 'pass'
    base_is_fail = base == 'fail'
    test_is_pass = test == 'pass'
    test_is_fail = test == 'fail'
    # 与历史实现一致：算通过率时排除对侧结果为空串的行
    base_counted = test != ''
    test_counted = base != ''

    base_pass = int((base_is_pass & base_counted).sum())
    base_fail = int((base_is_fail & base_counted).sum())
    base_total = base_pass + base_fail
    base_pass_rate = (base_pass / base_total * 100) if base_total else 0

    test_pass = int((test_is_pass & test_counted).sum())
    test_fail = int((test_is_fail & test_counted).sum())
    test_total = test_pass + test_fail
    test_pass_rate = (test_pass / test_total * 100) if test_total else 0

    return {
        'base_pass': base_pass,
        'base_fail': base_fail,
        'base_pass_rate': f'{round(base_pass_rate)}%',
        'test_pass': test_pass,
        'test_fail': test_fail,
        'test_pass_rate': f'{round(test_pass_rate)}%',
        'diff_pass_rate': f'{round(test_pass_rate - base_pass_rate)}%',
        'pass->fail': int((base_is_pass & test_is_fail).sum()),
        'fail->pass': int((base_is_fail & test_is_pass).sum()),
        'pass->pass': int((base_is_pass & test_is_pass).sum()),
        'fail->fail': int((base_is_fail & test_is_fail).sum()),
    }


def match_task_pairs(
    tasks: Iterable[Mapping[str, Any]],
    base_sim_task_ids: Iterable[int],
    test_sim_task_ids: Iterable[int],
) -> dict[int, int]:
    """在全量任务里挑出 base / test 两侧的任务，再按 taskName 相同配对。

    注意入参是 **simTaskId**，返回的是 **gradingTaskId** —— 两者不是一回事。

    Returns:
        {base_gradingTaskId: test_gradingTaskId}。test 侧同名任务以最后一个为准
        （与历史的双层循环结果一致）。
    """
    base_ids = set(base_sim_task_ids)
    test_ids = set(test_sim_task_ids)

    # simTaskId -> (taskName, gradingTaskId)
    base_tasks: dict[Any, tuple[Any, Any]] = {}
    # taskName -> gradingTaskId，用字典替掉原来 O(n²) 的双层循环
    test_by_name: dict[Any, Any] = {}

    for task in tasks:
        sim_task_id = task.get('simTaskId')
        task_name = task.get('taskName')
        grading_task_id = task.get('gradingTaskId')
        if sim_task_id in base_ids:
            base_tasks[sim_task_id] = (task_name, grading_task_id)
        if sim_task_id in test_ids:
            test_by_name[task_name] = grading_task_id

    logger.info(f'base 侧任务名: {[name for name, _ in base_tasks.values()]}')
    logger.info(f'test 侧任务名: {list(test_by_name)}')

    pairs: dict[int, int] = {}
    for task_name, base_grading_id in base_tasks.values():
        test_grading_id = test_by_name.get(task_name)
        if test_grading_id is None:
            continue
        pairs[base_grading_id] = test_grading_id
        logger.info(
            f'匹配成功: {task_name} -> base={base_grading_id}, test={test_grading_id}'
        )

    logger.info(f'总共找到 {len(pairs)} 对匹配的任务')
    return pairs
