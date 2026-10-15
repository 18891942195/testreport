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

url = "https://pgsim-phigent.navinfo.com/apiv2/sim/unity/task/listTaskByPage"
data = {
    "createUserName": "cibuild",
    "pageNo": 1,
    "pageSize": 100,
    "runModuleList": [],
    "taskTemplateId": "1f17434d7b326fca9524a7c6",
    "testAlgorithm": "phisim:dev_saic-as5pr-x86_239_20261015_1230_x86"
}


def main(argv: list[str] | None = None) -> int:
    import requests
    taskids = []
    response = requests.post(url, json=data)
    tasks = response.json()['result']['data']
    for task in tasks:
        taskids.append(task["simTaskId"])
    print(taskids)
    return 0


if __name__ == '__main__':
    sys.exit(main())

