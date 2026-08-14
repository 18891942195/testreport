"""PGSim 平台接口客户端。

关于认证：平台对本工具用到的接口开放匿名访问，而携带**空** token
（``Bearer `` 后面没有内容）会被判定为认证失败并返回 HTTP 401。
所以这里只在拿到非空 token 时才设置 Authorization 头 —— 没有
input/auth.txt 时以匿名方式访问，反而是能正常工作的那条路径。
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = 'https://pgsim.phigent.net'
API_TASK_DETAIL = f'{BASE_URL}/apiv2/sim/grading/task/getTaskRunningDetail'
API_METRIC_LIST = f'{BASE_URL}/apiv2/metric/getMetricList'
API_TASK_INFO = f'{BASE_URL}/apiv2/sim/grading/task/getGradingTaskInfo'
API_TASK_LIST = f'{BASE_URL}/apiv2/sim/grading/task/listTaskByPage'
REPLAY_URL_PREFIX = f'{BASE_URL}/task/replay?gradingSubTaskId='

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
)

# (连接超时, 读取超时)。任务列表一次拉取数万条，读取超时要给足；
# 但必须有上限，否则接口无响应时会无限挂起。
DEFAULT_TIMEOUT: tuple[float, float] = (10.0, 300.0)

# 两个接口都一次拉满，避免分页
_TASK_LIST_PAGE_SIZE = 200000
_TASK_DETAIL_PAGE_SIZE = 10000


class PGSimClient:
    """PGSim 接口封装：统一处理请求头、超时与错误。

    全量任务列表和指标映射在实例内缓存，一次运行里只拉取一次
    （``--all`` 模式下多个项目共用同一份快照）。
    """

    def __init__(
        self,
        token: str = '',
        timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
    ) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': USER_AGENT})
        if token:
            self._session.headers['Authorization'] = token
        else:
            logger.info('未提供认证 token，以匿名方式访问 PGSim')
        self._metric_display_map: dict[str, str] | None = None
        self._tasks: list[dict] | None = None

    def _request(self, method: str, url: str, **kwargs: Any) -> dict | None:
        """发送请求并返回 JSON；失败时记录日志并返回 None。"""
        try:
            response = self._session.request(
                method, url, timeout=self._timeout, **kwargs
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f'请求失败 {url}: {e}')
            return None
        except ValueError as e:  # 响应不是合法 JSON
            logger.error(f'响应解析失败 {url}: {e}')
            return None

    def get_metric_display_map(self) -> dict[str, str]:
        """指标名(metricName) -> 展示名(metricDisplay) 的映射。"""
        if self._metric_display_map is None:
            result = self._request('GET', API_METRIC_LIST)
            metrics = (result or {}).get('result') or []
            self._metric_display_map = {
                item['metricName']: item['metricDisplay'] for item in metrics
            }
            if not self._metric_display_map:
                logger.warning('获取指标列表失败或为空，指标名将统一显示为「未知」')
        return self._metric_display_map

    def get_task_title(self, grading_task_id: int) -> str | None:
        """获取评测任务标题；失败返回 None。"""
        result = self._request(
            'GET', API_TASK_INFO, params={'gradingTaskId': grading_task_id}
        )
        if result and result.get('result'):
            return result['result'].get('gradingTaskName')
        return None

    def get_task_detail(self, grading_task_id: int) -> list[dict] | None:
        """获取评测任务下所有场景的运行明细。

        Returns:
            记录列表；请求失败返回 None，任务无数据返回空列表。
        """
        payload = {
            'pageSize': _TASK_DETAIL_PAGE_SIZE,
            'taskId': grading_task_id,
            'pageNo': 1,
            'withExtraInfo': False,
            'scenarioIds': [],
            'filterMetric': {},
            'dataCycleTags': [],
        }
        result = self._request('POST', API_TASK_DETAIL, json=payload)
        inner = (result or {}).get('result') or {}
        if 'data' not in inner:
            return None
        return inner['data'] or []

    def list_tasks(self) -> list[dict]:
        """拉取全部评测任务（用于按 taskName 给 base/test 配对）。"""
        if self._tasks is None:
            result = self._request(
                'POST',
                API_TASK_LIST,
                json={'pageSize': _TASK_LIST_PAGE_SIZE, 'pageNo': 1},
            )
            tasks = ((result or {}).get('result') or {}).get('data')
            if tasks is None:
                logger.error('获取任务列表失败')
                return []
            logger.info(f'获取到 {len(tasks)} 个任务')
            self._tasks = tasks
        return self._tasks
