import os
import string

from ..exception import GroupNotExist
from ...ybdata import Clan_group
from .performance import report, PerformanceError
from ..exception import InputError


FILE_PATH = os.path.dirname(__file__)

def is_Chinese(word):
	for ch in word:
		if '\u4e00' <= ch <= '\u9fff': return True

#业绩表
def score_table(self, group_id):
	'''
	通过当期数据给成员打分
	'''
	group:Clan_group = self.get_clan_group(group_id=group_id)
	if group is None:raise GroupNotExist

	try:
		ranking = report(self, group, group.battle_id)['ranking']
	except PerformanceError as exc:
		raise InputError(str(exc)) from exc
	member_score_dict = {row['qqid']: row for row in ranking}
	back_msg = []
	for qqid, info in member_score_dict.items():
		name:string = list(self._get_nickname_by_qqid(qqid))
		while len(name) > 5:name.pop()
		a = ''
		if len(name) < 5:
			for i in range(5 - len(name)): a += ' '
			name.append(a)
		a = ''
		for i in name:
			if not is_Chinese(i): a += ' '
		back_msg.append(f"{''.join(name)}{a}     \
分数：{info['score']}     \
整刀：{info['full_blade']}     \
尾刀：{info['end_blade']}     \
小尾刀：{info['small_end_blade']}")

	return self.text_2_pic('\n'.join(back_msg), 450, len(back_msg)*20 + 10, (255, 255, 255), "#000000", 15, (10, 5))
