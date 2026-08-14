"""仿真测试结果对比工具。

从 PGSim 平台拉取仿真评测结果，对比 base 与 test 两个版本的差异，输出 Excel 报表。

模块划分：
    api      —— PGSim 接口客户端
    config   —— input/sim_config.json 与 input/auth.txt 的读取与校验
    compare  —— 指标分类、明细表构建、版本对比、通过率统计、任务配对
    sheets   —— 明细 sheet 分组与 Excel 写出
    cli      —— 命令行参数与整体编排
"""

__all__ = ['api', 'cli', 'compare', 'config', 'sheets']
