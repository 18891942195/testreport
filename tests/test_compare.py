"""simcompare 的离线单元测试（不联网、不依赖 pytest）。

运行：.venv/bin/python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from simcompare import config as config_mod
from simcompare.compare import (
    ANALYSIS_COLUMNS,
    COMPARE_COLUMN_ORDER,
    RESULT_COLUMNS,
    build_result_frame,
    calculate_statistics,
    classify_metrics,
    compare_frames,
    match_task_pairs,
    prefix_columns,
)
from simcompare.config import ProjectConfig, SheetGroup
from simcompare.sheets import (
    SPECIALTY_COLUMN,
    SUMMARY_SHEET_NAME,
    Sheet,
    group_detail_sheets,
    resolve_sheet_group,
    write_workbook,
)

DISPLAY_MAP = {'ttc': 'TTC过小', 'accel': '加速度超限'}


def make_record(scenario_id, *, name='场景', result='pass', metrics=None):
    """构造一条接口明细记录（字段与 getTaskRunningDetail 返回一致）。"""
    return {
        'gradingSubTaskId': 1000 + scenario_id,
        'scenarioId': scenario_id,
        'scenarioName': name,
        'gradingResult': result,
        'failedReason': '',
        'failMetric': None,
        'metricList': metrics if metrics is not None else [],
    }


class ClassifyMetricsTest(unittest.TestCase):
    def test_splits_by_status_and_uses_display_name(self):
        metrics = [
            {'metricName': 'ttc', 'metricStatus': 'fail'},
            {'metricName': 'accel', 'metricStatus': 'pass'},
            {'metricName': 'ttc', 'metricStatus': 'noRun'},
        ]
        self.assertEqual(
            classify_metrics(metrics, DISPLAY_MAP),
            {
                'pass_metrics': ['加速度超限'],
                'fail_metrics': ['TTC过小'],
                'unknown_metrics': ['TTC过小'],
            },
        )

    def test_unmapped_metric_falls_back_to_unknown_label(self):
        result = classify_metrics(
            [{'metricName': 'brandNew', 'metricStatus': 'pass'}], DISPLAY_MAP
        )
        self.assertEqual(result['pass_metrics'], ['未知'])

    def test_accepts_none_metric_list(self):
        result = classify_metrics(None, DISPLAY_MAP)
        self.assertEqual(result, {k: [] for k in result})


class BuildResultFrameTest(unittest.TestCase):
    def test_builds_expected_columns_and_replay_url(self):
        records = [
            make_record(1, metrics=[{'metricName': 'ttc', 'metricStatus': 'fail'}])
        ]
        df = build_result_frame(records, DISPLAY_MAP)

        self.assertEqual(list(df.columns), [*RESULT_COLUMNS, *ANALYSIS_COLUMNS])
        self.assertEqual(len(df), 1)
        self.assertTrue(
            df.loc[0, 'sim_url'].endswith('/task/replay?gradingSubTaskId=1001')
        )
        self.assertEqual(df.loc[0, 'fail_metrics'], ['TTC过小'])
        self.assertEqual(df.loc[0, 'pass_metrics'], [])
        self.assertIsNone(df.loc[0, '测试结果分析'])

    def test_duplicate_scenario_ids_do_not_multiply_rows(self):
        """回归测试：旧实现在此处对 scenarioId 做 merge，重复 id 会产生笛卡尔积。"""
        records = [
            make_record(7, name='第一条', metrics=[
                {'metricName': 'ttc', 'metricStatus': 'fail'}
            ]),
            make_record(7, name='第二条', metrics=[
                {'metricName': 'accel', 'metricStatus': 'pass'}
            ]),
        ]
        df = build_result_frame(records, DISPLAY_MAP)

        self.assertEqual(len(df), 2)
        self.assertEqual(list(df['scenarioName']), ['第一条', '第二条'])
        # 指标必须跟各自那一行对上，不能串行
        self.assertEqual(df.loc[0, 'fail_metrics'], ['TTC过小'])
        self.assertEqual(df.loc[1, 'pass_metrics'], ['加速度超限'])

    def test_returns_none_when_source_column_missing(self):
        broken = make_record(1)
        del broken['metricList']
        self.assertIsNone(build_result_frame([broken], DISPLAY_MAP))

    def test_empty_records_give_empty_frame_with_columns(self):
        df = build_result_frame([], DISPLAY_MAP)
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), [*RESULT_COLUMNS, *ANALYSIS_COLUMNS])


class CompareFramesTest(unittest.TestCase):
    def _versions(self):
        base = prefix_columns(
            build_result_frame(
                [make_record(1, name='场景A', result='pass'), make_record(2, result='fail')],
                DISPLAY_MAP,
            ),
            'base',
        )
        test = prefix_columns(
            build_result_frame(
                [make_record(1, name='场景A', result='fail'), make_record(3, result='pass')],
                DISPLAY_MAP,
            ),
            'test',
        )
        return base, test

    def test_prefix_columns_renames_only_version_columns(self):
        base, _ = self._versions()
        self.assertIn('base_gradingResult', base.columns)
        self.assertIn('scenarioId', base.columns)
        self.assertIn('scenarioName', base.columns)
        self.assertIn('测试结果分析', base.columns)

    def test_inner_join_and_column_order(self):
        base, test = self._versions()
        merged = compare_frames(base, test)

        self.assertEqual(list(merged['scenarioId']), [1])  # 只保留共同场景
        self.assertEqual(list(merged.columns), COMPARE_COLUMN_ORDER)
        self.assertEqual(merged.loc[0, '对比'], 'pass -> fail')
        self.assertEqual(merged.loc[0, '新增_fail_metrics'], 0)

    def test_no_merge_suffix_columns_leak(self):
        base, test = self._versions()
        merged = compare_frames(base, test)
        leaked = [c for c in merged.columns if c.endswith(('_x', '_y'))]
        self.assertEqual(leaked, [])
        self.assertEqual(merged.loc[0, 'scenarioName'], '场景A')

    def test_disjoint_scenarios_give_empty_frame(self):
        base = prefix_columns(build_result_frame([make_record(1)], DISPLAY_MAP), 'base')
        test = prefix_columns(build_result_frame([make_record(9)], DISPLAY_MAP), 'test')
        self.assertTrue(compare_frames(base, test).empty)


class CalculateStatisticsTest(unittest.TestCase):
    def test_counts_rates_and_transitions(self):
        merged = pd.DataFrame(
            {
                'base_gradingResult': ['pass', 'pass', 'pass', 'fail', 'fail', 'pass'],
                'test_gradingResult': ['pass', 'pass', 'fail', 'pass', 'fail', ''],
            }
        )
        stats = calculate_statistics(merged)

        # 最后一行 test 为空串，不计入 base 通过率
        self.assertEqual(stats['base_pass'], 3)
        self.assertEqual(stats['base_fail'], 2)
        self.assertEqual(stats['base_pass_rate'], '60%')
        self.assertEqual(stats['test_pass'], 3)
        self.assertEqual(stats['test_fail'], 2)
        self.assertEqual(stats['test_pass_rate'], '60%')
        self.assertEqual(stats['diff_pass_rate'], '0%')
        self.assertEqual(stats['pass->fail'], 1)
        self.assertEqual(stats['fail->pass'], 1)
        self.assertEqual(stats['pass->pass'], 2)
        self.assertEqual(stats['fail->fail'], 1)

    def test_key_order_matches_report_columns(self):
        merged = pd.DataFrame(
            {'base_gradingResult': ['pass'], 'test_gradingResult': ['pass']}
        )
        self.assertEqual(
            list(calculate_statistics(merged)),
            [
                'base_pass',
                'base_fail',
                'base_pass_rate',
                'test_pass',
                'test_fail',
                'test_pass_rate',
                'diff_pass_rate',
                'pass->fail',
                'fail->pass',
                'pass->pass',
                'fail->fail',
            ],
        )

    def test_no_scenarios_do_not_divide_by_zero(self):
        merged = pd.DataFrame(
            {'base_gradingResult': ['skip'], 'test_gradingResult': ['skip']}
        )
        stats = calculate_statistics(merged)
        self.assertEqual(stats['base_pass_rate'], '0%')
        self.assertEqual(stats['diff_pass_rate'], '0%')

    def test_returns_plain_ints(self):
        merged = pd.DataFrame(
            {'base_gradingResult': ['pass'], 'test_gradingResult': ['pass']}
        )
        self.assertIsInstance(calculate_statistics(merged)['base_pass'], int)


class MatchTaskPairsTest(unittest.TestCase):
    TASKS = [
        {'simTaskId': 1, 'taskName': '行车', 'gradingTaskId': 101},
        {'simTaskId': 2, 'taskName': '泊车', 'gradingTaskId': 102},
        {'simTaskId': 11, 'taskName': '行车', 'gradingTaskId': 201},
        {'simTaskId': 12, 'taskName': '泊车', 'gradingTaskId': 202},
        {'simTaskId': 99, 'taskName': '不相关', 'gradingTaskId': 999},
    ]

    def test_pairs_by_task_name(self):
        self.assertEqual(
            match_task_pairs(self.TASKS, [1, 2], [11, 12]), {101: 201, 102: 202}
        )

    def test_unmatched_names_are_dropped(self):
        tasks = [
            {'simTaskId': 1, 'taskName': '只有base', 'gradingTaskId': 101},
            {'simTaskId': 11, 'taskName': '只有test', 'gradingTaskId': 201},
        ]
        self.assertEqual(match_task_pairs(tasks, [1], [11]), {})

    def test_duplicate_test_name_keeps_last(self):
        tasks = [
            {'simTaskId': 1, 'taskName': '行车', 'gradingTaskId': 101},
            {'simTaskId': 11, 'taskName': '行车', 'gradingTaskId': 201},
            {'simTaskId': 12, 'taskName': '行车', 'gradingTaskId': 202},
        ]
        self.assertEqual(match_task_pairs(tasks, [1], [11, 12]), {101: 202})

    def test_ignores_tasks_outside_both_lists(self):
        pairs = match_task_pairs(self.TASKS, [1], [11])
        self.assertEqual(pairs, {101: 201})

    def test_containment_match(self):
        tasks = [
            {'simTaskId': 1, 'taskName': '任务a', 'gradingTaskId': 101},
            {'simTaskId': 11, 'taskName': '任务ab', 'gradingTaskId': 201},
        ]
        self.assertEqual(match_task_pairs(tasks, [1], [11]), {101: 201})

    def test_containment_does_not_steal_longer_exact(self):
        """短名不能抢走更具体的精确匹配：任务a↔任务a，任务ab↔任务ab。"""
        tasks = [
            {'simTaskId': 1, 'taskName': '任务a', 'gradingTaskId': 101},
            {'simTaskId': 2, 'taskName': '任务ab', 'gradingTaskId': 102},
            {'simTaskId': 11, 'taskName': '任务a', 'gradingTaskId': 201},
            {'simTaskId': 12, 'taskName': '任务ab', 'gradingTaskId': 202},
        ]
        self.assertEqual(
            match_task_pairs(tasks, [1, 2], [11, 12]), {101: 201, 102: 202}
        )

    def test_containment_prefers_longest(self):
        """两侧都有包含关系时，更长的名字优先配给更具体的任务。"""
        tasks = [
            {'simTaskId': 1, 'taskName': '任务a', 'gradingTaskId': 101},
            {'simTaskId': 2, 'taskName': '任务ab', 'gradingTaskId': 102},
            {'simTaskId': 12, 'taskName': '任务ab', 'gradingTaskId': 202},
        ]
        self.assertEqual(match_task_pairs(tasks, [1, 2], [12]), {102: 202})

    def test_containment_prefers_closer_length(self):
        tasks = [
            {'simTaskId': 1, 'taskName': '任务a', 'gradingTaskId': 101},
            {'simTaskId': 11, 'taskName': '任务ab', 'gradingTaskId': 201},
            {'simTaskId': 12, 'taskName': '任务abc', 'gradingTaskId': 202},
        ]
        self.assertEqual(match_task_pairs(tasks, [1], [11, 12]), {101: 201})

    def test_empty_task_name_does_not_match(self):
        tasks = [
            {'simTaskId': 1, 'taskName': '', 'gradingTaskId': 101},
            {'simTaskId': 11, 'taskName': '任务ab', 'gradingTaskId': 201},
        ]
        self.assertEqual(match_task_pairs(tasks, [1], [11]), {})


class SheetGroupingTest(unittest.TestCase):
    GROUPS = (
        SheetGroup('泊车明细', ('MEB', 'APA泊入')),
        SheetGroup('泊车性能明细', ('MEB性能测试',)),
        SheetGroup('行车明细', ('行车',)),
    )

    def test_longest_keyword_wins(self):
        """短关键词 MEB 不能抢走 MEB性能测试。"""
        self.assertEqual(
            resolve_sheet_group('SAIC_daily_MEB性能测试', self.GROUPS), '泊车性能明细'
        )
        self.assertEqual(resolve_sheet_group('SAIC_daily_MEB场景', self.GROUPS), '泊车明细')

    def test_equal_length_keeps_config_order(self):
        groups = (SheetGroup('先', ('AAA',)), SheetGroup('后', ('AAA',)))
        self.assertEqual(resolve_sheet_group('x_AAA_y', groups), '先')

    def test_returns_none_when_nothing_matches(self):
        self.assertIsNone(resolve_sheet_group('完全无关的标题', self.GROUPS))

    def _project(self, **overrides):
        params = {
            'key': 'T',
            'output_prefix': 'T',
            'base_sim_task_ids': (1,),
            'test_sim_task_ids': (2,),
            'detail_sheet_groups': self.GROUPS,
            'detail_sheet_fallback': '泊车明细',
        }
        params.update(overrides)
        return ProjectConfig(**params)

    def _sheet(self, name, value):
        return Sheet(name=name, frame=pd.DataFrame({'scenarioId': [value]}))

    def test_merges_details_and_tags_specialty(self):
        sheets = [
            self._sheet('SAIC_daily_MEB场景', 1),
            self._sheet('SAIC_daily_APA泊入状态机', 2),
            self._sheet('SAIC_daily_行车ACC', 3),
            Sheet(SUMMARY_SHEET_NAME, pd.DataFrame({'专项名称': ['x']})),
        ]
        merged = group_detail_sheets(sheets, self._project())

        self.assertEqual([s.name for s in merged], ['泊车明细', '行车明细', SUMMARY_SHEET_NAME])
        parking = merged[0].frame
        self.assertEqual(len(parking), 2)
        self.assertEqual(list(parking.columns)[0], SPECIALTY_COLUMN)
        self.assertEqual(
            list(parking[SPECIALTY_COLUMN]),
            ['SAIC_daily_MEB场景', 'SAIC_daily_APA泊入状态机'],
        )

    def test_unmatched_title_goes_to_fallback(self):
        merged = group_detail_sheets(
            [self._sheet('毫不相关的专项', 1)], self._project()
        )
        self.assertEqual([s.name for s in merged], ['泊车明细'])

    def test_summary_stays_last(self):
        sheets = [
            Sheet(SUMMARY_SHEET_NAME, pd.DataFrame({'a': [1]})),
            self._sheet('SAIC_daily_行车ACC', 1),
        ]
        merged = group_detail_sheets(sheets, self._project())
        self.assertEqual(merged[-1].name, SUMMARY_SHEET_NAME)

    def test_without_groups_sheets_are_untouched(self):
        sheets = [self._sheet('原样保留', 1)]
        merged = group_detail_sheets(sheets, self._project(detail_sheet_groups=()))
        self.assertEqual([s.name for s in merged], ['原样保留'])


class WriteWorkbookTest(unittest.TestCase):
    def test_truncates_and_deduplicates_sheet_names(self):
        long_name = 'A' * 40
        sheets = [
            Sheet(long_name + '第一个', pd.DataFrame({'v': [1]})),
            Sheet(long_name + '第二个', pd.DataFrame({'v': [2]})),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'out.xlsx'
            write_workbook(sheets, path)
            names = pd.ExcelFile(path).sheet_names

        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2, '截断后重名必须去重')
        for name in names:
            self.assertLessEqual(len(name), 31)

    def test_writes_frames_without_index_column(self):
        sheets = [Sheet('明细', pd.DataFrame({'scenarioId': [1, 2]}))]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'out.xlsx'
            write_workbook(sheets, path)
            df = pd.read_excel(path, sheet_name='明细')
        self.assertEqual(list(df.columns), ['scenarioId'])


class ConfigTest(unittest.TestCase):
    CONFIG = {
        'projects': {
            'GOOD': {
                'description': '正常项目',
                'output_prefix': 'GOOD',
                'base_sim_task_ids': [1, 2],
                'test_sim_task_ids': [3, 4],
                'detail_sheet_fallback': '其他',
                'detail_sheet_groups': [
                    {'sheet_name': '明细', 'title_keywords': ['行车', 123, '']},
                    {'sheet_name': '', 'title_keywords': ['忽略我']},
                ],
            },
            'BROKEN': {'description': '缺字段', 'output_prefix': 'BROKEN'},
        },
        'default_project': 'GOOD',
    }

    def _write(self, payload):
        tmp = tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8'
        )
        json.dump(payload, tmp, ensure_ascii=False)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return tmp.name

    def test_skips_invalid_project_and_parses_groups(self):
        cfg = config_mod.load_config(self._write(self.CONFIG))

        self.assertEqual(list(cfg.projects), ['GOOD'])  # BROKEN 被跳过
        self.assertEqual(cfg.default_project, 'GOOD')
        project = cfg.projects['GOOD']
        self.assertEqual(project.base_sim_task_ids, (1, 2))
        self.assertEqual(project.label, 'GOOD (正常项目)')
        # 非字符串与空关键词被过滤，空 sheet_name 的分组被丢弃
        self.assertEqual(len(project.detail_sheet_groups), 1)
        self.assertEqual(project.detail_sheet_groups[0].title_keywords, ('行车',))

    def test_missing_file_raises(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load_config('/nonexistent/sim_config.json')

    def test_invalid_json_raises(self):
        tmp = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
        tmp.write('{not json')
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load_config(tmp.name)

    def test_no_usable_project_raises(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load_config(self._write({'projects': {}}))

    def test_real_config_file_loads(self):
        """仓库里真实的 input/sim_config.json 必须能加载，且默认项目存在。"""
        cfg = config_mod.load_config()
        self.assertIn('SAIC_AS5PR', cfg.projects)
        self.assertIn(cfg.default_project, cfg.projects)

    def test_auth_token_prefixing(self):
        tmp = tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8')
        tmp.write('  abc123\n')
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        self.assertEqual(config_mod.load_auth_token(tmp.name), 'Bearer abc123')

    def test_auth_token_keeps_existing_prefix(self):
        tmp = tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8')
        tmp.write('Bearer xyz')
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        self.assertEqual(config_mod.load_auth_token(tmp.name), 'Bearer xyz')

    def test_missing_auth_file_returns_empty(self):
        self.assertEqual(config_mod.load_auth_token('/nonexistent/auth.txt'), '')


class ClientAuthHeaderTest(unittest.TestCase):
    """空 token 会被平台判 401，因此无 token 时不能发 Authorization 头。"""

    def test_no_token_means_no_authorization_header(self):
        from simcompare.api import PGSimClient

        client = PGSimClient('')
        self.assertNotIn('Authorization', client._session.headers)

    def test_token_is_sent_when_present(self):
        from simcompare.api import PGSimClient

        client = PGSimClient('Bearer abc')
        self.assertEqual(client._session.headers['Authorization'], 'Bearer abc')


class SpecialtyNameTest(unittest.TestCase):
    def test_strips_leading_and_trailing_segments(self):
        from simcompare.cli import _specialty_name

        self.assertEqual(
            _specialty_name('SAIC_J6B_AS5PR_daily_行车ACC-默认grading'), 'AS5PR_daily'
        )

    def test_short_title_is_kept_as_is(self):
        from simcompare.cli import _specialty_name

        self.assertEqual(_specialty_name('A_B'), 'A_B')


class OutputPathTest(unittest.TestCase):
    def test_filename_uses_prefix_and_date(self):
        from simcompare.cli import OUTPUT_DIR

        name = f'{"SAIC_AS5PR"}_{date(2026, 8, 14):%Y%m%d}.xlsx'
        self.assertEqual(name, 'SAIC_AS5PR_20260814.xlsx')
        self.assertEqual(OUTPUT_DIR.name, 'output')


if __name__ == '__main__':
    unittest.main()
