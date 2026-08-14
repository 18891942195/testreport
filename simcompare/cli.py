"""命令行参数解析与整体编排。"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

import pandas as pd

from .api import PGSimClient
from .compare import (
    build_result_frame,
    calculate_statistics,
    compare_frames,
    match_task_pairs,
    prefix_columns,
)
from .config import (
    PROJECT_ROOT,
    AppConfig,
    ConfigError,
    ProjectConfig,
    load_auth_token,
    load_config,
)
from .sheets import (
    SPECIALTY_COLUMN,
    SUMMARY_SHEET_NAME,
    Sheet,
    group_detail_sheets,
    write_workbook,
)

logger = logging.getLogger(__name__)

OUTPUT_DIR = PROJECT_ROOT / 'output'


def _load_version(
    client: PGSimClient,
    grading_task_id: int,
    prefix: str,
    display_map: dict[str, str],
) -> tuple[pd.DataFrame, str] | None:
    """取单个版本（base 或 test）的明细并加上列前缀。

    Returns:
        (明细表, 任务标题)；任何一步失败都返回 None。
    """
    title = client.get_task_title(grading_task_id)
    if not title:
        logger.warning(f'无法获取任务 {grading_task_id} 的标题')
        return None

    records = client.get_task_detail(grading_task_id)
    if records is None:
        logger.error(f'获取任务 {grading_task_id} 的测试结果失败')
        return None

    df = build_result_frame(records, display_map)
    if df is None or df.empty:
        logger.warning(f'任务 {grading_task_id} 没有数据')
        return None

    return prefix_columns(df, prefix), title


def _specialty_name(title: str) -> str:
    """从任务标题里截出专项名（去掉前两段和最后一段）。"""
    parts = title.split('_')
    return '_'.join(parts[2:-1]) if len(parts) > 3 else title


def run_project(
    client: PGSimClient,
    project: ProjectConfig,
    output_dir: Path,
    run_date: date,
) -> Path | None:
    """执行单个项目的对比并导出 Excel。

    Returns:
        输出文件路径；无数据或失败时返回 None。
    """
    if not project.base_sim_task_ids or not project.test_sim_task_ids:
        logger.warning(f'项目 {project.label} task IDs 为空，跳过')
        return None

    logger.info(f'========== 处理项目: {project.label} ==========')
    pairs = match_task_pairs(
        client.list_tasks(), project.base_sim_task_ids, project.test_sim_task_ids
    )
    if not pairs:
        logger.warning(f'项目 {project.label} 未找到匹配的任务对，跳过')
        return None

    display_map = client.get_metric_display_map()
    sheets: list[Sheet] = []
    summary_rows: list[dict] = []

    for base_task_id, test_task_id in pairs.items():
        logger.info(f'处理任务对: base={base_task_id}, test={test_task_id}')

        base = _load_version(client, base_task_id, 'base', display_map)
        if base is None:
            logger.warning(
                f'跳过任务对 {base_task_id}-{test_task_id}：base 版本数据获取失败'
            )
            continue

        test = _load_version(client, test_task_id, 'test', display_map)
        if test is None:
            logger.warning(
                f'跳过任务对 {base_task_id}-{test_task_id}：test 版本数据获取失败'
            )
            continue

        base_df, base_title = base
        test_df, test_title = test
        logger.info(f'对比: {base_title} vs {test_title}')

        merged = compare_frames(base_df, test_df)
        if merged.empty:
            logger.warning(f'任务对 {base_task_id}-{test_task_id} 没有共同的场景')
            continue

        sheets.append(Sheet(name=test_title, frame=merged))
        summary_rows.append(
            {
                SPECIALTY_COLUMN: _specialty_name(test_title),
                **calculate_statistics(merged),
            }
        )

    if summary_rows:
        sheets.append(
            Sheet(name=SUMMARY_SHEET_NAME, frame=pd.DataFrame(summary_rows))
        )
    if not sheets:
        logger.warning(f'项目 {project.label} 没有数据可导出')
        return None

    sheets = group_detail_sheets(sheets, project)
    if project.detail_sheet_groups:
        logger.info(
            f'项目 {project.label} 已按 detail_sheet_groups 合并明细，'
            f'共 {len(sheets)} 个工作表（含汇总）'
        )

    output_path = output_dir / f'{project.output_prefix}_{run_date:%Y%m%d}.xlsx'
    try:
        write_workbook(sheets, output_path)
    except Exception as e:
        logger.error(f'项目 {project.label} 导出失败: {e}', exc_info=True)
        return None

    logger.info(f'项目 {project.label} Excel 已生成: {output_path}')
    return output_path


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description='仿真测试结果对比工具（支持上汽/广汽/北汽等）',
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        '-p',
        '--project',
        help='只运行指定项目，如 GAC(广汽)、SAIC_AS5PR(上汽)、BAIC(北汽)。'
        '不指定则运行配置里的 default_project',
    )
    target.add_argument(
        '-a',
        '--all',
        action='store_true',
        help='运行配置里的所有项目，分别输出到各自的 Excel 文件',
    )
    parser.add_argument(
        '-l', '--list-projects', action='store_true', help='列出可用项目并退出'
    )
    return parser


def _select_projects(
    args: argparse.Namespace, config: AppConfig
) -> list[ProjectConfig] | None:
    """按命令行参数决定要跑哪些项目；参数非法时返回 None。"""
    if args.all:
        return list(config.projects.values())

    key = args.project or config.default_project
    if not key:
        logger.error(
            f'未指定项目，且配置里没有 default_project。可选: {list(config.projects)}'
        )
        return None

    project = config.projects.get(key)
    if project is None:
        logger.error(f'未知项目: {key}，可选: {list(config.projects)}')
        return None
    return [project]


def main(argv: Sequence[str] | None = None) -> int:
    """程序入口，返回进程退出码。"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    args = build_parser().parse_args(argv)

    try:
        config = load_config()
    except ConfigError as e:
        logger.error(str(e))
        return 1

    if args.list_projects:
        print('可用项目:')
        for key, project in config.projects.items():
            default_mark = ' [默认]' if key == config.default_project else ''
            print(
                f'  {key}: {project.description} '
                f'(output_prefix={project.output_prefix}){default_mark}'
            )
        return 0

    projects = _select_projects(args, config)
    if projects is None:
        return 1

    client = PGSimClient(load_auth_token())
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_date = date.today()

    written = [
        path
        for path in (
            run_project(client, project, OUTPUT_DIR, run_date) for project in projects
        )
        if path is not None
    ]
    if not written:
        logger.warning('未成功生成任何文件')
        return 1

    logger.info(f'全部完成，共生成 {len(written)} 个文件: {[str(p) for p in written]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
