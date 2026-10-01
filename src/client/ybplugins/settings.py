import asyncio
import json
import logging
import os
import sys
from pathlib import Path
import copy
import tempfile
from urllib.parse import urljoin

from playhouse.shortcuts import model_to_dict
from quart import Quart, jsonify, redirect, request, session, url_for

from .boss_data import create_session, fetch_boss_data
from .permissions import is_global_owner
from .templating import render_template
from .ybdata import Clan_group, User

_returned_query_fileds = [
    User.qqid,
    User.nickname,
    User.clan_group_id,
    User.authority_group,
    User.last_login_time,
    User.last_login_ipaddr,
]

logger = logging.getLogger(__name__)

class Setting:
    Passive = False
    Active = False
    Request = True

    def __init__(self,
                 glo_setting,
                 bot_api,
                 boss_id_name,
                 *args, **kwargs):
        self.setting = glo_setting
        self.boss_id_name = boss_id_name
        config_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
        with (config_root / 'packedfiles' / 'default_config.json').open(encoding='utf-8') as config_file:
            self.configurable_keys = frozenset(json.load(config_file))

    def _get_users_json(self, req_querys: dict):
        querys = []
        if req_querys.get('qqid'):
            querys.append(
                User.qqid == req_querys['qqid']
            )
        if req_querys.get('clan_group_id'):
            querys.append(
                User.clan_group_id == req_querys['clan_group_id']
            )
        if req_querys.get('authority_group'):
            querys.append(
                User.authority_group == req_querys['authority_group']
            )
        users = User.select(
            User.qqid,
            User.nickname,
            User.clan_group_id,
            User.authority_group,
            User.last_login_time,
            User.last_login_ipaddr,
        ).where(
            User.deleted == False,
            *querys,
        ).paginate(
            page=req_querys['page'],
            paginate_by=req_querys['page_size']
        )
        return json.dumps({
            'code': 0,
            'data': [model_to_dict(u, only=_returned_query_fileds) for u in users],
        })

    def register_routes(self, app: Quart):

        @app.route(
            urljoin(self.setting['public_basepath'], 'admin/setting/'),
            methods=['GET'])
        async def yobot_setting():
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            if not is_global_owner(user):
                if not user.authority_group >= 100:
                    uathname = '公会战管理员'
                else:
                    uathname = '成员'
                return await render_template(
                    'unauthorized.html',
                    limit='主人',
                    uath=uathname,
                )
            return await render_template(
                'admin/setting.html',
            )

        @app.route(
            urljoin(self.setting['public_basepath'], 'admin/setting/api/'),
            methods=['GET', 'PUT'])
        async def yobot_setting_api():
            if 'yobot_user' not in session:
                return jsonify(
                    code=10,
                    message='Not logged in',
                )
            user = User.get_by_id(session['yobot_user'])
            if not is_global_owner(user):
                return jsonify(
                    code=11,
                    message='Insufficient authority',
                )
            if request.method == 'GET':
                settings = {key: value for key, value in self.setting.items()
                            if key in self.configurable_keys}
                boss_id_name = self.boss_id_name.copy()
                settings.pop('host', None)
                settings.pop('port', None)
                settings.pop('access_token', None)
                return jsonify(
                    code=0,
                    message='success',
                    settings=settings,
                    boss_id_name=boss_id_name
                )
            elif request.method == 'PUT':
                req = await request.get_json()
                if not isinstance(req, dict):
                    return jsonify(code=30, message='Invalid payload')
                if not session.get('csrf_token') or req.get('csrf_token') != session.get('csrf_token'):
                    return jsonify(
                        code=15,
                        message='Invalid csrf_token',
                    )
                new_setting = req.get('setting')
                if not isinstance(new_setting, dict):
                    return jsonify(
                        code=30,
                        message='Invalid payload',
                    )
                if set(new_setting) - self.configurable_keys:
                    return jsonify(code=30, message='Unsupported setting fields')
                self.setting.update(new_setting)
                save_setting = self.setting.copy()
                del save_setting['dirname']
                del save_setting['verinfo']
                config_path = os.path.join(
                    self.setting['dirname'], 'yobot_config.json')
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(save_setting, f, indent=4)
                return jsonify(
                    code=0,
                    message='success',
                )

        @app.route(
            urljoin(self.setting['public_basepath'], 'admin/pool-setting/'),
            methods=['GET'])
        async def yobot_pool_setting():
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            if not is_global_owner(user):
                if not user.authority_group >= 100:
                    uathname = '公会战管理员'
                else:
                    uathname = '成员'
                return await render_template(
                    'unauthorized.html',
                    limit='主人',
                    uath=uathname,
                )
            return await render_template('admin/pool-setting.html')

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'admin/pool-setting/api/'),
            methods=['GET', 'PUT'])
        async def yobot_pool_setting_api():
            if 'yobot_user' not in session:
                return jsonify(
                    code=10,
                    message='Not logged in',
                )
            user = User.get_by_id(session['yobot_user'])
            if not is_global_owner(user):
                return jsonify(
                    code=11,
                    message='Insufficient authority',
                )
            if request.method == 'GET':
                with open(os.path.join(self.setting['dirname'], 'pool3.json'),
                          'r', encoding='utf-8') as f:
                    settings = json.load(f)
                return jsonify(
                    code=0,
                    message='success',
                    settings=settings,
                )
            elif request.method == 'PUT':
                req = await request.get_json()
                if not isinstance(req, dict):
                    return jsonify(code=30, message='Invalid payload')
                if not session.get('csrf_token') or req.get('csrf_token') != session.get('csrf_token'):
                    return jsonify(
                        code=15,
                        message='Invalid csrf_token',
                    )
                new_setting = req.get('setting')
                if not isinstance(new_setting, dict):
                    return jsonify(
                        code=30,
                        message='Invalid payload',
                    )
                with open(os.path.join(self.setting['dirname'], 'pool3.json'),
                          'w', encoding='utf-8') as f:
                    json.dump(new_setting, f, ensure_ascii=False, indent=2)
                return jsonify(
                    code=0,
                    message='success',
                )

        @app.route(
            urljoin(self.setting['public_basepath'], 'admin/users/'),
            methods=['GET'])
        async def yobot_users_managing():
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            if not is_global_owner(user):
                if not user.authority_group >= 100:
                    uathname = '公会战管理员'
                else:
                    uathname = '成员'
                return await render_template(
                    'unauthorized.html',
                    limit='主人',
                    uath=uathname,
                )
            return await render_template('admin/users.html')

        @app.route(
            urljoin(self.setting['public_basepath'], 'admin/users/api/'),
            methods=['POST'])
        async def yobot_users_api():
            if 'yobot_user' not in session:
                return jsonify(
                    code=10,
                    message='Not logged in',
                )
            user = User.get_by_id(session['yobot_user'])
            if not is_global_owner(user):
                return jsonify(
                    code=11,
                    message='Insufficient authority',
                )
            try:
                req = await request.get_json()
                if req is None:
                    return jsonify(
                        code=30,
                        message='Invalid payload',
                    )
                if not isinstance(req, dict):
                    return jsonify(code=30, message='Invalid payload')
                if not session.get('csrf_token') or req.get('csrf_token') != session.get('csrf_token'):
                    return jsonify(
                        code=15,
                        message='Invalid csrf_token',
                    )
                action = req['action']
                if action == 'get_data':
                    return await asyncio.get_event_loop().run_in_executor(
                        None,
                        self._get_users_json,
                        req['querys'],
                    )

                elif action == 'modify_user':
                    data = req['data']
                    m_user: User = User.get_or_none(qqid=data['qqid'])
                    if ((m_user.authority_group <= user.authority_group) or
                            (data.get('authority_group', 999)) <= user.authority_group):
                        return jsonify(code=12, message='Exceed authorization is not allowed')
                    if data.get('authority_group') == 1:
                        self.setting['super-admin'].append(data['qqid'])
                        save_setting = self.setting.copy()
                        del save_setting['dirname']
                        del save_setting['verinfo']
                        config_path = os.path.join(
                            self.setting['dirname'], 'yobot_config.json')
                        with open(config_path, 'w', encoding='utf-8') as f:
                            json.dump(save_setting, f, indent=4)
                    if m_user is None:
                        return jsonify(code=21, message='user not exist')
                    for key in data.keys():
                        setattr(m_user, key, data[key])
                    m_user.save()
                    return jsonify(code=0, message='success')
                elif action == 'delete_user':
                    user = User.get_or_none(qqid=req['data']['qqid'])
                    if user is None:
                        return jsonify(code=21, message='user not exist')
                    user.clan_group_id = None
                    user.authority_group = 999
                    user.password = None
                    user.deleted = True
                    user.save()
                    return jsonify(code=0, message='success')
                else:
                    return jsonify(code=32, message='unknown action')
            except KeyError as e:
                return jsonify(code=31, message=str(e))

        @app.route(
            urljoin(self.setting['public_basepath'], 'admin/groups/'),
            methods=['GET'])
        async def yobot_groups_managing():
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            if not is_global_owner(user):
                if not user.authority_group >= 100:
                    uathname = '公会战管理员'
                else:
                    uathname = '成员'
                return await render_template(
                    'unauthorized.html',
                    limit='主人',
                    uath=uathname,
                )
            return await render_template('admin/groups.html')

        @app.route(
            urljoin(self.setting['public_basepath'], 'admin/groups/api/'),
            methods=['POST'])
        async def yobot_groups_api():
            if 'yobot_user' not in session:
                return jsonify(
                    code=10,
                    message='Not logged in',
                )
            user = User.get_by_id(session['yobot_user'])
            if not is_global_owner(user):
                return jsonify(
                    code=11,
                    message='Insufficient authority',
                )
            try:
                req = await request.get_json()
                if req is None:
                    return jsonify(
                        code=30,
                        message='Invalid payload',
                    )
                if not isinstance(req, dict):
                    return jsonify(code=30, message='Invalid payload')
                if not session.get('csrf_token') or req.get('csrf_token') != session.get('csrf_token'):
                    return jsonify(
                        code=15,
                        message='Invalid csrf_token',
                    )
                action = req['action']
                if action == 'get_data':
                    groups = []
                    for group in Clan_group.select().where(
                        Clan_group.deleted == False,
                    ):
                        groups.append({
                            'group_id': group.group_id,
                            'group_name': group.group_name,
                            'game_server': group.game_server,
                        })
                    return jsonify(code=0, data=groups)
                if action == 'drop_group':
                    User.update({
                        User.clan_group_id: None,
                    }).where(
                        User.clan_group_id == req['group_id'],
                    ).execute()
                    Clan_group.delete().where(
                        Clan_group.group_id == req['group_id'],
                    ).execute()
                    return jsonify(code=0, message='ok')
                else:
                    return jsonify(code=32, message='unknown action')
            except KeyError as e:
                return jsonify(code=31, message=str(e))


        @app.route(urljoin(self.setting['public_basepath'], 'admin/setting/auto_get_boss_data/'), methods=['POST'])
        async def auto_get_boss_data():
            if 'yobot_user' not in session:
                return jsonify(code=10, message='Not logged in')
            user = User.get_or_none(qqid=session['yobot_user'])
            if not is_global_owner(user):
                return jsonify(code=11, message='Insufficient authority')
            req = await request.get_json()
            if not isinstance(req, dict):
                return jsonify(code=30, message='Invalid payload')
            if not session.get('csrf_token') or req.get('csrf_token') != session.get('csrf_token'):
                return jsonify(code=15, message='Invalid csrf_token' )

            use_latest = req.get('use_latest', False)
            if not isinstance(use_latest, bool):
                return jsonify(code=30, message='Invalid use_latest')
            new_setting = copy.deepcopy(self.setting)
            new_names = copy.deepcopy(self.boss_id_name)
            messages, updated, icon_ids = [], [], set()
            async with create_session() as client:
                servers = [server for server in self.setting['boss'] if server in ('cn', 'jp')]
                results = await asyncio.gather(
                    *(fetch_boss_data(client, server, use_latest) for server in servers),
                    return_exceptions=True,
                )
                for server, result in zip(servers, results):
                    if isinstance(result, Exception):
                        logger.warning('Boss data update failed for %s: %s', server, result)
                        detail = str(result) or type(result).__name__
                        messages.append(f'{server} 获取失败：{detail}')
                        continue
                    new_setting['boss'][server] = result.health
                    new_setting['boss_id'][server] = result.ids
                    new_setting['level_by_cycle'][server] = result.cycles
                    for number, names in result.names.items():
                        new_names.setdefault(number, {}).update(names)
                        icon_ids.update(names)
                    updated.append(server)
                    messages.append(f'{server} 已获取 {result.year}-{result.month:02d} Boss 数据')
            if not updated:
                return jsonify(code=32, message='\n'.join(messages) or '没有支持自动获取的服务器')

            save_setting = {key: value for key, value in new_setting.items()
                            if key not in ('dirname', 'verinfo')}
            try:
                # Names are additive; install them before configuration references new IDs.
                save_json(os.path.join(self.setting['dirname'], 'BossIdAndName.json'), new_names)
                save_json(os.path.join(self.setting['dirname'], 'yobot_config.json'), save_setting)
            except OSError:
                logger.exception('Cannot save downloaded Boss data')
                return jsonify(code=31, message='Boss 数据保存失败，原配置未更新')
            for key in ('boss', 'boss_id', 'level_by_cycle'):
                self.setting[key].update({server: new_setting[key][server] for server in updated})
            self.boss_id_name.update(new_names)

            # Icon failures must not discard a successful data update.
            icon_root = Path(__file__).resolve().parents[1] / 'public' / 'libs' / 'yocool@final' / 'princessadventure' / 'boss_icon'
            missing = [boss_id for boss_id in icon_ids if not (icon_root / f'{boss_id}.webp').exists()]
            if missing:
                try:
                    await asyncio.wait_for(download_icons(icon_root, missing), timeout=10)
                except Exception:
                    logger.warning('Boss data saved but some icons could not be downloaded', exc_info=True)
                    messages.append('Boss 数据已保存，部分头像下载失败')
            return jsonify(code=0, message='\n'.join(messages), partial=len(updated) != len(servers))


def save_json(path, data):
    """Replace a complete file without exposing a truncated configuration."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=os.path.dirname(path), delete=False) as target:
            temporary = target.name
            json.dump(data, target, indent=4, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


async def download_icons(root, boss_ids):
    semaphore = asyncio.Semaphore(4)
    async with create_session() as client:
        async def download(boss_id):
            async with semaphore:
                async with client.get(f'https://wthee.xyz/redive/jp/resource/icon/unit/{boss_id}.webp') as response:
                    response.raise_for_status()
                    data = await response.read()
                    if not data.startswith(b'RIFF') or data[8:12] != b'WEBP':
                        raise ValueError('Invalid Boss icon response')
                root.mkdir(parents=True, exist_ok=True)
                (root / f'{boss_id}.webp').write_bytes(data)
        results = await asyncio.gather(*(download(boss_id) for boss_id in boss_ids), return_exceptions=True)
        if any(isinstance(result, Exception) for result in results):
            raise OSError('Some Boss icons could not be downloaded')
