"""Application services for reports, undo and battle archives.

Both QQ commands and web APIs enter these services through the existing
ClanBattle methods. Rule validation precedes mutation, and each operation
commits its database changes before publishing notifications.
"""
import json
import logging
from typing import Optional

from ...ybdata import Clan_challenge, Clan_challenge_undo, Clan_group, Clan_group_backups, Clan_member, User
from ...permissions import can_manage_clan
from ..typing import Groupid, QQid
from ..exception import GroupError, GroupNotExist, InputError, UserError, UserNotInGroup
from ..util import atqq, pcr_datetime
from .handler import SubscribeHandler
from .challenge_state import ChallengeState
from .report_undo import BattleSnapshot, reverse_report
from .battle_state import after_commit, atomic_battle_operation, count_blades, validate_battle_id, validate_boss_number
from .performance import forget_weights
from .realize import get_clan_group, safe_load_json, future_operation, check_next_boss, subscribe_remind, send_group_notification

_logger = logging.getLogger(__name__)

@atomic_battle_operation
def clear_data_slot(self, group_id: Groupid, battle_id: Optional[int] = None):
    """
    清空选择的档案并重置boss状态
    挑战数据应进行备份和确认
    在调用此函数之前，要先检查操作者权限。

    Args:
        group_id: QQ群号
        battle_id: 选择的档案号
    """
    group:Clan_group = get_clan_group(self, group_id)
    if group is None:
        raise GroupNotExist

    battle_id = group.battle_id if battle_id is None else validate_battle_id(battle_id)
    Clan_challenge_undo.delete().where(
        Clan_challenge_undo.gid == group_id, Clan_challenge_undo.bid == battle_id).execute()
    Clan_challenge.delete().where(
        Clan_challenge.gid == group_id, Clan_challenge.bid == battle_id).execute()
    after_commit(lambda: forget_weights(self, group_id, battle_id))
    Clan_group_backups.delete().where(
        Clan_group_backups.group_id == group_id,
        Clan_group_backups.battle_id == battle_id).execute()
    if battle_id != group.battle_id:
        _logger.info('群%s的%s号历史档案已清空', group_id, battle_id)
        return

    now_cycle_boss_health = {}
    level = self._level_by_cycle(1, group.game_server)
    for boss_num, health in enumerate(self.bossinfo[group.game_server][level]):
        now_cycle_boss_health[boss_num+1] = health
    next_cycle_boss_health = {}
    level = self._level_by_cycle(2, group.game_server)
    for boss_num, health in enumerate(self.bossinfo[group.game_server][level]):
        next_cycle_boss_health[boss_num+1] = health

    group.now_cycle_boss_health = json.dumps(now_cycle_boss_health)
    group.next_cycle_boss_health = json.dumps(next_cycle_boss_health)
    group.boss_cycle = 1
    group.challenging_member_list = None
    group.subscribe_list = None
    group.challenging_start_time = 0

    group.save()
    future_operation(self, group, '当前会战档案已清空')
    _logger.info(f'群{group_id}的{battle_id}号存档已清空')


@atomic_battle_operation
def switch_data_slot(self, group_id: Groupid, battle_id: int):
    """
    切换到选择的档案并重置boss状态
    挑战数据应进行备份和确认
    在调用此函数之前，要先检查操作者权限。

    Args:
        group_id: QQ群号
        battle_id：选择的档案号
    """
    group:Clan_group = get_clan_group(self, group_id)
    if group is None: raise GroupNotExist
    battle_id = validate_battle_id(battle_id)
    if battle_id == group.battle_id:
        return
    backups:Clan_group_backups = Clan_group_backups.get_or_create(
        group_id = group_id,
        battle_id = group.battle_id)[0]
    restore:Clan_group_backups = Clan_group_backups.get_or_create(
        group_id = group_id,
        battle_id = battle_id)[0]

    #备份
    backups_group_data = {
        "group_name": group.group_name,
        "privacy": group.privacy,
        "game_server": group.game_server,
        "notification": group.notification,
        "battle_id": group.battle_id,
        "threshold": group.threshold,
        "boss_cycle": group.boss_cycle,
        "now_cycle_boss_health": group.now_cycle_boss_health,
        "next_cycle_boss_health": group.next_cycle_boss_health,
        "challenging_member_list": group.challenging_member_list,
        "subscribe_list": group.subscribe_list,
        "challenging_start_time": group.challenging_start_time,
    }
    backups.group_data = json.dumps(backups_group_data)
    backups.save()

    #还原
    group.battle_id = battle_id
    if restore.group_data: #如果有备份数据则还原
        data:Clan_group = json.loads(restore.group_data)
        group.group_name = data["group_name"]
        group.privacy = data["privacy"]
        group.game_server = data["game_server"]
        group.notification = data["notification"]
        group.threshold = data.get("threshold", group.threshold)
        group.boss_cycle = data["boss_cycle"]
        group.now_cycle_boss_health = data["now_cycle_boss_health"]
        group.next_cycle_boss_health = data["next_cycle_boss_health"]
        group.challenging_member_list = data["challenging_member_list"]
        group.subscribe_list = data["subscribe_list"]
        group.challenging_start_time = data["challenging_start_time"]
    else:   #没有备份数据则新建
        now_cycle_boss_health = {}
        level = self._level_by_cycle(1, group.game_server)
        for boss_num, health in enumerate(self.bossinfo[group.game_server][level]):
            now_cycle_boss_health[boss_num+1] = health
        next_cycle_boss_health = {}
        level = self._level_by_cycle(2, group.game_server)
        for boss_num, health in enumerate(self.bossinfo[group.game_server][level]):
            next_cycle_boss_health[boss_num+1] = health

        group.now_cycle_boss_health = json.dumps(now_cycle_boss_health)
        group.next_cycle_boss_health = json.dumps(next_cycle_boss_health)
        group.boss_cycle = 1
        group.challenging_member_list = None
        group.subscribe_list = None
        group.challenging_start_time = 0

    group.save()
    future_operation(self, group, f'已切换至{battle_id}号存档')
    _logger.info(f'群{group_id}切换至{battle_id}号存档')


@atomic_battle_operation
def challenge(self, group_id, qqid, defeat, damage=0, behalfed=None,
              is_continue=False, *, boss_num=None, previous_day=False, return_seconds=None):
    """Validate a report, then atomically update records and clan state."""
    if not isinstance(defeat, bool) or not isinstance(is_continue, bool):
        raise InputError('尾刀和补偿标记必须是布尔值')
    if return_seconds is not None:
        if not defeat:
            raise InputError('仅尾刀可记录返秒')
        if isinstance(return_seconds, bool) or not isinstance(return_seconds, int) or not 0 <= return_seconds <= 90:
            raise InputError('返秒必须是0至90的整数秒数')
    if not defeat and (isinstance(damage, bool) or not isinstance(damage, int) or damage < 0):
        raise InputError('伤害必须是非负整数')
    behalf = qqid if behalfed is not None else None
    if behalfed is not None:
        qqid = int(behalfed)
    if qqid == behalf:
        behalf = None
    if Clan_member.get_or_none(group_id=group_id, qqid=qqid) is None:
        raise UserNotInGroup()

    group = get_clan_group(self, group_id)
    before = BattleSnapshot.capture(group)
    applications = ChallengeState.from_json(group.challenging_member_list)
    original_boss = self.get_in_boss_num(group_id, qqid)
    explicit_boss = boss_num is not None
    if boss_num is None:
        boss_num = original_boss
    if boss_num is False and not explicit_boss:
        raise GroupError('又不申请出刀又不说打哪个王，报啥子刀啊 (╯‵□′)╯︵┻━┻')
    boss_num = validate_boss_number(boss_num)
    previous_application = applications.get(boss_num, qqid)
    now_health = safe_load_json(group.now_cycle_boss_health, {})
    next_health = safe_load_json(group.next_cycle_boss_health, {})
    if (explicit_boss or not original_boss) and now_health[boss_num] == 0 and not check_next_boss(self, group_id, boss_num):
        raise GroupError('只能挑战2个周目内且不跨阶段的同个boss，请等待该周目的boss全部击杀完毕')
    boss_cycle = group.boss_cycle
    target_health = now_health
    if now_health[boss_num] == 0:
        if next_health[boss_num] == 0:
            raise InputError('只能挑战2个周目内的同个boss')
        target_health = next_health
        boss_cycle += 1
    if not defeat and damage >= target_health[boss_num]:
        raise InputError('伤害超出剩余血量，如击败请使用尾刀')

    date, time = pcr_datetime(area=group.game_server)
    if previous_day:
        today_records = Clan_challenge.select().where(
            Clan_challenge.gid == group_id,
            Clan_challenge.bid == group.battle_id,
            Clan_challenge.challenge_pcrdate == date)
        if today_records.exists():
            raise GroupError('今日报刀记录不为空，无法将记录添加到昨日')
        date -= 1
        time += 86400
    records = Clan_challenge.select().where(
        Clan_challenge.gid == group_id, Clan_challenge.qqid == qqid,
        Clan_challenge.bid == group.battle_id,
        Clan_challenge.challenge_pcrdate == date).order_by(Clan_challenge.cid)
    counts = count_blades(records)
    if counts.finished >= 3:
        raise InputError('昨日上报次数已达到3次' if previous_day else '今日上报次数已达到3次')
    if not explicit_boss and previous_application:
        is_continue = bool(is_continue or previous_application.is_continue)
    else:
        is_continue = counts.choose_compensation(is_continue)
    if is_continue and counts.compensation <= 0:
        raise GroupError('您没有补偿刀')

    # All rejection paths above leave the database and application state intact.
    challenge_damage = target_health[boss_num] if defeat else damage
    target_health[boss_num] -= challenge_damage
    health_remaining = target_health[boss_num]
    report = Clan_challenge.create(
        gid=group_id, qqid=qqid, bid=group.battle_id,
        challenge_pcrdate=date, challenge_pcrtime=time,
        boss_cycle=boss_cycle, boss_num=int(boss_num),
        boss_health_remain=health_remaining,
        challenge_damage=challenge_damage, is_continue=is_continue, behalf=behalf,
        return_seconds=return_seconds)

    rollover_notices = []
    if defeat and all(health == 0 for health in now_health.values()):
        group.boss_cycle += 1
        now_health = next_health.copy()
        rollover_notices = [number for number, health in now_health.items() if health == 0]
        level = self._level_by_cycle(group.boss_cycle + 1, group.game_server)
        next_health = {str(number): health for number, health in
                       enumerate(self.bossinfo[group.game_server][level], 1)}

    tree_notices = []
    if defeat:
        tree_notices = [member for member, info in applications.members(boss_num).items() if info.tree]
        applications.remove_boss(boss_num)
    applications.remove_member(qqid)
    group.now_cycle_boss_health = json.dumps(now_health)
    group.next_cycle_boss_health = json.dumps(next_health)
    group.challenging_member_list = applications.to_json_or_none()
    group.save()

    subscriber = SubscribeHandler(group=group)
    if subscriber.is_subscribed(qqid, int(boss_num)):
        subscriber.unsubscribe(qqid, int(boss_num))
        subscriber.save()
    if tree_notices:
        send_group_notification(self, group_id, '可以下树惹~ _(:з)∠)_\n' + '\n'.join(atqq(member) for member in tree_notices))
    for number in rollover_notices:
        subscribe_remind(self, group_id, number)
    if defeat and check_next_boss(self, group_id, boss_num) and boss_num not in rollover_notices:
        subscribe_remind(self, group_id, boss_num)

    nickname = self._get_nickname_by_qqid(qqid)
    delegated = f'（{self._get_nickname_by_qqid(behalf)}代）' if behalf else ''
    if defeat:
        message = '{}{}对{}号boss造成了{:,}点伤害，击败了boss\n（今日已完成{}刀，还有补偿刀{}刀，本刀是{}）\n'.format(
            nickname, delegated, boss_num, challenge_damage,
            counts.finished + 1 if is_continue else counts.finished,
            counts.compensation - 1 if is_continue else counts.compensation + 1,
            '尾余刀' if is_continue else '收尾刀')
    else:
        message = '{}{}对{}号boss造成了{:,}点伤害\n（今日已出完整刀{}刀，还有补偿刀{}刀，本刀是{}）\n'.format(
            nickname, delegated, boss_num, challenge_damage,
            counts.finished + 1,
            counts.compensation - 1 if is_continue else counts.compensation,
            '剩余刀' if is_continue else '完整刀')
    if return_seconds is not None:
        message += f'返秒：{return_seconds}s\n'
    message += '\n'.join(self.challenger_info_small(group, boss_num))
    Clan_challenge_undo.create(cid=report.cid, gid=group_id, bid=group.battle_id,
                               before_state=before.dumps(),
                               after_state=BattleSnapshot.capture(group).dumps())
    future_operation(self, group, message)
    return message


@atomic_battle_operation
def undo(self, group_id: Groupid, qqid: QQid) :
    """
    删除上一刀的记录

    Args:
        group_id: QQ群号
        qqid: 发起撤销请求的成员QQ号
    """
    group:Clan_group = get_clan_group(self, group_id)
    if group is None: raise GroupNotExist
    user:User = User.get_or_create(qqid = qqid, defaults = {'clan_group_id': group_id})[0]
    last_challenge:Clan_challenge = self._get_group_previous_challenge(group)

    if last_challenge is None: raise GroupError('本群无出刀记录')
    membership = Clan_member.get_or_none(group_id=group_id, qqid=qqid)
    if not can_manage_clan(user, membership) and (membership is None or last_challenge.qqid != qqid):
        raise UserError('无权撤销')

    snapshot = Clan_challenge_undo.get_or_none(
        cid=last_challenge.cid, gid=group_id, bid=group.battle_id)
    if snapshot is not None:
        restored = reverse_report(group, BattleSnapshot.loads(snapshot.before_state),
                                  BattleSnapshot.loads(snapshot.after_state))
        restored.restore(group)
    else:
        last_num = str(last_challenge.boss_num) #上一刀的boss_num
        last_cycle = last_challenge.boss_cycle  #上一刀的周目数
        level = self._level_by_cycle(last_cycle, group.game_server)#阶段

        now_cycle_boss_health = safe_load_json(group.now_cycle_boss_health, {})
        next_cycle_boss_health = safe_load_json(group.next_cycle_boss_health, {})
        real_cycle_boss_health = now_cycle_boss_health #用来记录上一刀打的是哪个周目的boss

        if last_cycle < group.boss_cycle:   # 判断被撤销的一刀是否是切换周目的一刀
            for boss_num, health in now_cycle_boss_health.items():
                next_cycle_boss_health[boss_num] = health
                now_cycle_boss_health[boss_num] = 0
            now_cycle_boss_health[last_num] = last_challenge.challenge_damage
            group.boss_cycle = last_cycle
        else:
            if last_cycle != group.boss_cycle: real_cycle_boss_health = next_cycle_boss_health
            real_cycle_boss_health[last_num] += last_challenge.challenge_damage
            full_health = self.bossinfo[group.game_server][level][int(last_num)-1]
            if real_cycle_boss_health[last_num] > full_health: real_cycle_boss_health[last_num] = full_health

    last_challenge.delete_instance()
    after_commit(lambda: forget_weights(self, group_id, last_challenge.bid, last_challenge.cid))
    if snapshot is not None:
        snapshot.delete_instance()
    else:
        group.now_cycle_boss_health = json.dumps(now_cycle_boss_health)
        group.next_cycle_boss_health = json.dumps(next_cycle_boss_health)
    group.save()

    nik = self._get_nickname_by_qqid(last_challenge.qqid)
    msg = f'{nik}的出刀记录已被撤销'
    if snapshot is None:
        msg += '（旧记录无状态快照，仅恢复血量和刀数）'
    future_operation(self, group, msg)
    return msg
