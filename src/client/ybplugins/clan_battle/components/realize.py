import math
import os
import sys
import json
import peewee
import base64
import random
import string
import asyncio
from collections import Counter
from .tail_return_seconds import effective_return_seconds, pending_compensations, select_compensation
import logging
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageFont, ImageDraw
from typing import Any, Dict, List, Optional, Union, Tuple

from .handler import ChallengeHandler, SubscribeHandler
from .challenge_state import ChallengeState
from .battle_state import after_commit, atomic_battle_operation, count_blades, validate_boss_number

from ..typing import ClanBattleReport, Groupid, Pcr_date, QQid
from ...web_util import async_cached_func
from ..util import atqq, pcr_datetime, pcr_timestamp, timed_cached_func

from ...ybdata import Clan_challenge, Clan_group, Clan_member, User, Clan_group_backups
from ..exception import GroupError, GroupNotExist, InputError, UserError, UserNotInGroup
from .multi_cq_utils import who_am_i
from .image_engine import download_user_profile_image, generate_combind_boss_state_image, BossStatusImageCore, get_process_image, GroupStateBlock

_logger = logging.getLogger(__name__)
FILE_PATH = Path(sys._MEIPASS).resolve() if "_MEIPASS" in dir(sys) else Path(__file__).resolve().parent

def safe_load_json(text, back = None):
	return text and json.loads(text) or back

def text_2_pic(self, text:string, weight:int, height:int, bg_color:Tuple, text_color:string, font_size:int, text_offset:Tuple):
	im = Image.new("RGB", (weight, height), bg_color)
	dr = ImageDraw.Draw(im)
	FONTS_PATH = os.path.join(FILE_PATH,'fonts')
	FONTS = os.path.join(FONTS_PATH,'msyh.ttf')
	try:
    # 尝试使用指定的字体加载
		font = ImageFont.truetype(FONTS, font_size)
	except OSError:
    # 加载失败时使用默认字体
		font = ImageFont.load_default()
	dr.text(text_offset, text, font=font, fill=text_color)
	bio = BytesIO()
	im.save(bio, format='PNG')
	base64_str = 'base64://' + base64.b64encode(bio.getvalue()).decode()
	return f"[CQ:image,file={base64_str}]"

def future_operation(self, group, msg):
    payload = (self._boss_data_dict(group), group.boss_cycle, msg)
    def publish():
        previous = self._boss_status.get(group.group_id)
        if previous is not None and not previous.done():
            previous.set_result(payload)
        self._boss_status[group.group_id] = asyncio.get_event_loop().create_future()
    after_commit(publish)


def send_group_notification(self, group_id, message):
    async def send():
        try:
            await self.api.send_group_msg(
                self_id=who_am_i(group_id), group_id=group_id, message=message)
        except Exception:
            _logger.exception('会战通知发送失败，群%s', group_id)
    after_commit(lambda: asyncio.ensure_future(send()))

#获取公会数据实例，确保每次获取的都是同一个
def get_clan_group(self, group_id):
	if group_id in self.group_data_list:
		return self.group_data_list[group_id]
	else:
		group:Clan_group = Clan_group.get_or_none(group_id=group_id)
		if group is not None:
			self.group_data_list[group_id] = group
		return group

#阶段周目
def _level_by_cycle(self, cycle, game_server=None):
	level = 0
	for lv in self.level_by_cycle[game_server]:
		if cycle >= lv[0] and cycle <= lv[1] : return level
		level += 1
	return level

#通过qq号获取名字
@timed_cached_func(128, 3600, ignore_self=True)
def _get_nickname_by_qqid(self, qqid) -> Union[str, None]:
	user = User.get_or_create(qqid=qqid)[0]
	if user.nickname is None:
		asyncio.ensure_future(self._update_user_nickname_async(
			qqid = qqid, group_id = None))
	return user.nickname or str(qqid)

#获取上一个出刀记录
def _get_group_previous_challenge(self, group: Clan_group):
	Clan_challenge_alias = Clan_challenge.alias()
	query = Clan_challenge.select().where(
		Clan_challenge.cid == Clan_challenge_alias.select(
			peewee.fn.MAX(Clan_challenge_alias.cid)
		).where(
			Clan_challenge_alias.gid == group.group_id,
			Clan_challenge_alias.bid == group.battle_id,
		)
	)
	try : return query.get()
	except peewee.DoesNotExist:return None

#更新群列表
async def _update_group_list_async(self):
	try : group_list = await self.api.get_group_list()
	except Exception as e:
		_logger.exception('获取群列表错误'+str(e))
		return False

	for group_info in group_list:
		group:Clan_group = get_clan_group(self, group_info['group_id'],)
		if group is None : continue
		group.group_name = group_info['group_name']
		group.save(only=[Clan_group.group_name])
	return True

#获取群成员列表
@async_cached_func(16)
async def _fetch_member_list_async(self, group_id):
	try:
		group_member_list = await self.api.get_group_member_list(group_id=group_id)
	except Exception as e:
		_logger.exception('获取群成员列表错误' + str(type(e)) + str(e))
		asyncio.ensure_future(self.api.send_group_msg(
			# FIXME:多CQ
			group_id = group_id, message = '获取群成员错误，这可能是缓存问题，请重启go-cqhttp后再试'))
		return []
	return group_member_list

#更新所有群成员
async def _update_all_group_members_async(self, group_id):
	group_member_list = await self._fetch_member_list_async(group_id)
	for member in group_member_list:
		user = User.get_or_create(qqid=member['user_id'])[0]
		membership = Clan_member.get_or_create(group_id = group_id, qqid = member['user_id'])[0]
		user.nickname = member.get('card') or member['nickname']
		user.clan_group_id = group_id
		membership.role = (100 if member['role'] == 'member' else 10)
		if user.authority_group >= 10:
			user.authority_group = membership.role
		user.save()
		membership.save()

	# refresh member list
	self.get_member_list(group_id, nocache = True)

#更新成员名字
async def _update_user_nickname_async(self, qqid, group_id = None):
	try:
		user = User.get_or_create(qqid=qqid)[0]
		if group_id is None:
			userinfo = await self.api.get_stranger_info(user_id=qqid)
			user.nickname = userinfo['nickname']
		else:
			userinfo = await self.api.get_group_member_info(group_id=group_id, user_id=qqid)
			user.nickname = userinfo['card'] or userinfo['nickname']
		user.save()

		# refresh
		if user.nickname is not None : self._get_nickname_by_qqid(qqid, nocache=True)
	except Exception as e : _logger.exception(e)

def _update_user_profile_image(self, user_id: Optional[Union[int,List[int]]] = None, group_id: Optional[int] = None) -> None:
	update_qqid_list = set()
	if not (group_id and user_id):
		for this_user in User.select(User.qqid):
			update_qqid_list.add(this_user.qqid)
	if user_id:
		if isinstance(user_id,int):
			update_qqid_list.add(user_id)
		elif isinstance(user_id,List):
			for i in user_id:
				update_qqid_list.add(i)
	if group_id:
		for this_user in Clan_member.select().where(Clan_member.group_id == group_id):
			update_qqid_list.add(this_user.qqid)
	asyncio.ensure_future(download_user_profile_image(list(update_qqid_list)))

#获取boss当前数据
def _boss_data_dict(self, group: Clan_group) -> Dict[str, Any]:
	cycle = group.boss_cycle
	now_health = safe_load_json(group.now_cycle_boss_health, {})
	next_health = safe_load_json(group.next_cycle_boss_health, {})
	challenging_member_list = safe_load_json(group.challenging_member_list, {})

	back_data = {}
	for i in range(5):
		str_boss_num = str(i + 1)
		num_boss_num = i + 1
		next_flag = now_health[str_boss_num] == 0
		level = self._level_by_cycle(cycle, group.game_server)
		icon_id = self.setting['boss_id'][group.game_server][i]
		back_data[num_boss_num] = {
			'is_next': next_flag,
			'cycle': next_flag and cycle+1 or cycle,
			'health': 0 if now_health[str_boss_num] == 0 and not check_next_boss(self, group.group_id, str_boss_num)
						else next_flag and next_health[str_boss_num] or now_health[str_boss_num],
			'full_health': self.bossinfo[group.game_server][level][i],
			'challenger': str_boss_num in challenging_member_list and challenging_member_list[str_boss_num] or 0,
			'icon_id': icon_id,
			'name': self.boss_id_name[str_boss_num][icon_id]
		}
	return back_data




#创建公会
def create_group(self, group_id: Groupid, game_server, group_name=None) -> None:
	"""
	Args:
		group_id: QQ群号
		group_name: QQ群名，用作公会名
		game_server: 服务器名("jp" "tw" "cn" "kr")
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None:
		now_cycle_boss_health = {}
		level = self._level_by_cycle(1, game_server)
		for boss_num, health in enumerate(self.bossinfo[game_server][level]):
			now_cycle_boss_health[boss_num+1] = health
		next_cycle_boss_health = {}
		level = self._level_by_cycle(2, game_server)
		for boss_num, health in enumerate(self.bossinfo[game_server][level]):
			next_cycle_boss_health[boss_num+1] = health
		group = Clan_group.create(
			group_id = group_id,
			group_name = group_name,
			game_server = game_server,
			now_cycle_boss_health = json.dumps(now_cycle_boss_health),
			next_cycle_boss_health = json.dumps(next_cycle_boss_health),
		)
	elif group.deleted:
		group.deleted = False
		group.game_server = game_server
		group.save()
	else : raise GroupError('群已经存在')
	self._boss_status[group_id] = asyncio.get_event_loop().create_future()

	# refresh group list
	asyncio.ensure_future(self._update_group_list_async())

#加入公会
async def bind_group(self, group_id:Groupid, qqid:QQid, nickname:str):
	"""
	Args:
		group_id: QQ群号
		qqid: 加入公会的成员QQ号
		nickname: 用来显示的名字
	"""
	user = User.get_or_create(qqid=qqid)[0]
	user.clan_group_id = group_id
	user.nickname = nickname
	user.deleted = False
	try:
		groupmember = await self.api.get_group_member_info(group_id = group_id, user_id = qqid)
		role = 100 if groupmember['role'] == 'member' else 10
	except Exception as e:
		_logger.exception(e)
		role = 100
	membership = Clan_member.get_or_create(
		group_id = group_id,
		qqid = qqid,
		defaults = {'role': role})[0]
	user.save()

	# refresh
	self.get_member_list(group_id, nocache=True)
	if nickname is None:
		asyncio.ensure_future(self._update_user_nickname_async(qqid = qqid, group_id = group_id))
	return membership

#删除成员
@atomic_battle_operation
def drop_member(self, group_id: Groupid, member_list: List[QQid]):
	"""
	删除公会里的成员（一般在面板里操作，可同时删除多个）

	在调用此函数之前，要先检查操作者权限。
	Args:
		group_id: QQ群号
		member_list: 要被删除的成员QQ号列表
	"""
	delete_count = Clan_member.delete().where(
		Clan_member.group_id == group_id,
		Clan_member.qqid.in_(member_list)
	).execute()

	for user_id in member_list:
		user = User.get_or_none(qqid=user_id)
		if user is not None:
			user.clan_group_id = None
			user.save()

	# refresh member list
	self.get_member_list(group_id, nocache=True)
	return delete_count

#修改boss状态
@atomic_battle_operation
def modify(self, group_id: Groupid, cycle=None, bossData=None):
	"""
	在调用此函数之前，要先检查操作者权限。

	Args:
		group_id: group id
		cycle: 要改到第几周目
		bossData: 结构和 _boss_data_dict() 获取的数据一致
	"""
	if cycle and cycle < 1:
		raise InputError('周目数不能为负')

	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist

	next_cycle_level = self._level_by_cycle(cycle and cycle+1 or group.boss_cycle+1, group.game_server)
	now_health = safe_load_json(group.now_cycle_boss_health, {})
	next_health = safe_load_json(group.next_cycle_boss_health, {})
	now_cycle_level = self._level_by_cycle(cycle or group.boss_cycle, group.game_server)

	for boss_num, data in bossData.items():
		next_cycle_full_boss_health = self.setting['boss'][group.game_server][next_cycle_level][int(boss_num)-1]
		if data["is_next"]:
			if now_cycle_level == next_cycle_level:
				now_health[boss_num] = 0
				next_health[boss_num] = data["health"]
			else:
				raise InputError('设置为下个周目的BOSS与当前周目BOSS不可处于不同阶段。')
		else:
			now_health[boss_num] = data["health"]
			next_health[boss_num] = next_cycle_full_boss_health
	
	group.now_cycle_boss_health = json.dumps(now_health)
	group.next_cycle_boss_health = json.dumps(next_health)
	group.boss_cycle = cycle

	group.save()

	msg = 'boss状态已修改'
	future_operation(self, group, msg)
	return msg

#修改服务器
@atomic_battle_operation
def change_game_server(self, group_id: Groupid, game_server):
	"""
	在调用此函数之前，要先检查操作者权限。

	Args:
		group_id: QQ群号
		game_server: 服务器名("jp" "tw" "cn" "kr")
	"""
	if game_server not in ("jp", "tw", "cn", "kr"):
		raise InputError(f'不存在{game_server}游戏服务器')
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	group.game_server = game_server
	group.save()

#获取当期会战数据记录档案的编号
def get_data_slot_record_count(self, group_id: Groupid):
	"""
	创建新档并重置boss状态
	挑战数据应进行备份和确认
	在调用此函数之前，要先检查操作者权限。

	Args:
		group_id: QQ群号
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	counts = []
	for c in Clan_challenge.select(
		Clan_challenge.bid,
		peewee.fn.COUNT(Clan_challenge.cid).alias('record_count'),
	).where(Clan_challenge.gid == group_id).group_by(Clan_challenge.bid,):
		counts.append({'battle_id': c.bid, 'record_count': c.record_count})
	return counts

#清空会战数据记录档案
def clear_data_slot(self, group_id, battle_id=None):
    from .battle_service import clear_data_slot as clear_archive
    return clear_archive(self, group_id, battle_id)

#切换会战数据记录档案
def switch_data_slot(self, group_id, battle_id):
    from .battle_service import switch_data_slot as switch_archive
    return switch_archive(self, group_id, battle_id)

def _get_available_empty_battle_id(self, group_id: int) -> int:
	"""
	获取最靠前且未使用的档案编号

	:param group_id: QQ群号
	"""
	group = get_clan_group(self, group_id=group_id)
	if group is None: raise GroupNotExist
	statement = Clan_challenge.select(Clan_challenge.bid).where(Clan_challenge.gid == group_id).group_by(Clan_challenge.bid)
	counts = statement.count()
	def bid_generator():
		for i in statement.order_by(Clan_challenge.bid):
			yield i
	temp = bid_generator()
	for i in range(counts): # 查找并返回档案编号中被跳过使用的编号
		if i != next(temp).bid:
			return i
	return counts # 档案都已按照顺序使用，则返回顺序下新档案编号，因档案编号从0开始所以无需+1

#向指定个人私聊发送提醒
async def send_private_remind(self, member_list:List[QQid] = None, member_id:QQid = None, content: str = None):
	if member_list:
		for qqid in member_list:
			await asyncio.sleep(random.randint(3, 10))
			try:
				await self.api.send_private_msg(user_id=qqid, message=content)
				_logger.info(f'向{qqid}发送出刀提醒')
			except Exception as e:
				_logger.exception(e)
	elif member_id and member_id > 0:
		try:
			await self.api.send_private_msg(user_id=member_id, message=content)
			_logger.info(f'向{member_id}发送代刀提醒')
		except Exception as e:
			_logger.exception(e)

#发送出刀提醒
def send_remind(self,
				group_id: Groupid,
				member_list: List[QQid],
				sender: QQid,
				send_private_msg: bool = False):
	"""
	在调用此函数之前，要先检查操作者权限。

	Args:
		group_id: QQ群号
		member_list: 被提醒的成员QQ号列表
		sender: 发送者QQ号
		send_private_msg: 是否私聊发送
	"""
	sender_name = self._get_nickname_by_qqid(sender)
	if send_private_msg:
		asyncio.ensure_future(self.send_private_remind(
			member_list=member_list,
			content=f'{sender_name}提醒您及时完成今日出刀',
		))
	else:
		message = ' '.join(atqq(qqid) for qqid in member_list)
		asyncio.ensure_future(self.api.send_group_msg(
			self_id = who_am_i(group_id), 
			group_id=group_id,
			message=message+f'\n=======\n{sender_name}提醒您及时完成今日出刀',
		))

#发送代刀提醒给被代刀的玩家
def behelf_remind(self, member_id, msg):
	asyncio.ensure_future(self.send_private_remind(member_id = member_id,content = msg))
#当前的boss状态
def boss_status_summary(self, group_id:Groupid) -> str:
	boss_summary = self.challenger_info(group_id)

	return boss_summary


#报刀
def challenge(self, group_id, qqid, defeat, damage=0, behalfed=None,
              is_continue=False, *, boss_num=None, previous_day=False, return_seconds=None, compensation_seconds=None):
    from .battle_service import challenge as report
    return report(self, group_id, qqid, defeat, damage, behalfed, is_continue,
                  boss_num=boss_num, previous_day=previous_day, return_seconds=return_seconds,
                  compensation_seconds=compensation_seconds)

#撤销上一刀的伤害
def undo(self, group_id, qqid):
    from .battle_service import undo as undo_report
    return undo_report(self, group_id, qqid)

#预约x/预约表
@atomic_battle_operation
def subscribe(self, group_id:Groupid, qqid:QQid, msg, note):
	"""
	预约某个boss或查看所有已预约的玩家

	Args:
		msg: 第几个王 or '表'
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	if not msg: GroupError('您预约了一个空气')
	subscribe_handler = SubscribeHandler(group=group)
	if msg == '表':
		back_msg = []
		if not subscribe_handler.have_subscribe:
			raise GroupError('目前没有人预约任意一个Boss')
		back_msg.append("预约表：")
		for boss_num, subscribe_data in subscribe_handler.data.items():
			back_msg.append(f'==={boss_num}号Boss===')
			for boss_qqid, qqid_note in subscribe_data.items():
				back_msg.append(f'{self._get_nickname_by_qqid(boss_qqid)}' + (f'：{qqid_note}' if qqid_note else ''))
		back_msg.append('='*12)
		return '\n'.join(back_msg)
	else:
		boss_num = int(msg)
		if subscribe_handler.is_subscribed(qqid, boss_num):
			raise GroupError('你已经预约过这个boss啦 (╯‵□′)╯︵┻━┻')
		subscribe_handler.subscribe(qqid, boss_num, note)
		subscribe_handler.save()
		return f'预约{boss_num}王成功！下个{boss_num}王出现时会at提醒。'

#预约提醒
def subscribe_remind(self, group_id:Groupid, boss_num):
	group:Clan_group = get_clan_group(self, group_id)
	subscribe_handler = SubscribeHandler(group=group)
	boss_num = int(boss_num)
	if not subscribe_handler.get_subscribe_list(boss_num):
		return
	hint_message = f'船新的{boss_num}王来惹~ _(:з)∠)_\n'
	for user_id in subscribe_handler.get_subscribe_list(boss_num):
		hint_message += atqq(user_id)
		note = subscribe_handler.get_note(user_id, boss_num)
		hint_message += ('：' + note) if note else ''
		hint_message += '\n'
	hint_message = hint_message[:-1]
	send_group_notification(self, group_id, hint_message)
	# 仅发送预约提醒，不清空预约列表（预约者出刀击杀boss后由challenge单独移除）

#取消预约
@atomic_battle_operation
def subscribe_cancel(self, group_id:Groupid, boss_num, qqid = None):
	'''
	取消预约特定boss

	Args:
		boss_num: 几王
		qqid: 不填为删除特定boss的整个预约记录，填则删除特定用户的单个预约记录
	'''
	group:Clan_group = get_clan_group(self, group_id)
	if not boss_num: raise GroupError('您取消了个寂寞')
	subscribe_handler = SubscribeHandler(group=group)
	boss_num = int(boss_num)
	if not qqid:
		subscribe_handler.unsubscribe_all(boss_num)
	else:
		if not subscribe_handler.is_subscribed(qqid, boss_num):
			raise GroupError('您还没有预约这个boss')
		subscribe_handler.unsubscribe(qqid, boss_num)
	subscribe_handler.save()
	return '取消成功~'

#获取预约列表
def get_subscribe_list(self, group_id: Groupid):
	"""
	返回预约列表

	Args:
		group_id: QQ群号
	"""
	group:Clan_group = get_clan_group(self, group_id)
	subscribe_handler = SubscribeHandler(group=group)
	back_info = []
	for boss_num, qqid_list in subscribe_handler.data.items():
		for qqid, msg in qqid_list.items():
			back_info.append({
				'boss': boss_num,
				'qqid': qqid,
				'message': msg,
			})
	return back_info

#挂树
@atomic_battle_operation
def put_on_the_tree(self, group_id: Groupid, qqid: QQid, message=None, boss_num=False, behalfed=None):
	"""
	放在树上

	Args:
		group_id: QQ群号
		qqid: 报告挂树的qq号（不一定为真正挂树的成员
		message: 留言
		boss_num: [可选]指定挂树的boss，若不指定则继续查找
		behalf：[可选]真正挂树的qq号，默认None，视为报告挂树者挂树
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist

	challenger = behalfed and behalfed or qqid
	behalf_is_member = None
	if behalfed:
		behalf = qqid
		if User.get_or_none(qqid=behalf) is None:
			behalf_is_member = False
			behalf_nickname = str(behalf)
			behalf = None
		else:
			behalf_is_member = True
			behalf_nickname = self._get_nickname_by_qqid(behalf)
	else:
		behalf = None

	if User.get_or_none(qqid=challenger) is None:
		raise GroupError('请挂树者先加入公会')
	challenger_nickname = self._get_nickname_by_qqid(challenger)

	if boss_num == False:
		if not self.check_blade(group_id, challenger):
			raise GroupError('你既没申请出刀，也没说挂哪个，挂啥子树啊 (╯‵□′)╯︵┻━┻')
		else:
			boss_num = self.get_in_boss_num(group_id, challenger)

	boss_num = str(boss_num)
	if not self.check_blade(group_id, challenger):
		try:
			if behalfed:
				self.apply_for_challenge(False, group_id, behalf, boss_num, send_web=False, behalfed=challenger)
			else:
				self.apply_for_challenge(False, group_id, challenger, boss_num, send_web=False, behalfed=behalf)
		except Exception as e1:
			if '完整' in str(e1):
				try:
					if behalfed:
						self.apply_for_challenge(True, group_id, behalf, boss_num, send_web=False, behalfed=challenger)
					else:
						self.apply_for_challenge(True, group_id, challenger, boss_num, send_web=False, behalfed=behalf)
				except Exception as e2:
					if '补偿' in str(e2):
						raise GroupError('你今天都下班了，挂啥子树啊 (╯‵□′)╯︵┻━┻')
					else:
						raise GroupError(str(e2))
			else:
				raise GroupError(str(e1))
	else:
		if str(self.get_in_boss_num(group_id, challenger)) != str(boss_num):
			raise GroupError('你申请的王和挂树的王不一样，怎么挂树啊 (╯‵□′)╯︵┻━┻')

	handler = ChallengeHandler(group)
	if not ((behalf is None) and (behalf_is_member is None)):
		message = f'[「{behalf_nickname}」代挂]' + str(message)
	try:
		handler.state.put_on_tree(challenger, message)
	except ValueError:
		raise GroupError('您已经在树上了')
	handler.save()
	msg = f'{challenger_nickname}挂树惹~ (っ °Д °;)っ'
	future_operation(self, group, msg)
	return msg

#查树
def query_tree(self, group_id: Groupid, user_id: QQid, boss_id=0) -> dict:
	"""
	Args:
		self, group_id, user_id, boss_id(Optional):不填boss_id或给0为查询所有
	OUTPUT:
		{"1":[(10000, "消息：马化腾一号挂树")], "2":[($QID, $MSG)], "3":[], "4":[], "5":[]}
	"""
	qid = str(user_id)
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	user = User.get_or_none(qqid=user_id)
	if user is None: raise GroupError('请先加入公会')
	challenging_member_list = safe_load_json(group.challenging_member_list, {})
	result = {"1": [], "2": [], "3": [], "4": [], "5": []}
	if boss_id == 0:
		for i in range(1, 6):
			try:
				for qid in challenging_member_list[str(i)]:
					# reply += f""
					if challenging_member_list[str(i)][qid]['tree']:
						result[str(i)].append((qid, challenging_member_list[str(i)][qid]['msg']))
			except KeyError:
				continue
	else:
		boss_id = str(boss_id)
		for qid in challenging_member_list[boss_id]:
			if challenging_member_list[boss_id][qid]['tree']:
				result[boss_id].append((qid, challenging_member_list[boss_id][qid]['msg']))
	return result

#是否挂树
def check_tree(self, group_id: Groupid, user_id: QQid):
	"""
	查查这位大聪明在不在树上，在树上返回在哪个王(int)，不在树上返回False

	Args:
		group_id: QQ群号
		qqid: 可能挂树的大聪明的QQ号
	"""
	qid = str(user_id)
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	user = User.get_or_none(qqid=user_id)
	if user is None: raise GroupError('请先加入公会')
	found = ChallengeState.from_json(group.challenging_member_list).find_member(user_id)
	if found is not None and found[1].tree:
		return int(found[0])
	return False


#下树
@atomic_battle_operation
def take_it_of_the_tree(self, group_id: Groupid, qqid: QQid, boss_num=0, take_it_type = 0, send_web = True):
	"""
	把ta从树上取下来

	Args:
		group_id: QQ群号
		qqid: 挂树的霉b/菜b的QQ号
		boss_num: 砍一棵树
		take_it_type: 0下一个人 1下一棵树
		send_web:是否更新web面板数据
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	
	user = User.get_or_none(qqid=qqid)
	if user is None: raise GroupError('请先加入公会')

	handler = ChallengeHandler(group)

	if take_it_type == 0:
		boss_num = self.get_in_boss_num(group_id, qqid)
		if not boss_num :
			raise GroupError('你都没申请出刀，下啥子树啊 (╯‵□′)╯︵┻━┻')
		if not handler.state.get(boss_num, qqid).tree:
			raise GroupError('你都没挂树，下啥子树啊 (╯‵□′)╯︵┻━┻')
		handler.state.take_off_tree(qqid)
		handler.save()
	elif take_it_type == 1:
		notice = []
		for challenger, info in handler.state.members(boss_num).items():
			if info.tree: notice.append(atqq(challenger))
		if len(notice) > 0:
			send_group_notification(self, group_id, '可以下树惹~ _(:з)∠)_\n'+'\n'.join(notice))
	msg = '下树惹~ _(:з)∠)_'
	if send_web: future_operation(self, group, msg)
	return msg

#检查能否继续挑战下个boss
def check_next_boss(self, group_id:Groupid, boss_num):
	group:Clan_group = get_clan_group(self, group_id)
	boss_cycle = group.boss_cycle
	now_cycle_boss_health = safe_load_json(group.now_cycle_boss_health, {})
	next_cycle_boss_health = safe_load_json(group.next_cycle_boss_health, {})
	if now_cycle_boss_health[boss_num] == 0 and next_cycle_boss_health[boss_num] == 0:
		return False
	if self._level_by_cycle(boss_cycle, group.game_server) != self._level_by_cycle(boss_cycle+1, group.game_server):
		return False
	return True

#申请出刀
@atomic_battle_operation
def apply_for_challenge(self, is_continue, group_id:Groupid, qqid:QQid, boss_num, behalfed = None, send_web=True, *, compensation_seconds=None) :
	"""
	Args:
		is_continue: 是否是补偿刀
		group_id: QQ群号
		qqid: 申请人的QQ号
		boss_num: 几王
		behalfed: 被代刀人的qq号
	"""
	boss_num = validate_boss_number(boss_num)
	group:Clan_group = get_clan_group(self, group_id)
	if group is None:raise GroupNotExist

	behalf = None
	challenger = behalfed and behalfed or qqid
	if behalfed : behalf = qqid
	membership = Clan_member.get_or_none(group_id=group_id, qqid=challenger)
	if membership is None:raise UserNotInGroup

	if self.check_blade(group_id, challenger):
		raise GroupError('你已经申请过了 (╯‵□′)╯︵┻━┻')

	now_cycle_boss_health = safe_load_json(group.now_cycle_boss_health, {})
	if (not check_next_boss(self, group_id, boss_num) 
		and now_cycle_boss_health[boss_num] == 0):
		raise GroupError('只能挑战2个周目内且不跨阶段的同个boss，请等待该周目的boss全部击杀完毕')

	d, _ = pcr_datetime(area = group.game_server)
	challenges:List[Clan_challenge] = Clan_challenge.select().where(
		Clan_challenge.gid == group_id,
		Clan_challenge.qqid == challenger,
		Clan_challenge.bid == group.battle_id,
		Clan_challenge.challenge_pcrdate == d,
	).order_by(Clan_challenge.cid)
	challenges = list(challenges)
	counts = count_blades(challenges)
	if counts.finished >= 3: raise GroupError('今日已出了3次完整刀')
	if compensation_seconds is not None:
		is_continue = True
	if is_continue and counts.compensation <= 0:
		raise GroupError('您没有补偿刀')
	is_continue = counts.choose_compensation(is_continue)
	selected_tail = select_compensation(self, challenges, group.game_server, compensation_seconds) if compensation_seconds is not None else None
	
	nik = self._get_nickname_by_qqid(challenger)
	info = [f'{nik}已开始挑战boss，剩最后几秒的时候记得暂停报伤害哦~']
	handler = ChallengeHandler(group)
	application = handler.state.apply(boss_num, challenger, is_continue, behalf)
	if selected_tail:
		application.compensation_tail_id = selected_tail.cid
		info.append(f'已选定补偿：{compensation_seconds}s')
	handler.save()

	self.challenger_info_small(group, boss_num, info)
	info = '\n'.join(info)
	if send_web: future_operation(self, group, f'{nik}申请挑战{boss_num}王成功')
	return info

#取消申请出刀
@atomic_battle_operation
def cancel_blade(self, group_id: Groupid, qqid: QQid, boss_num=0, cancel_type=1, send_web=True):
	"""
	Args:
		group_id: QQ群号
		qqid: 需要进行操作的QQ号
		cancel_type: 取消类型：0取消全部 1取消特定qq号 2取消特定boss
		send_web:是否更新web面板数据
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	msg = '？'
	if group.challenging_member_list == None:
		raise GroupError('目前没有人正在挑战这个boss')
	handler = ChallengeHandler(group)
	if cancel_type == 0 :
		handler.state = ChallengeState()
		handler.save(empty_as_none=True)
		msg = '已取消所有'
	elif cancel_type == 1 :
		_boss_num = self.get_in_boss_num(group_id, qqid)
		if not _boss_num : raise GroupError('你都没申请出刀，取啥子消啊 (╯‵□′)╯︵┻━┻')
		handler.state.remove_member(qqid)
		handler.save(empty_as_none=True)
		msg = '取消申请出刀成功'
	elif boss_num != 0 and cancel_type == 2:
		if str(boss_num) not in handler.state.applications: return
		handler.state.remove_boss(boss_num)
		handler.save()

	if send_web: future_operation(self, group, msg)
	return msg

#检查是否已申请出刀
def check_blade(self, group_id: Groupid, qqid: QQid):
	"""
	返回True即已申请出刀，返回False为未申请出刀
	Args:
		group_id: QQ群号
		qqid: 需要进行操作的QQ号
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	return ChallengeState.from_json(group.challenging_member_list).find_member(qqid) is not None

#获取boss_num
def get_in_boss_num(self, group_id, qqid):
	"""
	获取已申请出刀的qqid正在挑战哪个王（挂在哪棵树（不是）），返回False为未找到
	Args:
		group: 公会群对象
		qqid: 需要进行操作的QQ号
	"""
	group:Clan_group = get_clan_group(self, group_id)
	found = ChallengeState.from_json(group.challenging_member_list).find_member(qqid)
	return found[0] if found is not None else False


#SL
@atomic_battle_operation
def save_slot(self, group_id: Groupid, qqid: QQid,
				only_check: bool = False,
				clean_flag: bool = False):
	"""
	记录今天的sl情况

	Args:
		group_id: QQ群号
		qqid: 需要进行操作的QQ号
		todaystatus: 今日状态
		only_check: 是否只查询
		clean_flag: 是否取消sl
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	membership = Clan_member.get_or_none(group_id = group_id, qqid = qqid)
	if membership is None: raise UserNotInGroup
	today, _ = pcr_datetime(group.game_server)
	if clean_flag:
		if membership.last_save_slot != today: raise UserError('您今天还没有SL过')
		membership.last_save_slot = 0
		membership.save()
		return '已取消SL。若已申请/挂树，需重新报告。'
	if only_check:
		return (membership.last_save_slot == today)
	if membership.last_save_slot == today:
		raise UserError('您今天已经SL过了，该不会退游戏了吧？ Σ(っ °Д °;)っ')
	membership.last_save_slot = today


	if self.check_tree(group_id, qqid):
		try:
			self.take_it_of_the_tree(group_id, qqid)
		except:
			pass

	if self.check_blade(group_id, qqid):
		try:
			self.cancel_blade(group_id, qqid)
		except:
			pass

	membership.save()

	# refresh
	self.get_member_list(group_id, nocache = True)
	return '已记录SL。若已申请/挂树，需重新报告。 Σ(っ °Д °;)っ'

#记录伤害/清空伤害
@atomic_battle_operation
def report_hurt(self, s, hurt, group_id:Groupid, qqid:QQid, clean_type = 0, message=None):
	"""
	记录/清空出刀暂停后，成员报的伤害

	Args:
		s: 秒
		hurt: 伤害
		group_id: QQ群号
		qqid: 需要进行操作的QQ号
		clean_type: 清理类型 0不清理(记录伤害) 1清特定玩家
		message: 报伤害留言，重新报伤害或取消时覆盖/清空
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	boss_num = self.get_in_boss_num(group_id, qqid)
	if clean_type != 2 and not boss_num:
		raise GroupError('你都没申请出刀，报啥子伤害啊 (╯‵□′)╯︵┻━┻')

	ret_msg = ''
	handler = ChallengeHandler(group)
	if clean_type == 0:
		handler.state.report_damage(qqid, s, hurt, message)
		ret_msg = '已记录伤害，小心不要手滑哦~ ♪(´▽｀)'
	elif clean_type == 1:
		if handler.state.get(boss_num, qqid).damage == 0:
			ret_msg = '您还没有报伤害呢'
		else:
			handler.state.report_damage(qqid, 0, 0)
			ret_msg = '取消成功~'

	handler.save()
	return ret_msg

#单个boss信息
def challenger_info_small(self, group:Clan_group, boss_num, msg:List = None):
	"""
	Args:
		group: 公会信息对象
		boss_num: 几王
	"""
	now_health = safe_load_json(group.now_cycle_boss_health)[boss_num]
	next_health = safe_load_json(group.next_cycle_boss_health)[boss_num]

	challenging_list = safe_load_json(group.challenging_member_list)
	if challenging_list and (boss_num in challenging_list): 
		challenging_list = challenging_list[boss_num]
	else:
		challenging_list = None

	real_health = next_health if now_health == 0 else now_health
	real_health_str = '{:,}'.format(real_health)
	cycle = group.boss_cycle + 1 if now_health == 0 else group.boss_cycle
	if not msg: msg = []
	msg.append(f'{cycle}周目{boss_num}王，剩余{real_health_str}血')
	if now_health == 0 and not check_next_boss(self, group.group_id, boss_num):
		msg.append(f'该boss无法继续挑战')
		return msg
	elif not challenging_list or len(challenging_list) == 0:
		msg.append(f'目前无人在挑战这个boss')
		return msg
	else:
		msg.append(f'当前有{len(challenging_list)}人正在挑战这个boss')

	if challenging_list:
		msg.append('--------------------')
		for challenger, info in challenging_list.items():
			temp_msg = f'->{self._get_nickname_by_qqid(int(challenger))}'
			if info['is_continue']:
				temp_msg += '(补偿)'
			if info['behalf']:
				behalf = self._get_nickname_by_qqid(info['behalf'])
				temp_msg += f'({behalf}代刀)'
			if (0 if info['damage'] is None else info['damage']) > 0:
				temp_msg += f', 剩{info["s"]}秒，打了{info["damage"]}万伤害'
				if info.get('damage_message'):
					temp_msg += f'，留言：{info["damage_message"]}'
			if info['tree']:
				temp_msg += ', 已挂树'
			msg.append(temp_msg)
		msg.append('--------------------')

	return msg

#总出刀信息
def challenger_info(self, group_id):
	"""
	Args:
		group: 公会信息对象
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None : raise GroupNotExist
	date, _ = pcr_datetime(area = group.game_server)
	challenges:List[Clan_challenge] = Clan_challenge.select().where(
		Clan_challenge.gid == group_id,
		Clan_challenge.bid == group.battle_id,
		Clan_challenge.challenge_pcrdate == date,
	).order_by(Clan_challenge.cid)
	end_blade_qqid = {}         #按出刀顺序保存每个成员未使用的补偿返秒
	for c in pending_compensations(self, challenges, group.game_server):
		seconds = effective_return_seconds(self, c.boss_cycle, group.game_server, c.return_seconds)
		end_blade_qqid.setdefault(c.qqid, []).append(seconds)

	finish_challenge_count = sum(bool(c.boss_health_remain or c.is_continue) for c in challenges)

	half_challenge_list:Dict[str, Any] = {"style-background-color": (240,240,240)}
	for qqid, seconds in end_blade_qqid.items():
		return_times = ' / '.join((f'{s}s' if s is not None else '?s') +
			(f' ×{count}' if count > 1 else '') for s, count in Counter(seconds).items())
		half_challenge_list[str(qqid)] = f'{self._get_nickname_by_qqid(qqid)[:4]} {return_times}'

	challenging_list = safe_load_json(group.challenging_member_list)
	group_boss_data = self._boss_data_dict(group)
	boss_state_image_list:List[Union[Image.Image, BossStatusImageCore]] = []
	subscribe_handler = SubscribeHandler(group=group)
	
	for boss_num in range(1,6):
		this_boss_data = group_boss_data[boss_num]
		boss_num_str = str(boss_num)
		extra_info = {
			"预约":{"style-background-color": (179, 229, 252)},
			"挑战":{"style-background-color": (255, 249, 196)},
		}
		if challenging_list and boss_num_str in challenging_list:
			for challenger, info in challenging_list[boss_num_str].items():
				challenger = str(challenger)
				challenger_nickname = self._get_nickname_by_qqid(challenger)[:4]
				challenger_msg = challenger_nickname
				if info['is_continue']:
					challenger_msg += '(补)'
				if info['behalf']:
					behalf = self._get_nickname_by_qqid(info['behalf'])[:4]
					challenger_msg += f'({behalf}代)'
				if (0 if info['damage'] is None else info['damage']) > 0:
					challenger_msg += f'@{info["s"]}s,{info["damage"]}w'
					if info.get('damage_message'):
						challenger_msg += f':{info["damage_message"]}'
				if info['tree']:
					challenger_msg += '(挂树)'
					if "挂树" not in extra_info:
						extra_info["挂树"] = {"style-background-color": (255, 205, 210)}
					extra_info["挂树"][challenger] = challenger_nickname
				extra_info["挑战"][challenger] = challenger_msg
		
		if boss_num in subscribe_handler.data:
			subscribe_list = subscribe_handler.data[boss_num]
			for user_id, note in subscribe_list.items():
				extra_info["预约"][str(user_id)] = self._get_nickname_by_qqid(user_id)[:4] + (f":{note}" if note else "")

		boss_state_image_list.append(BossStatusImageCore(
			this_boss_data['cycle'], 
			this_boss_data["health"],
			this_boss_data["full_health"],
			boss_num_str + '-' + this_boss_data["name"],
			this_boss_data["icon_id"],
			extra_info,
			this_boss_data['is_next']
		))
	level_cycle = self._level_by_cycle(group.boss_cycle, group.game_server)
	try:
		_bg_color = [(132, 1, 244), (115, 166, 231), (206, 105, 165), (206, 80, 66), (181, 105, 206)][level_cycle]
	except IndexError:
		_bg_color = (181, 105, 206)
	process_image = get_process_image(
		[
			GroupStateBlock(
				title_text="完整刀",
				data_text=str(finish_challenge_count),
				title_color=(0, 0, 0),
				data_color=(255, 0, 0),
				background_color=(255, 205, 210),
			),
			GroupStateBlock(
				title_text="阶段",
				data_text=chr(66+level_cycle),
				title_color=(255, 255, 255),
				data_color=(255, 255, 255),
				background_color=_bg_color,
			),
		],
		{"补偿": half_challenge_list}
	)
	result_image = generate_combind_boss_state_image([process_image, *boss_state_image_list])
	if result_image.mode != "RGB":
		result_image = result_image.convert("RGB")
	bio = BytesIO()
	result_image.save(bio, format='JPEG', quality=95)
	result_image.close()
	base64_str = 'base64://' + base64.b64encode(bio.getvalue()).decode()
	bio.close()
	return f"[CQ:image,file={base64_str}]"

#出刀记录
def challenge_record(self, group_id):
	group:Clan_group = get_clan_group(self, group_id)
	if group is None : raise GroupNotExist
	date, _ = pcr_datetime(area = group.game_server)
	members:List[Clan_member] = Clan_member.select().where(Clan_member.group_id == group_id)

	total_blade_num = 0				#总出刀数
	total_continue_blade_num = 0	#总补偿刀数量
	zero_blade_members = []			#一刀没出的成员
	blade_list = {}
	for member in members:
		challenge_records:List[Clan_challenge] = Clan_challenge.select().where(
			Clan_challenge.gid == group_id,
			Clan_challenge.bid == group.battle_id,
			Clan_challenge.challenge_pcrdate == date,
			Clan_challenge.qqid == member.qqid
		).order_by(Clan_challenge.cid)
		if len(challenge_records) != 0:
			member_num = 0			#单个成员出刀数
			continue_blade_num = 0	#单个成员剩余补偿刀数量
			for c in challenge_records:
				if c.boss_health_remain == 0 and not c.is_continue:	#完整刀收尾算0.5刀
					member_num += 0.5
					continue_blade_num += 1
				elif c.is_continue:	#补偿刀算0.5刀
					member_num += 0.5
					continue_blade_num -= 1
				else: member_num += 1
			total_blade_num += member_num
			total_continue_blade_num += continue_blade_num
			if member_num not in blade_list: blade_list[member_num] = 1
			else: blade_list[member_num] += 1
		else:
			zero_blade_members.append(member.qqid)

	back_msg = []
	back_msg.append(f"待出补偿刀数量：{total_continue_blade_num}")
	back_msg.append(f"已出0刀的成员数量：{len(zero_blade_members)}")
	for i in range(len(zero_blade_members)):
		name = self._get_nickname_by_qqid(zero_blade_members[i])
		back_msg.append(f"{i == len(zero_blade_members)-1 and '┖' or '┣'}{name}")
	for blade_num in blade_list.keys():
		back_msg.append(f"已出{blade_num}刀：{blade_list[blade_num]}")
	back_msg.append(f"今天已出 {total_blade_num}/{len(members)*3}")
	return '\n'.join(back_msg)


##获取报告
@timed_cached_func(max_len=64, max_age_seconds=10, ignore_self=True)
def get_report(self,
				group_id: Groupid,
				battle_id: Union[str, int, None],
				qqid: Optional[QQid] = None,
				pcrdate: Optional[Pcr_date] = None
				) -> ClanBattleReport:
	"""
	get the records

	Args:
		group_id: QQ群号
		qqid: user id of report
		pcrdate: pcrdate of report
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	report = []
	expressions = [
		Clan_challenge.gid == group_id,
	]
	if battle_id is None:
		battle_id = group.battle_id
	if isinstance(battle_id, str):
		if battle_id == 'all':
			pass
		else:
			raise InputError(
				f'unexceptd value "{battle_id}" for battle_id')
	else:
		expressions.append(Clan_challenge.bid == battle_id)
	if qqid is not None:
		expressions.append(Clan_challenge.qqid == qqid)
	if pcrdate is not None:
		expressions.append(Clan_challenge.challenge_pcrdate == pcrdate)
	for c in Clan_challenge.select().where(
		*expressions
	).order_by(Clan_challenge.cid):
		report.append({
			'record_id': c.cid,
			'consumed_tail_id': c.consumed_tail_id,
			'recorded_return_seconds': c.return_seconds,
			'battle_id': c.bid,
			'qqid': c.qqid,
			'challenge_time': pcr_timestamp(
				c.challenge_pcrdate,
				c.challenge_pcrtime,
				group.game_server,
			),
			'challenge_pcrdate': c.challenge_pcrdate,
			'challenge_pcrtime': c.challenge_pcrtime,
			'cycle': c.boss_cycle,
			'boss_num': c.boss_num,
			'health_remain': c.boss_health_remain,
			'damage': c.challenge_damage,
			'is_continue': c.is_continue,
			'message': c.message,
			'return_seconds': effective_return_seconds(self, c.boss_cycle, group.game_server, c.return_seconds)
				if c.boss_health_remain == 0 and not c.is_continue else None,
			'behalf': c.behalf,
		})
	return report

#从会战记录里获取成员列表
@timed_cached_func(max_len=64, max_age_seconds=10, ignore_self=True)
def get_battle_member_list(self,
							group_id: Groupid,
							battle_id: Union[str, int, None],
							):
	"""
	Args:
		group_id: QQ群号
		battle_id: 会战记录编号
	"""
	group:Clan_group = get_clan_group(self, group_id)
	if group is None: raise GroupNotExist
	expressions = [
		Clan_challenge.gid == group_id,
	]
	if battle_id is None:
		battle_id = group.battle_id
	if isinstance(battle_id, str):
		if battle_id == 'all':
			pass
		else:
			raise InputError(
				f'unexceptd value "{battle_id}" for battle_id')
	else:
		expressions.append(Clan_challenge.bid == battle_id)
	member_list = []
	for u in Clan_challenge.select(
		Clan_challenge.qqid,
		User.nickname,
	).join(
		User,
		on=(Clan_challenge.qqid == User.qqid),
		attr='user',
	).where(
		*expressions
	).distinct():
		member_list.append({
			'qqid': u.qqid,
			'nickname': u.user.nickname,
		})
	return member_list

#获取并刷新成员列表
@timed_cached_func(max_len=16, max_age_seconds=3600, ignore_self=True)
def get_member_list(self, group_id: Groupid) -> List[Dict[str, Any]]:
	"""
	获取并刷新成员列表

	Args:
		group_id: QQ群号
	"""
	member_list = []
	for user in User.select(
		User, Clan_member,
	).join(
		Clan_member,
		on=(User.qqid == Clan_member.qqid),
		attr='clan_member',
	).where(
		Clan_member.group_id == group_id,
		User.deleted == False,
	):
		member_list.append({
			'qqid': user.qqid,
			'nickname': user.nickname,
			'sl': user.clan_member.last_save_slot,
		})
	return member_list
