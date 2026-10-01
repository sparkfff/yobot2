"""Persisted report snapshots and conflict-aware inverse state changes."""
import copy
import json
from dataclasses import dataclass

from ..exception import GroupError

_MISSING = object()
STATE_FIELDS = (
    'boss_cycle', 'now_cycle_boss_health', 'next_cycle_boss_health',
    'challenging_member_list', 'subscribe_list', 'challenging_start_time',
)
JSON_FIELDS = {
    'now_cycle_boss_health', 'next_cycle_boss_health',
    'challenging_member_list', 'subscribe_list',
}


class UndoConflict(GroupError):
    """A later operation changed state that undo would otherwise overwrite."""


@dataclass(frozen=True)
class BattleSnapshot:
    group_id: int
    battle_id: int
    game_server: str
    state: dict

    @classmethod
    def capture(cls, group):
        state = {}
        for field in STATE_FIELDS:
            value = getattr(group, field)
            state[field] = (json.loads(value) or {}) if field in JSON_FIELDS and value else (
                {} if field in JSON_FIELDS else value)
        return cls(group.group_id, group.battle_id, group.game_server, state)

    def dumps(self):
        return json.dumps({'version': 1, 'group_id': self.group_id,
                           'battle_id': self.battle_id, 'game_server': self.game_server,
                           'state': self.state}, ensure_ascii=False, sort_keys=True)

    @classmethod
    def loads(cls, text):
        try:
            data = json.loads(text)
            if data['version'] != 1 or set(data['state']) != set(STATE_FIELDS):
                raise ValueError('Unsupported report snapshot')
            if any(not isinstance(data['state'][field], dict) for field in JSON_FIELDS):
                raise ValueError('Invalid report snapshot')
            return cls(data['group_id'], data['battle_id'], data['game_server'], data['state'])
        except (KeyError, TypeError, ValueError) as error:
            raise GroupError('撤销快照无效，请联系管理员检查数据') from error

    def restore(self, group):
        for field, value in self.state.items():
            if field in JSON_FIELDS:
                value = json.dumps(value, ensure_ascii=False)
                if field == 'challenging_member_list' and not self.state[field]:
                    value = None
            setattr(group, field, value)


def _inverse(before, after, current, path):
    if before == after:
        return copy.deepcopy(current) if current is not _MISSING else _MISSING
    if (isinstance(before, dict) or before is _MISSING) and (
            isinstance(after, dict) or after is _MISSING):
        if current is not _MISSING and not isinstance(current, dict):
            raise UndoConflict('报刀后的状态已被修改，无法安全撤销：' + path)
        result = copy.deepcopy(current) if current is not _MISSING else {}
        before_map = before if isinstance(before, dict) else {}
        after_map = after if isinstance(after, dict) else {}
        for key in before_map.keys() | after_map.keys():
            value = _inverse(before_map.get(key, _MISSING),
                             after_map.get(key, _MISSING), result.get(key, _MISSING),
                             path + '/' + str(key))
            if value is _MISSING:
                result.pop(key, None)
            else:
                result[key] = value
        # Empty legacy containers carry no semantic membership or reservation.
        return result if result else _MISSING
    if current != after:
        raise UndoConflict('报刀后的状态已被修改，无法安全撤销：' + path)
    return copy.deepcopy(before) if before is not _MISSING else _MISSING


def reverse_report(group, before, after):
    """Undo only the report's changes, preserving unrelated later operations.

    New reservations or applications belonging to other members survive.
    Conflicting edits to affected health, application or reservation fields
    reject the entire undo rather than silently overwriting newer state.
    """
    current = BattleSnapshot.capture(group)
    identity = (current.group_id, current.battle_id, current.game_server)
    if identity != (before.group_id, before.battle_id, before.game_server) or identity != (
            after.group_id, after.battle_id, after.game_server):
        raise UndoConflict('会战档案或服务器已改变，无法安全撤销')
    if current.state['boss_cycle'] != after.state['boss_cycle']:
        raise UndoConflict('会战周目已被修改，无法安全撤销')
    if before.state['boss_cycle'] != after.state['boss_cycle'] and any(
            current.state[field] != after.state[field] for field in (
                'now_cycle_boss_health', 'next_cycle_boss_health')):
        raise UndoConflict('切换周目后的血量已被修改，无法安全撤销')
    state = {}
    for field in STATE_FIELDS:
        value = _inverse(before.state[field], after.state[field], current.state[field], field)
        state[field] = {} if value is _MISSING else value
    members = set()
    for applications in state['challenging_member_list'].values():
        for member in applications:
            if member in members:
                raise UndoConflict('成员已重新申请其他Boss，无法安全恢复原申请：' + member)
            members.add(member)
    for boss, applications in current.state['challenging_member_list'].items():
        old_target = current.state['boss_cycle'] + (
            current.state['now_cycle_boss_health'][boss] == 0)
        restored_target = state['boss_cycle'] + (state['now_cycle_boss_health'][boss] == 0)
        if applications and old_target != restored_target:
            raise UndoConflict('撤销会改变后续申请的Boss周目，无法安全恢复：' + boss)
    return BattleSnapshot(current.group_id, current.battle_id, current.game_server, state)
