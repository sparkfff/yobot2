"""Rules and transaction boundary shared by clan battle commands.

QQ and web adapters keep their existing interfaces. Database writes happen
inside one transaction; web updates and notifications run only after commit.
"""

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from inspect import signature

from peewee import SqliteDatabase

from ...ybdata import Clan_group
from ..exception import GroupNotExist, InputError

_logger = logging.getLogger(__name__)
_current_mutation = ContextVar("clan_battle_mutation", default=None)


@dataclass(frozen=True)
class BladeCounts:
    finished: int
    full_used: int
    compensation: int

    def choose_compensation(self, requested):
        return bool(requested or (
            self.full_used >= 3 and self.compensation > 0
        ))


def count_blades(records):
    """Keep the existing full-blade and compensation accounting in one place."""
    records = list(records)
    finished = sum(bool(c.boss_health_remain or c.is_continue) for c in records)
    used_compensation = sum(bool(c.is_continue) for c in records)
    tails = sum(c.boss_health_remain == 0 and not c.is_continue for c in records)
    return BladeCounts(finished, finished + tails - used_compensation,
                       len(records) - finished - used_compensation)


def validate_boss_number(value):
    if isinstance(value, bool) or str(value) not in {"1", "2", "3", "4", "5"}:
        raise InputError("Boss编号必须是1至5")
    return str(value)


def validate_battle_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError("档案编号必须是非负整数")
    return value


def after_commit(callback):
    mutation = _current_mutation.get()
    if mutation is None:
        callback()
    else:
        mutation[2].append(callback)


def atomic_battle_operation(fn):
    """Reload authoritative state, roll back writes, then publish side effects.

    SQLite's immediate transaction serializes read/modify/write operations
    across processes. Nested operations for the same clan share the boundary.
    The legacy instance cache is refreshed after rollback as well as commit.
    """
    parameters = signature(fn)

    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        group_id = parameters.bind(self, *args, **kwargs).arguments['group_id']
        current = _current_mutation.get()
        if current is not None:
            if current[:2] != (self, group_id):
                raise RuntimeError("A battle operation cannot modify another clan")
            return fn(self, *args, **kwargs)

        database = Clan_group._meta.database
        if database.in_transaction():
            raise RuntimeError('Battle services must own the outer transaction')
        effects = []
        token = _current_mutation.set((self, group_id, effects))
        transaction = (database.atomic("IMMEDIATE")
                       if isinstance(database, SqliteDatabase) else database.atomic())
        try:
            with transaction:
                group = Clan_group.get_or_none(group_id=group_id)
                if group is None or group.deleted:
                    raise GroupNotExist()
                original_data = group.__data__.copy()
                cached = self.group_data_list.get(group_id)
                if cached is not None:
                    # Web adapters may hold this same instance while invoking
                    # the service. Preserve its identity so their response is
                    # based on the committed state rather than an old object.
                    cached.__data__ = original_data.copy()
                    cached._dirty.clear()
                    cached.__rel__.clear()
                    group = cached
                self.group_data_list[group_id] = group
                result = fn(self, *args, **kwargs)
        except BaseException:
            # Peewee rolls back the database, but not a cached model instance.
            self.group_data_list.pop(group_id, None)
            if 'original_data' in locals():
                group.__data__ = original_data
                group._dirty.clear()
                group.__rel__.clear()
            raise
        finally:
            _current_mutation.reset(token)

        for effect in effects:
            try:
                effect()
            except Exception:
                # A notification failure must not turn a committed report into
                # an apparent failure that prompts the user to report again.
                _logger.exception("会战数据已保存，但发送更新失败，群%s", group_id)
        return result
    return wrapped
