"""Existing performance points, with archive-scoped stage and record weights."""
import hashlib
import json
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ...ybdata import Clan_challenge, Clan_member, User


class PerformanceError(ValueError):
    pass


def weight(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PerformanceError('权重必须是 0 至 100 之间的数字')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise PerformanceError('权重格式错误') from exc
    if not result.is_finite() or result < 0 or result > 100 or result.as_tuple().exponent < -4:
        raise PerformanceError('权重须在 0 至 100 之间，最多 4 位小数')
    return result


def validate_config(config, records):
    if not isinstance(config, dict) or set(config) != {'stages', 'overrides', 'threshold'}:
        raise PerformanceError('业绩配置格式错误')
    threshold = config['threshold']
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise PerformanceError('伤害阈值必须是非负整数')
    stages, overrides = config['stages'], config['overrides']
    if not isinstance(stages, list) or not 1 <= len(stages) <= 30 or not isinstance(overrides, dict):
        raise PerformanceError('阶段或单刀配置格式错误')
    normalized, previous = [], 0
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) != {'from', 'to', 'weight'}:
            raise PerformanceError('阶段配置格式错误')
        start, end = stage['from'], stage['to']
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int)
                or not isinstance(end, int) or start != previous + 1 or end < start or end > 1000000):
            raise PerformanceError('阶段须从第 1 周开始，周目连续且不重叠')
        stage_weight = weight(stage['weight'])
        if stage_weight != stage_weight.quantize(Decimal('.1')):
            raise PerformanceError('阶段权重最多 1 位小数')
        normalized.append({'from': start, 'to': end, 'weight': str(stage_weight)})
        previous = end
    ids = {str(record.cid) for record in records}
    if any(record.boss_cycle > previous for record in records):
        raise PerformanceError('阶段周目范围未覆盖档案中的全部报刀')
    if set(overrides) - ids:
        raise PerformanceError('单刀配置包含已撤销或不属于本公会档案的记录，请刷新')
    return {'threshold': threshold, 'stages': normalized,
            'overrides': {cid: str(weight(value)) for cid, value in overrides.items()}}


def config_path(battle, group_id, battle_id):
    return Path(battle.setting['dirname']) / 'performance' / str(int(group_id)) / f'{int(battle_id)}.json'


def record_fingerprint(record):
    fields = ('cid', 'gid', 'bid', 'qqid', 'challenge_pcrdate', 'challenge_pcrtime',
              'boss_cycle', 'boss_num', 'boss_health_remain', 'challenge_damage',
              'is_continue', 'behalf', 'message')
    raw = json.dumps([getattr(record, field) for field in fields], ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def load_config(battle, group, battle_id, records):
    path = config_path(battle, group.group_id, battle_id)
    if path.exists():
        raw = path.read_bytes()
        try:
            saved = json.loads(raw)
            config = saved['config']
            # Undo/clear can remove records without making remaining settings unusable.
            fingerprints = {str(record.cid): record_fingerprint(record) for record in records}
            config['overrides'] = {cid: value for cid, value in config['overrides'].items()
                                   if cid in fingerprints and saved['records'].get(cid) == fingerprints[cid]}
            config = validate_config(config, records)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise PerformanceError('保存的业绩配置无效，请联系管理员检查数据目录') from exc
        return config, hashlib.sha256(raw).hexdigest(), True
    config = {'threshold': group.threshold, 'overrides': {}, 'stages': [
        {'from': start, 'to': end, 'weight': '1'}
        for start, end in battle.level_by_cycle[group.game_server]]}
    return validate_config(config, records), 'new', False


def save_config(battle, group, battle_id, config, revision, records):
    config = validate_config(config, records)
    path = config_path(battle, group.group_id, battle_id)
    current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else 'new'
    if revision != current:
        raise PerformanceError('其他管理员已修改配置，请刷新后重新编辑')
    write_file(path, {'config': config, 'records': {
        str(record.cid): record_fingerprint(record) for record in records
        if str(record.cid) in config['overrides']}})


def write_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as target:
            temporary = target.name
            json.dump(data, target, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def forget_weights(battle, group_id, battle_id, cid=None):
    """Called after database commit, before a removed record ID can be reused."""
    if 'dirname' not in battle.setting:
        return
    path = config_path(battle, group_id, battle_id)
    if not path.exists():
        return
    saved = json.loads(path.read_bytes())
    if cid is None:
        saved['config']['overrides'], saved['records'] = {}, {}
    else:
        saved['config']['overrides'].pop(str(cid), None)
        saved['records'].pop(str(cid), None)
    write_file(path, saved)


def archive_records(group_id, battle_id):
    return list(Clan_challenge.select().where(
        Clan_challenge.gid == group_id, Clan_challenge.bid == battle_id
    ).order_by(Clan_challenge.challenge_pcrdate, Clan_challenge.challenge_pcrtime, Clan_challenge.cid))


def base_points(record, threshold):
    if not record.is_continue and record.boss_health_remain > 0:
        return Decimal('1'), 'full_blade', '整刀'
    kind, label = ('small_end_blade', '补偿刀') if record.is_continue else ('end_blade', '尾刀')
    return Decimal('1') if record.challenge_damage >= threshold else Decimal('.5'), kind, label


def calculate(records, members, config, names):
    rows, details = {}, []
    def member(qqid):
        if qqid not in rows:
            rows[qqid] = {'qqid': qqid, 'nickname': names.get(qqid, str(qqid)),
                         'score': Decimal(0), 'base_score': Decimal(0), 'damage': 0,
                         'full_blade': 0, 'end_blade': 0, 'small_end_blade': 0,
                         'total_blades': 0, 'stage_blades': [0] * max(3, len(config['stages'])),
                         'stage_scores': [Decimal(0)] * max(3, len(config['stages']))}
        return rows[qqid]
    for qqid in members:
        member(qqid)
    for record in records:
        stage_number, stage = next(((index + 1, stage) for index, stage in enumerate(config['stages'])
                                    if stage['from'] <= record.boss_cycle <= stage['to']), (None, None))
        if stage is None:
            raise PerformanceError('阶段范围未覆盖报刀周目')
        base, kind, label = base_points(record, config['threshold'])
        override = config['overrides'].get(str(record.cid))
        applied_weight = weight(override if override is not None else stage['weight'])
        score = base * applied_weight
        owner = record.behalf or record.qqid
        row = member(owner)
        row['total_blades'] += 1
        row['stage_blades'][stage_number - 1] += 1
        row['stage_scores'][stage_number - 1] += score
        row['score'] += score
        row['base_score'] += base
        row['damage'] += record.challenge_damage
        row[kind] += 1
        details.append({'cid': record.cid, 'qqid': record.qqid, 'nickname': names.get(record.qqid, str(record.qqid)),
                        'credited_to': owner, 'credited_name': names.get(owner, str(owner)),
                        'date': record.challenge_pcrdate, 'cycle': record.boss_cycle, 'boss_num': record.boss_num,
                        'damage': record.challenge_damage, 'kind': label, 'stage': stage_number,
                        'base_score': float(base), 'weight': str(applied_weight), 'score': float(score),
                        'override': override})
    ranking = sorted(rows.values(), key=lambda row: (-row['score'], row['qqid']))
    for row in ranking:
        row['stage_scores'] = [float(value) for value in row['stage_scores']]
        row['score'], row['base_score'] = float(row['score']), float(row['base_score'])
    return ranking, details


def report(battle, group, battle_id):
    records = archive_records(group.group_id, battle_id)
    config, revision, saved = load_config(battle, group, battle_id, records)
    members = [member.qqid for member in Clan_member.select().where(Clan_member.group_id == group.group_id)]
    ids = set(members) | {record.qqid for record in records} | {record.behalf for record in records if record.behalf}
    names = {user.qqid: user.nickname or str(user.qqid) for user in User.select().where(User.qqid.in_(ids))}
    ranking, details = calculate(records, members, config, names)
    return {'config': config, 'revision': revision, 'saved': saved, 'ranking': ranking, 'records': details}
