"""Resolve omitted return seconds using the stage of the original tail."""
from ..exception import GroupError, InputError


def effective_return_seconds(battle, boss_cycle, game_server, seconds):
    if seconds is not None:
        return seconds
    # Stage indices start at zero: B, C, D, ...
    return 90 if battle._level_by_cycle(boss_cycle, game_server) in (0, 1) else None


def pending_compensations(battle, records, game_server):
    """Replay source links; legacy compensation records consume the oldest tail."""
    pending = {}
    for record in sorted(records, key=lambda record: record.cid):
        key = (record.gid, record.bid, record.qqid, record.challenge_pcrdate)
        queue = pending.setdefault(key, [])
        if record.is_continue:
            source = record.consumed_tail_id
            if source is None:
                if queue:
                    queue.pop(0)
            else:
                queue[:] = [tail for tail in queue if tail.cid != source]
        elif record.boss_health_remain == 0:
            queue.append(record)
    return sorted((tail for queue in pending.values() for tail in queue), key=lambda tail: tail.cid)


def select_compensation(battle, records, game_server, seconds=None, source_id=None):
    if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int) or not 21 <= seconds <= 90):
        raise InputError('补偿秒数必须是21至90的整数秒数')
    pending = pending_compensations(battle, records, game_server)
    if source_id is not None:
        selected = next((tail for tail in pending if tail.cid == source_id), None)
        if selected is None:
            raise GroupError('申请选定的补偿已使用或已撤销，请取消申请后重新选择')
        return selected
    if seconds is not None:
        selected = next((tail for tail in pending if effective_return_seconds(
            battle, tail.boss_cycle, game_server, tail.return_seconds) == seconds), None)
        if selected is None:
            raise GroupError(f'没有可用的{seconds}s补偿刀，请检查尾刀一览')
        return selected
    return pending[0] if pending else None
