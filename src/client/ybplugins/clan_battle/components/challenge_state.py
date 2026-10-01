"""Typed application state with the existing database JSON representation."""
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Union

Identifier = Union[str, int]


@dataclass
class ApplicationState:
    is_continue: bool = False
    behalf: Optional[int] = None
    s: Optional[int] = 0
    damage: Optional[int] = 0
    tree: bool = False
    msg: Optional[str] = None
    damage_message: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data):
        known = {'is_continue', 'behalf', 's', 'damage', 'tree', 'msg', 'damage_message'}
        values = {key: deepcopy(value) for key, value in data.items() if key in known}
        values['extra'] = {key: deepcopy(value) for key, value in data.items() if key not in known}
        return cls(**values)

    def to_dict(self):
        result = deepcopy(self.extra)
        result.update(is_continue=self.is_continue, behalf=self.behalf, s=self.s,
                      damage=self.damage, tree=self.tree, msg=self.msg)
        if self.damage_message:
            result['damage_message'] = self.damage_message
        return result


@dataclass
class ChallengeState:
    applications: Dict[str, Dict[str, ApplicationState]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, text: Optional[str]):
        data = json.loads(text) if text else {}
        return cls({str(boss): {str(member): ApplicationState.from_dict(info)
                              for member, info in members.items()}
                    for boss, members in (data or {}).items()})

    def to_json(self) -> str:
        return json.dumps({str(boss): {str(member): info.to_dict()
                                      for member, info in members.items()}
                           for boss, members in self.applications.items()})

    def to_json_or_none(self) -> Optional[str]:
        return self.to_json() if self.applications else None

    def members(self, boss: Identifier) -> Dict[str, ApplicationState]:
        return self.applications.get(str(boss), {})

    def get(self, boss: Identifier, qqid: Identifier) -> Optional[ApplicationState]:
        return self.members(boss).get(str(qqid))

    def find_member(self, qqid: Identifier) -> Optional[Tuple[str, ApplicationState]]:
        for boss, members in self.applications.items():
            if str(qqid) in members:
                return boss, members[str(qqid)]
        return None

    def apply(self, boss: Identifier, qqid: Identifier, is_continue=False, behalf=None) -> ApplicationState:
        if self.find_member(qqid) is not None:
            raise ValueError('Member already has an application')
        info = ApplicationState(is_continue=is_continue, behalf=behalf)
        self.applications.setdefault(str(boss), {})[str(qqid)] = info
        return info

    def remove_member(self, qqid: Identifier) -> Optional[ApplicationState]:
        found = self.find_member(qqid)
        if found is None:
            return None
        boss, info = found
        del self.applications[boss][str(qqid)]
        if not self.applications[boss]:
            del self.applications[boss]
        return info

    def remove_boss(self, boss: Identifier) -> Dict[str, ApplicationState]:
        return self.applications.pop(str(boss), {})

    def _require_member(self, qqid: Identifier) -> ApplicationState:
        found = self.find_member(qqid)
        if found is None:
            raise KeyError(str(qqid))
        return found[1]

    def put_on_tree(self, qqid: Identifier, msg=None) -> ApplicationState:
        info = self._require_member(qqid)
        if info.tree:
            raise ValueError('Member is already on the tree')
        info.tree, info.msg = True, msg
        return info

    def take_off_tree(self, qqid: Identifier) -> ApplicationState:
        info = self._require_member(qqid)
        info.tree, info.msg = False, None
        return info

    def report_damage(self, qqid: Identifier, s, damage, message=None) -> ApplicationState:
        info = self._require_member(qqid)
        info.s, info.damage = s, damage
        info.damage_message = message.strip() or None if message else None
        return info
