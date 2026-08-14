#!/usr/bin/env python3
"""仿真测试结果对比工具（入口）。

从 PGSim 平台获取仿真测试结果，对比 base 版本与 test 版本的差异，
输出包含明细与汇总统计的 Excel 文件到 output/。

具体实现见 simcompare/ 包：
    api      —— PGSim 接口客户端
    config   —— input/sim_config.json 与 input/auth.txt
    compare  —— 指标分类、明细表构建、版本对比、通过率统计、任务配对
    sheets   —— 明细 sheet 分组与 Excel 写出
    cli      —— 命令行参数与整体编排

用法：
    python get_simresult_compare_J6B.py                # 运行配置里的 default_project
    python get_simresult_compare_J6B.py -p GAC         # 只运行指定项目
    python get_simresult_compare_J6B.py --all          # 运行全部项目
    python get_simresult_compare_J6B.py -l             # 列出可用项目
"""

import sys

from simcompare.cli import main

if __name__ == '__main__':
    sys.exit(main())
