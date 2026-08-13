"""
仿真测试结果对比工具
功能：从PGSim平台获取仿真测试结果，对比base版本和test版本的测试结果差异
输出：生成Excel文件，包含详细的对比数据和汇总统计
"""

import argparse
import json
import os
import logging
import pandas as pd
import requests
from datetime import date
from typing import Dict, List, Set, Tuple, Optional
from collections import OrderedDict
from functools import lru_cache

# ========== 配置日志 ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# ========== 常量定义 ==========
BASE_URL = "https://pgsim.phigent.net"
API_TASK_DETAIL = f"{BASE_URL}/apiv2/sim/grading/task/getTaskRunningDetail"
API_METRIC_LIST = f"{BASE_URL}/apiv2/metric/getMetricList"
API_TASK_INFO = f"{BASE_URL}/apiv2/sim/grading/task/getGradingTaskInfo"
API_TASK_LIST = f"{BASE_URL}/apiv2/sim/grading/task/listTaskByPage"
REPLAY_BASE_URL = f"{BASE_URL}/task/replay?gradingSubTaskId="

# 需要的列名
REQUIRED_COLUMNS = [
    'scenarioId',
    'scenarioName',
    'failedReason',
    'sim_url',
    'gradingResult',
    'pass_metrics',
    'fail_metrics',
    'unknown_metrics',
]

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


def safe_request(
    method: str, url: str, headers: Dict = None, **kwargs
) -> Optional[Dict]:
    """
    安全的API请求封装，包含错误处理

    Args:
        method: HTTP方法 ('GET', 'POST'等)
        url: 请求URL
        headers: 请求头
        **kwargs: 其他requests参数

    Returns:
        dict: 响应JSON数据，失败返回None
    """
    try:
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"请求失败 {url}: {e}")
        return None


def get_userInfo(headers: Dict) -> Optional[Dict]:
    """
    获取当前用户信息

    Args:
        headers: 请求头

    Returns:
        dict: 包含用户名称(name)和用户ID(uid)的字典，如果请求失败返回None
    """
    url = f'{BASE_URL}/auth-api/v1/user/self/profile'
    result = safe_request('GET', url, headers=headers)
    if result and result.get('data'):
        return {'name': result['data']['ldap_name'], 'uid': result['data']['uid']}
    return None


def _classify_metrics(
    metric_list: List[Dict], rename_dict: Dict[str, str]
) -> Dict[str, List[str]]:
    """
    将指标列表按状态分类

    Args:
        metric_list: 指标列表
        rename_dict: 指标名称映射字典

    Returns:
        dict: 包含pass_metrics, fail_metrics, unknown_metrics的字典
    """
    pass_metrics = []
    fail_metrics = []
    unknown_metrics = []

    for metric in metric_list:
        metric_name = rename_dict.get(metric["metricName"], "未知")
        metric_status = metric["metricStatus"]
        if metric_status == 'fail':
            fail_metrics.append(metric_name)
        elif metric_status == "pass":
            pass_metrics.append(metric_name)
        else:
            unknown_metrics.append(metric_name)

    return {
        'pass_metrics': pass_metrics,
        'fail_metrics': fail_metrics,
        'unknown_metrics': unknown_metrics,
    }


def get_simresult(taskId: int, headers: Dict = None) -> Optional[pd.DataFrame]:
    """
    获取指定任务的仿真测试结果详情

    Args:
        taskId: 任务ID
        headers: 请求头（可选）

    Returns:
        DataFrame: 包含测试结果的DataFrame，失败返回None
    """
    data = {
        "pageSize": 10000,
        "taskId": taskId,
        "pageNo": 1,
        "withExtraInfo": False,
        "scenarioIds": [],
        "filterMetric": {},
        "dataCycleTags": [],
    }

    # 发送POST请求获取测试结果
    result = safe_request('POST', API_TASK_DETAIL, headers=headers, json=data)
    if not result or 'result' not in result or 'data' not in result['result']:
        logger.error(f"获取任务 {taskId} 的测试结果失败")
        return None

    res = result['result']['data']
    if not res:
        logger.warning(f"任务 {taskId} 没有测试结果数据")
        return pd.DataFrame()

    # 将结果转换为DataFrame并筛选需要的列
    df = pd.DataFrame(res)
    required_cols = [
        'gradingSubTaskId',
        'scenarioId',
        'scenarioName',
        'gradingResult',
        'failedReason',
        'failMetric',
        'metricList',
    ]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        logger.warning(f"缺少列 {missing_cols}")
        return None

    df = df[required_cols]

    # 生成仿真回放链接
    df['sim_url'] = REPLAY_BASE_URL + df['gradingSubTaskId'].astype(str)

    # 处理指标列表，按状态分类
    rename_dict = getMetricList()
    rows = []
    for item in res:
        scenario_id = item["scenarioId"]
        metrics = _classify_metrics(item.get("metricList", []), rename_dict)
        metrics["scenarioId"] = scenario_id
        rows.append(metrics)

    df_grading = pd.DataFrame(rows)

    # 合并测试结果和指标数据
    df = pd.merge(df, df_grading, on='scenarioId', how='inner')

    # 重新选择并排序列
    df = df[REQUIRED_COLUMNS]

    # 添加预留字段
    df['测试结果分析'] = None
    df['截图'] = None
    return df


@lru_cache(maxsize=1)
def getMetricList() -> Dict[str, str]:
    """
    获取指标列表，并构建指标名称到显示名称的映射字典
    使用缓存避免重复请求

    Returns:
        dict: 指标名称(metricName)到显示名称(metricDisplay)的映射字典
    """
    result = safe_request('GET', API_METRIC_LIST)
    if result and result.get('result'):
        metrics_list = result['result']
        return {item['metricName']: item['metricDisplay'] for item in metrics_list}
    return {}


def getTaskTitle(taskId: int, headers: Dict = None) -> Optional[str]:
    """
    获取任务的标题名称

    Args:
        taskId: 任务ID
        headers: 请求头（可选）

    Returns:
        str: 任务名称，失败返回None
    """
    url = f"{API_TASK_INFO}?gradingTaskId={taskId}"
    result = safe_request('GET', url, headers=headers)
    if result and result.get('result'):
        return result['result'].get('gradingTaskName')
    return None


def getTaskIds(
    list1: List[int], list2: List[int], headers: Dict = None
) -> Dict[int, int]:
    """
    根据simTaskId列表匹配对应的gradingTaskId对
    修复：保持与原代码相同的逻辑，确保所有匹配都能找到

    Args:
        list1: base版本的simTaskId列表
        list2: test版本的simTaskId列表
        headers: 请求头（可选）

    Returns:
        dict: base版本的gradingTaskId到test版本的gradingTaskId的映射字典
    """
    data = {"pageSize": 200000, "pageNo": 1}
    result = safe_request('POST', API_TASK_LIST, headers=headers, json=data)
    if not result or 'result' not in result or 'data' not in result['result']:
        logger.error("获取任务列表失败")
        return {}

    rep_data = result['result']['data']
    logger.info(f"获取到 {len(rep_data)} 个任务")
    # 使用 set 提升查找性能，列表过长时 O(1) vs O(n)
    list1_set: Set[int] = set(list1)
    list2_set: Set[int] = set(list2)
    logger.debug(f"查找list1中的simTaskId: {list1_set}")
    logger.debug(f"查找list2中的simTaskId: {list2_set}")

    # 构建字典：simTaskId -> (taskName, gradingTaskId)
    list1_tasks = {}  # {simTaskId: (taskName, gradingTaskId)} for list1
    list2_tasks = {}  # {simTaskId: (taskName, gradingTaskId)} for list2

    for task in rep_data:
        sim_task_id = task.get("simTaskId")
        task_name = task.get("taskName")
        grading_task_id = task.get("gradingTaskId")

        if sim_task_id in list1_set:
            list1_tasks[sim_task_id] = (task_name, grading_task_id)
            logger.debug(
                f"找到list1任务: simTaskId={sim_task_id}, taskName={task_name}, gradingTaskId={grading_task_id}"
            )
        if sim_task_id in list2_set:
            list2_tasks[sim_task_id] = (task_name, grading_task_id)
            logger.debug(
                f"找到list2任务: simTaskId={sim_task_id}, taskName={task_name}, gradingTaskId={grading_task_id}"
            )

    # 按照原逻辑：遍历list1中的每个任务，在list2中找taskName相同的任务
    task_ids = {}
    task_name1s = [task_name_1 for sim_task_id_1, (task_name_1, grading_task_id_1) in list1_tasks.items()]
    task_name2s = [task_name_2 for sim_task_id_2, (task_name_2, grading_task_id_2) in list2_tasks.items()]
    logger.info(f"task_name1s: {task_name1s}")
    logger.info(f"task_name2s: {task_name2s}")
    for sim_task_id_1, (task_name_1, grading_task_id_1) in list1_tasks.items():
        for sim_task_id_2, (task_name_2, grading_task_id_2) in list2_tasks.items():
            # if task_name_1 == task_name_2 or task_name_1.startswith(task_name_2) or task_name_2.startswith(task_name_1):
            if task_name_1 == task_name_2:
                task_ids[grading_task_id_1] = grading_task_id_2
                logger.info(
                    f"匹配成功: {task_name_1} -> base={grading_task_id_1}, test={grading_task_id_2}"
                )

    logger.info(f"总共找到 {len(task_ids)} 对匹配的任务")
    return task_ids


def calculate_statistics(merged_df: pd.DataFrame) -> Dict:
    """
    计算对比统计信息

    Args:
        merged_df: 合并后的DataFrame

    Returns:
        dict: 包含各种统计指标的字典
    """
    # 统计对比结果
    pass2fail = len(
        merged_df[
            (merged_df['base_gradingResult'] == 'pass')
            & (merged_df['test_gradingResult'] == 'fail')
        ]
    )
    fail2pass = len(
        merged_df[
            (merged_df['base_gradingResult'] == 'fail')
            & (merged_df['test_gradingResult'] == 'pass')
        ]
    )
    pass2pass = len(
        merged_df[
            (merged_df['base_gradingResult'] == 'pass')
            & (merged_df['test_gradingResult'] == 'pass')
        ]
    )
    fail2fail = len(
        merged_df[
            (merged_df['base_gradingResult'] == 'fail')
            & (merged_df['test_gradingResult'] == 'fail')
        ]
    )

    # 计算base版本通过率
    base_pass = len(
        merged_df[
            (merged_df['base_gradingResult'] == 'pass')
            & (merged_df['test_gradingResult'] != "")
        ]
    )
    base_fail = len(
        merged_df[
            (merged_df['base_gradingResult'] == 'fail')
            & (merged_df['test_gradingResult'] != "")
        ]
    )
    base_total = base_pass + base_fail
    base_pass_rate_1 = (base_pass / base_total * 100) if base_total != 0 else 0
    base_pass_rate = f"{round(base_pass_rate_1)}%"

    # 计算test版本通过率
    test_pass = len(
        merged_df[
            (merged_df['test_gradingResult'] == 'pass')
            & (merged_df['base_gradingResult'] != "")
        ]
    )
    test_fail = len(
        merged_df[
            (merged_df['test_gradingResult'] == 'fail')
            & (merged_df['base_gradingResult'] != "")
        ]
    )
    test_total = test_pass + test_fail
    test_pass_rate_1 = (test_pass / test_total * 100) if test_total != 0 else 0
    test_pass_rate = f"{round(test_pass_rate_1)}%"

    # 计算通过率差异
    diff_pass_rate = f"{round(test_pass_rate_1 - base_pass_rate_1)}%"

    return {
        "base_pass": base_pass,
        "base_fail": base_fail,
        "base_pass_rate": base_pass_rate,
        "test_pass": test_pass,
        "test_fail": test_fail,
        "test_pass_rate": test_pass_rate,
        "diff_pass_rate": diff_pass_rate,
        "pass->fail": pass2fail,
        "fail->pass": fail2pass,
        "pass->pass": pass2pass,
        "fail->fail": fail2fail,
    }


def process_version_data(
    task_id: int, prefix: str, headers: Dict
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    处理单个版本的数据（base或test）

    Args:
        task_id: 任务ID
        prefix: 列名前缀（'base'或'test'）
        headers: 请求头

    Returns:
        tuple: (DataFrame, 任务标题)
    """
    title = getTaskTitle(task_id, headers)
    if not title:
        logger.warning(f"无法获取任务 {task_id} 的标题")
        return None, None

    df = get_simresult(task_id, headers)
    if df is None or df.empty:
        logger.warning(f"任务 {task_id} 没有数据")
        return None, None

    df.name = title

    # 筛选需要的列（保留预留字段）
    columns_to_select = REQUIRED_COLUMNS + ['测试结果分析', '截图']
    available_cols = [col for col in columns_to_select if col in df.columns]
    df = df[available_cols]

    # 重命名列，添加前缀
    rename_map = {
        'failedReason': f'{prefix}_failedReason',
        'sim_url': f'{prefix}_sim_url',
        'gradingResult': f'{prefix}_gradingResult',
        'pass_metrics': f'{prefix}_pass_metrics',
        'fail_metrics': f'{prefix}_fail_metrics',
        'unknown_metrics': f'{prefix}_unknown_metrics',
    }
    df.rename(columns=rename_map, inplace=True)

    return df, title


def load_auth_token() -> str:
    """
    从文件加载认证token

    Returns:
        str: Bearer token
    """
    auth_file = os.path.join('input', 'auth.txt')
    if os.path.exists(auth_file):
        with open(auth_file, 'r', encoding='utf-8') as f:
            token = f.read().strip()
            if not token.startswith('Bearer '):
                token = f'Bearer {token}'
            return token
    return ""


def load_project_config(project_key: str) -> Optional[Dict]:
    """
    从配置文件加载项目配置（上汽/广汽等）

    Args:
        project_key: 项目键名，如 'GAC'、'SAIC'

    Returns:
        dict: 包含 base_sim_task_ids、test_sim_task_ids、output_prefix 的配置，失败返回 None
    """
    config_path = os.path.join('input', 'sim_config.json')
    if not os.path.exists(config_path):
        logger.error(f"配置文件不存在: {config_path}")
        return None
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        projects = config.get('projects', {})
        if project_key not in projects:
            logger.error(f"未知项目: {project_key}，可选: {list(projects.keys())}")
            return None
        proj = projects[project_key]
        for key in ('base_sim_task_ids', 'test_sim_task_ids', 'output_prefix'):
            if key not in proj:
                logger.error(f"配置缺少字段: {key}")
                return None
        return proj
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"加载配置失败: {e}")
        return None


def load_all_projects() -> Dict[str, Dict]:
    """加载配置中所有项目，返回 {project_key: config}"""
    config_path = os.path.join('input', 'sim_config.json')
    if not os.path.exists(config_path):
        logger.error(f"配置文件不存在: {config_path}")
        return {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config.get('projects', {})
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"加载配置失败: {e}")
        return {}


def _resolve_detail_sheet_group(title: str, groups_cfg: List[Dict]) -> Optional[str]:
    """
    为任务标题选择明细 sheet。
    在所有分组关键词中选取「匹配且最长」的关键词，避免短关键词误匹配
    （如 MEB 抢匹配 MEB性能测试）。同长度时按 detail_sheet_groups 顺序优先。
    """
    best_sheet: Optional[str] = None
    best_kw_len = -1
    best_group_idx = len(groups_cfg)

    for gi, g in enumerate(groups_cfg):
        sn = str(g.get('sheet_name', '')).strip()[:31]
        if not sn:
            continue
        for kw in g.get('title_keywords', []) or []:
            if isinstance(kw, str) and kw and kw in title:
                kw_len = len(kw)
                if kw_len > best_kw_len or (
                    kw_len == best_kw_len and gi < best_group_idx
                ):
                    best_kw_len = kw_len
                    best_sheet = sn
                    best_group_idx = gi
    return best_sheet


def _apply_detail_sheet_groups(
    df_list: List[pd.DataFrame], proj_config: Dict
) -> List[pd.DataFrame]:
    """
    按配置将多个专项明细合并为少量 sheet（如北汽 5 大类）。
    任务标题匹配规则：命中最长关键词的分组优先；同长度时按
    detail_sheet_groups 顺序优先。未命中则归入 detail_sheet_fallback。
    「汇总」sheet 不参与分组，仍附在最后。
    """
    groups_cfg = proj_config.get('detail_sheet_groups') or []
    if not isinstance(groups_cfg, list) or not groups_cfg:
        return df_list

    summaries = [df for df in df_list if getattr(df, 'name', None) == '汇总']
    details = [df for df in df_list if getattr(df, 'name', None) != '汇总']
    if not details:
        return df_list

    buckets: OrderedDict[str, List[pd.DataFrame]] = OrderedDict()
    for g in groups_cfg:
        sn = str(g.get('sheet_name', '')).strip()[:31]
        if sn and sn not in buckets:
            buckets[sn] = []

    if not buckets:
        logger.warning('detail_sheet_groups 未配置有效 sheet_name，跳过分组合并')
        return df_list

    fallback = str(proj_config.get('detail_sheet_fallback', '')).strip()[:31]
    if fallback not in buckets:
        fallback = next(iter(buckets.keys()))

    for df in details:
        title = getattr(df, 'name', '') or ''
        assigned = _resolve_detail_sheet_group(title, groups_cfg)
        if assigned and assigned not in buckets:
            assigned = None
        if not assigned:
            logger.warning(
                '任务标题未匹配任何分组，归入「%s」: %s',
                fallback,
                title[:120] if title else '',
            )
            assigned = fallback
        buckets[assigned].append(df)

    out: List[pd.DataFrame] = []
    for sheet_name, dfs in buckets.items():
        if not dfs:
            continue
        parts: List[pd.DataFrame] = []
        for d0 in dfs:
            d = d0.copy()
            d.insert(0, '专项名称', getattr(d0, 'name', '') or '')
            parts.append(d)
        combined = pd.concat(parts, ignore_index=True)
        combined.name = sheet_name
        out.append(combined)

    return out + summaries


def run_single_project(
    project_key: str,
    proj_config: Dict,
    headers: Dict,
    report_dir: str,
    today_date: date,
) -> Optional[str]:
    """
    执行单个项目的对比并导出Excel

    Returns:
        成功返回输出文件路径，失败返回 None
    """
    base_sim_task_ids = proj_config['base_sim_task_ids']
    test_sim_task_ids = proj_config['test_sim_task_ids']
    output_prefix = proj_config['output_prefix']

    if not base_sim_task_ids or not test_sim_task_ids:
        logger.warning(f"项目 {project_key} ({proj_config.get('description', '')}) task IDs 为空，跳过")
        return None

    logger.info(f"========== 处理项目: {project_key} ({proj_config.get('description', '')}) ==========")
    task_ids = getTaskIds(base_sim_task_ids, test_sim_task_ids, headers)
    if not task_ids:
        logger.warning(f"项目 {project_key} 未找到匹配的任务对，跳过")
        return None

    logger.info(f"项目 {project_key} 找到 {len(task_ids)} 对匹配的任务")
    df_all = pd.DataFrame()
    df_list = []

    for base_task_id, test_task_id in task_ids.items():
        logger.info(f"处理任务对: base={base_task_id}, test={test_task_id}")

        base_df, base_title = process_version_data(base_task_id, 'base', headers)
        if base_df is None:
            logger.warning(f"跳过任务对 {base_task_id}-{test_task_id}：base版本数据获取失败")
            continue

        test_df, test_title = process_version_data(test_task_id, 'test', headers)
        if test_df is None:
            logger.warning(f"跳过任务对 {base_task_id}-{test_task_id}：test版本数据获取失败")
            continue

        logger.info(f"对比: {base_title} vs {test_title}")

        merged_df = pd.merge(test_df, base_df, on='scenarioId', how='inner')
        if merged_df.empty:
            logger.warning(f"任务对 {base_task_id}-{test_task_id} 没有共同的场景")
            continue

        merged_df["对比"] = (
            merged_df['base_gradingResult'] + ' -> ' + merged_df['test_gradingResult']
        )
        merged_df["scenarioName"] = merged_df["scenarioName_x"]
        merged_df['新增_fail_metrics'] = 0
        if '测试结果分析' not in merged_df.columns:
            merged_df['测试结果分析'] = None
        if '截图' not in merged_df.columns:
            merged_df['截图'] = None

        available_cols = [col for col in COMPARE_COLUMN_ORDER if col in merged_df.columns]
        merged_df = merged_df[available_cols]
        merged_df.name = test_title
        df_list.append(merged_df)

        stats = calculate_statistics(merged_df)
        project_name = (
            "_".join(test_title.split('_')[2:-1])
            if len(test_title.split('_')) > 3
            else test_title
        )
        summary_dict = {"专项名称": project_name, **stats}
        tmp_row = pd.Series(summary_dict)
        df_all = pd.concat([df_all, tmp_row.to_frame().T], ignore_index=True)

    if not df_all.empty:
        df_all.name = '汇总'
        df_list.append(df_all)

    if not df_list:
        logger.warning(f"项目 {project_key} 没有数据可导出")
        return None

    df_list = _apply_detail_sheet_groups(df_list, proj_config)
    if proj_config.get('detail_sheet_groups'):
        logger.info(
            f"项目 {project_key} 已按 detail_sheet_groups 合并明细，"
            f"共 {len(df_list)} 个工作表（含汇总）"
        )

    filepath = os.path.join(report_dir, f'{output_prefix}_{today_date.strftime("%Y%m%d")}.xlsx')
    try:
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            for merged_df in df_list:
                sheet_name = (
                    merged_df.name[0:30]
                    if hasattr(merged_df, 'name') and merged_df.name
                    else 'Unnamed'
                )
                merged_df.to_excel(writer, sheet_name=sheet_name, index=False)
        logger.info(f"项目 {project_key} Excel 已生成: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"项目 {project_key} 导出失败: {e}", exc_info=True)
        return None


if __name__ == '__main__':
    # ========== 解析命令行参数 ==========
    parser = argparse.ArgumentParser(description='仿真测试结果对比工具（支持上汽/广汽等）')
    parser.add_argument(
        '-p', '--project',
        default=None,
        help='只运行指定项目，可选: GAC(广汽), SAIC(上汽), BAIC(北汽)。不指定则默认运行所有项目',
    )
    parser.add_argument(
        '-a', '--all',
        action='store_true',
        help='同时运行所有项目（北汽、上汽、广丰），分别输出到各自的 Excel 文件',
    )
    parser.add_argument('-l', '--list-projects', action='store_true', help='列出可用项目并退出')
    args = parser.parse_args()
    args.project = "SAIC_AS5PR"

    # --all 与 -p 互斥；默认运行所有项目
    if args.all and args.project:
        logger.error("不能同时使用 --all 和 --project，请二选一")
        exit(1)
    if not args.project:
        args.all = True  # 默认运行所有项目（北汽、上汽、广丰）

    # ========== 加载项目配置 ==========
    if args.list_projects:
        config_path = os.path.join('input', 'sim_config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            projects = config.get('projects', {})
            print("可用项目:")
            for k, v in projects.items():
                print(f"  {k}: {v.get('description', '')} (output_prefix={v.get('output_prefix')})")
        else:
            print("配置文件不存在")
        exit(0)

    # ========== 初始化配置 ==========
    token = load_auth_token()
    if not token:
        logger.warning("未找到认证token，请检查 input/auth.txt 文件")
        token = "Bearer "  # 使用空token，可能会失败

    headers = {
        'Authorization': token,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    }
    today_date = date.today()
    report_dir = os.path.abspath(os.path.dirname(__file__)) + os.sep + 'output'
    os.makedirs(report_dir, exist_ok=True)

    if args.all:
        # ========== 运行所有项目 ==========
        all_projects = load_all_projects()
        if not all_projects:
            exit(1)
        success_files = []
        for project_key, proj_config in all_projects.items():
            for key in ('base_sim_task_ids', 'test_sim_task_ids', 'output_prefix'):
                if key not in proj_config:
                    logger.warning(f"项目 {project_key} 配置缺少 {key}，跳过")
                    break
            else:
                fp = run_single_project(project_key, proj_config, headers, report_dir, today_date)
                if fp:
                    success_files.append(fp)
        if success_files:
            logger.info(f"全部完成，共生成 {len(success_files)} 个文件: {success_files}")
        else:
            logger.warning("未成功生成任何文件")
    else:
        # ========== 运行单个项目 ==========
        proj_config = load_project_config(args.project)
        if not proj_config:
            exit(1)
        fp = run_single_project(args.project, proj_config, headers, report_dir, today_date)
        if not fp:
            exit(1)
