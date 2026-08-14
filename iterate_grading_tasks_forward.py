#!/usr/bin/env python3
"""
从 gradingTaskId = start 起连续递增查询 count 个任务（start, start+1, …）。
用法：python iterate_grading_tasks_forward.py --start 169480 --count 30

认证：input/auth.txt（与 get_simresult_compare_J6B.py 相同）
"""

import argparse
import logging

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 --start 起向前连续查询 --count 个 gradingTaskId",
    )
    parser.add_argument("--start", type=int, required=True, help="起始 gradingTaskId")
    parser.add_argument(
        "--count",
        type=int,
        required=True,
        help="查询个数（依次访问 start, start+1, …）",
    )
    args = parser.parse_args()

    if args.count < 1:
        logger.error("--count 须 >= 1")
        return 1

    ids = [args.start - i for i in range(args.count)]
    return ids
    

if __name__ == "__main__":
    print(main())


# python3 iterate_grading_tasks_forward.py --start 195154 --count 29
# python3 iterate_grading_tasks_forward.py --start 177069 --count 17

# 广丰 python3 iterate_grading_tasks_forward.py --start 193645 --count 12