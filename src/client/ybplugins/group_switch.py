"""Persist per-group service switches and apply them immediately."""
import json
import os
import tempfile

from .permissions import can_manage_group_command


def switch_group_service(settings, msg):
    command = msg['raw_message'].strip()
    if command not in ('开启 yobot2', '关闭 yobot2'):
        return None
    if msg['message_type'] != 'group':
        return '此功能仅可用于群聊'
    if not can_manage_group_command(msg, settings):
        return '只有群主、群管理员或机器人主人可以开关本群服务'

    enabled = command == '开启 yobot2'
    group_id = msg['group_id']
    key = 'white-list-group' if settings['white_list_mode'] else 'black-list-group'
    listed = enabled if settings['white_list_mode'] else not enabled
    groups = [group for group in settings[key] if group != group_id]
    if listed:
        groups.append(group_id)
    saved = {k: v for k, v in settings.items() if k not in ('dirname', 'verinfo')}
    saved[key] = groups
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=settings['dirname'], delete=False) as output:
            temporary_path = output.name
            json.dump(saved, output, ensure_ascii=False, indent=4)
        os.replace(temporary_path, os.path.join(settings['dirname'], 'yobot_config.json'))
    except OSError:
        return '保存配置失败，本群服务状态未改变，请检查配置目录权限'
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    settings[key] = groups
    return '已开启本群 yobot2 服务' if enabled else '已关闭本群 yobot2 服务，可发送“开启 yobot2”恢复'
