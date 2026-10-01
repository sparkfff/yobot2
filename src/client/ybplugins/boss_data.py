"""Validate published clan battle data before changing local configuration."""
import datetime
from dataclasses import dataclass

import aiohttp


SOURCE_URL = 'https://pcr.satroki.tech/api/Quest/GetClanBattleInfos'


class BossDataError(ValueError):
    pass


@dataclass
class BossData:
    year: int
    month: int
    health: list
    ids: list
    cycles: list
    names: dict


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise BossDataError('数据中存在无效整数')
    try:
        return int(value)
    except ValueError as exc:
        raise BossDataError('数据中存在无效整数') from exc


def parse_boss_data(infos, today=None, use_latest=False):
    today = today or datetime.date.today()
    if not isinstance(infos, list) or not infos:
        raise BossDataError('数据源未返回会战列表')
    available = []
    try:
        for info in infos:
            year, month = integer(info['year']), integer(info['month'])
            datetime.date(year, month, 1)
            if (year, month) <= (today.year, today.month):
                available.append(((year, month), info))
        if not available:
            raise BossDataError('数据源没有已发布的会战数据')
        period, info = max(available, key=lambda item: item[0])
        if period != (today.year, today.month) and not use_latest:
            raise BossDataError(
                f'{today.year}-{today.month:02d} 数据尚未发布；'
                f'最近可用的是 {period[0]}-{period[1]:02d}。'
                '如需往期数据，请勾选“允许使用最近一期”。')
        phases = sorted((phase for phase in info['phases']
                         if integer(phase['lapFrom']) > 0),
                        key=lambda phase: integer(phase['lapFrom']))
        if not phases or integer(phases[0]['lapFrom']) != 1:
            raise BossDataError('缺少从第 1 周开始的正式阶段')
        health, ids, cycles, names = [], [], [], {}
        for index, phase in enumerate(phases):
            start = integer(phase['lapFrom'])
            end = (integer(phases[index + 1]['lapFrom']) - 1
                   if index + 1 < len(phases) else 999)
            allowed_ends = (end, -1) if index == len(phases) - 1 else (end,)
            if start > end or integer(phase['lapTo']) not in allowed_ends:
                raise BossDataError('正式阶段周目范围不连续或超出上限')
            bosses = phase['bosses']
            if len(bosses) != 5:
                raise BossDataError('每个阶段必须包含 5 个 Boss')
            row = []
            for number, boss in enumerate(bosses, 1):
                hp, unit_id = integer(boss['hp']), integer(boss['unitId'])
                name = boss['name']
                if hp <= 0 or unit_id <= 0 or not isinstance(name, str) or not name.strip():
                    raise BossDataError('Boss 血量、编号或名称无效')
                row.append(hp)
                names.setdefault(str(number), {})[str(unit_id)] = name
                if index == 0:
                    ids.append(str(unit_id))
            health.append(row)
            cycles.append([start, end])
        return BossData(*period, health, ids, cycles, names)
    except (KeyError, TypeError, OverflowError, ValueError) as exc:
        if isinstance(exc, BossDataError):
            raise
        raise BossDataError('数据源格式不正确') from exc


async def fetch_boss_data(session, server, use_latest=False):
    async with session.get(SOURCE_URL, params={'s': server}) as response:
        response.raise_for_status()
        infos = await response.json()
    return parse_boss_data(infos, use_latest=use_latest)


def create_session():
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
