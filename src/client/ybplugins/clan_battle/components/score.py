"""Render the shared web performance ranking for the group command."""
import base64
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from ..exception import GroupNotExist, InputError
from ..util import pcr_datetime
from .image_engine import FONTS
from .performance import report, PerformanceError


STAGE_COLORS = ('#eef5ff', '#eef8f3', '#f5f1fc')


def performance_title(group, data):
    # Archive dates are PCR calendar days, not the date the image is requested.
    day = max((row['date'] for row in data['records']),
              default=pcr_datetime(group.game_server)[0])
    month = datetime.fromtimestamp(day * 86400, timezone.utc).strftime('%Y年%m月')
    return f'{group.group_name or group.group_id}-{month}-公会战业绩表'


def performance_cells(data):
    count = max(3, len(data['config']['stages']))
    labels = [chr(66 + index) if index < 25 else f'阶段 {index + 1}' for index in range(count)]
    headers = ['排名', '成员', '总刀数'] + [label + '阶段' for label in labels]
    headers += [label + '得分' for label in labels] + ['总业绩分']
    def points(value):
        return format(value, '.5f').rstrip('0').rstrip('.')
    rows = [[str(index), row['nickname'], str(row['total_blades'])]
            + [str(value) for value in row['stage_blades']]
            + [points(value) for value in row['stage_scores']] + [points(row['score'])]
            for index, row in enumerate(data['ranking'], 1)]
    colors = ['#ffffff', '#ffffff', '#f5f7fb']
    colors += [STAGE_COLORS[index % 3] for index in range(count)] * 2 + ['#e8effb']
    return headers, rows, colors


def render_performance_image(title, data):
    headers, rows, colors = performance_cells(data)
    widths = [70, 240] + [125] * (len(headers) - 3) + [150]
    padding, row_height, table_top = 24, 48, 94
    image = Image.new('RGB', (sum(widths) + padding * 2,
                              table_top + row_height * (max(1, len(rows)) + 1) + padding), '#f4f7fc')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONTS, 22)
    title_font = ImageFont.truetype(FONTS, 30)

    def fit(text, selected_font, available):
        text = str(text).replace('\n', ' ').replace('\r', ' ')
        if draw.textbbox((0, 0), text, font=selected_font)[2] <= available:
            return text
        while text and draw.textbbox((0, 0), text + '…', font=selected_font)[2] > available:
            text = text[:-1]
        return text + '…'

    draw.text((padding, 26), fit(title, title_font, image.width - padding * 2),
              font=title_font, fill='#263650')
    visible_rows = rows or [['—', '暂无成员'] + ['—'] * (len(headers) - 2)]
    for row_index, cells in enumerate([headers] + visible_rows):
        x, y = padding, table_top + row_index * row_height
        for column, (cell, width, color) in enumerate(zip(cells, widths, colors)):
            background = '#f1f5fc' if row_index == 0 and column < 2 else color
            draw.rectangle((x, y, x + width - 1, y + row_height - 1), fill=background)
            text = fit(cell, font, width - 20)
            box = draw.textbbox((0, 0), text, font=font)
            text_x = x + 12 if column == 1 else x + (width - (box[2] - box[0])) / 2 - box[0]
            text_y = y + (row_height - (box[3] - box[1])) / 2 - box[1]
            draw.text((text_x, text_y), text, font=font, fill='#263650')
            draw.line((x, y + row_height - 1, x + width - 1, y + row_height - 1), fill='#e2e8f2')
            x += width
    return image


def score_table(self, group_id):
    group = self.get_clan_group(group_id=group_id)
    if group is None:
        raise GroupNotExist
    try:
        data = report(self, group, group.battle_id)
        image = render_performance_image(performance_title(group, data), data)
    except (PerformanceError, OSError) as exc:
        raise InputError(f'业绩表生成失败：{exc}') from exc
    output = BytesIO()
    image.save(output, format='PNG')
    encoded = base64.b64encode(output.getvalue()).decode('ascii')
    return f'[CQ:image,file=base64://{encoded}]'
