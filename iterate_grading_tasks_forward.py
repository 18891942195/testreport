#!/usr/bin/env python3
"""生成一段连续的 simTaskId 列表，便于粘贴进 input/sim_config.json。

从 --start 开始**递减**取 --count 个 id，即 start, start-1, start-2, …
（与 sim_config.json 里 base_sim_task_ids / test_sim_task_ids 的排列顺序一致）。

注意这里生成的是 **simTaskId**（仿真任务 id，形如 195153），不是 gradingTaskId
（评测任务 id，形如 75048）—— 两者由平台一一对应，配置文件里填的是前者。

用法：python3 iterate_grading_tasks_forward.py --start 195154 --count 29
"""

import argparse
import sys


def build_task_ids(start: int, count: int) -> list[int]:
    """返回从 start 起递减的 count 个 id。"""
    return [start - offset for offset in range(count)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='从 --start 起递减生成 --count 个 simTaskId',
    )
    parser.add_argument('--start', type=int, required=True, help='起始 simTaskId')
    parser.add_argument(
        '--count',
        type=int,
        required=True,
        help='生成个数（依次为 start, start-1, …）',
    )
    args = parser.parse_args(argv)

    if args.count < 1:
        parser.error('--count 须 >= 1')

    print(build_task_ids(args.start, args.count))
    return 0


if __name__ == '__main__':
    sys.exit(main())


# python3 iterate_grading_tasks_forward.py --start 197376 --count 29
# python3 iterate_grading_tasks_forward.py --start 177069 --count 17

# 广丰 python3 iterate_grading_tasks_forward.py --start 193645 --count 12
