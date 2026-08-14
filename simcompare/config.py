"""配置读取与校验：input/sim_config.json 与 input/auth.txt。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_ROOT / 'input'
DEFAULT_CONFIG_PATH = INPUT_DIR / 'sim_config.json'
DEFAULT_AUTH_PATH = INPUT_DIR / 'auth.txt'

# 项目配置里必须存在的字段
_REQUIRED_PROJECT_KEYS = ('base_sim_task_ids', 'test_sim_task_ids', 'output_prefix')


class ConfigError(Exception):
    """配置文件缺失或格式非法。"""


@dataclass(frozen=True)
class SheetGroup:
    """一条「把哪些专项明细合并到同一个 sheet」的规则。"""

    sheet_name: str
    title_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectConfig:
    """单个项目（上汽/广汽/北汽…）的对比配置。"""

    key: str
    output_prefix: str
    base_sim_task_ids: tuple[int, ...]
    test_sim_task_ids: tuple[int, ...]
    description: str = ''
    detail_sheet_groups: tuple[SheetGroup, ...] = ()
    detail_sheet_fallback: str = ''

    @property
    def label(self) -> str:
        """日志里用的「KEY (描述)」。"""
        return f'{self.key} ({self.description})' if self.description else self.key


@dataclass(frozen=True)
class AppConfig:
    """整个配置文件。"""

    projects: dict[str, ProjectConfig]
    default_project: str | None = None


def _parse_sheet_groups(raw: object, project_key: str) -> tuple[SheetGroup, ...]:
    """解析 detail_sheet_groups；非法项跳过而不是整体报错。"""
    if not isinstance(raw, list):
        if raw:
            logger.warning(f'项目 {project_key} 的 detail_sheet_groups 不是列表，已忽略')
        return ()

    groups: list[SheetGroup] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sheet_name = str(item.get('sheet_name', '')).strip()
        if not sheet_name:
            continue
        keywords = tuple(
            kw
            for kw in (item.get('title_keywords') or ())
            if isinstance(kw, str) and kw
        )
        groups.append(SheetGroup(sheet_name=sheet_name, title_keywords=keywords))
    return tuple(groups)


def _parse_project(key: str, raw: object) -> ProjectConfig | None:
    """校验并转换单个项目配置；缺字段时告警并返回 None（调用方跳过该项目）。"""
    if not isinstance(raw, dict):
        logger.warning(f'项目 {key} 的配置不是对象，跳过')
        return None

    missing = [k for k in _REQUIRED_PROJECT_KEYS if k not in raw]
    if missing:
        logger.warning(f'项目 {key} 配置缺少字段 {missing}，跳过')
        return None

    return ProjectConfig(
        key=key,
        output_prefix=str(raw['output_prefix']),
        base_sim_task_ids=tuple(raw['base_sim_task_ids'] or ()),
        test_sim_task_ids=tuple(raw['test_sim_task_ids'] or ()),
        description=str(raw.get('description', '')),
        detail_sheet_groups=_parse_sheet_groups(raw.get('detail_sheet_groups'), key),
        detail_sheet_fallback=str(raw.get('detail_sheet_fallback', '')).strip(),
    )


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    """读取并校验配置文件。

    Raises:
        ConfigError: 文件不存在、JSON 非法，或没有任何可用项目。
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as e:
        raise ConfigError(f'配置文件不存在: {path}') from e
    except json.JSONDecodeError as e:
        raise ConfigError(f'配置文件不是合法 JSON: {path}: {e}') from e
    except OSError as e:
        raise ConfigError(f'读取配置文件失败: {path}: {e}') from e

    if not isinstance(raw, dict):
        raise ConfigError(f'配置文件顶层应为对象: {path}')

    projects: dict[str, ProjectConfig] = {}
    for key, value in (raw.get('projects') or {}).items():
        project = _parse_project(key, value)
        if project is not None:
            projects[key] = project

    if not projects:
        raise ConfigError(f'配置中没有可用项目: {path}')

    default_project = raw.get('default_project') or None
    if default_project and default_project not in projects:
        logger.warning(
            f'default_project「{default_project}」不在 projects 中，'
            f'可选: {list(projects)}'
        )

    return AppConfig(projects=projects, default_project=default_project)


def load_auth_token(path: Path | str = DEFAULT_AUTH_PATH) -> str:
    """读取 Bearer token；文件不存在或内容为空时返回空字符串（即匿名访问）。"""
    try:
        token = Path(path).read_text(encoding='utf-8').strip()
    except OSError:
        return ''
    if not token:
        return ''
    return token if token.startswith('Bearer ') else f'Bearer {token}'
