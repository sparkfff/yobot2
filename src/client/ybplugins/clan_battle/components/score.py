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
    date = datetime.fromtimestamp(day * 86400, timezone.utc)
    month = f'{date.year:04d}年{date.month:02d}月'
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
    widths = [76, 240] + [125] * (len(headers) - 3) + [156]
    padding, row_height, table_top = 36, 58, 142
    table_width = sum(widths)
    table_height = row_height * (max(1, len(rows)) + 1)
    image = Image.new('RGB', (table_width + padding * 2,
                              table_top + table_height + 70), '#edf2fa')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, image.width - 18, image.height - 18),
                           radius=22, fill='#ffffff')
    font = ImageFont.truetype(FONTS, 22)
    header_font = ImageFont.truetype(FONTS, 20)
    score_font = ImageFont.truetype(FONTS, 24)
    caption_font = ImageFont.truetype(FONTS, 17)
    title_font = ImageFont.truetype(FONTS, 32)

    def fit(text, selected_font, available):
        text = str(text).replace('\n', ' ').replace('\r', ' ')
        if draw.textbbox((0, 0), text, font=selected_font)[2] <= available:
            return text
        while text and draw.textbbox((0, 0), text + '…', font=selected_font)[2] > available:
            text = text[:-1]
        return text + '…'

    def centered(text, selected_font, bounds, color):
        left, top, right, bottom = bounds
        text = fit(text, selected_font, right - left - 16)
        box = draw.textbbox((0, 0), text, font=selected_font)
        draw.text((left + (right - left - box[2] + box[0]) / 2 - box[0],
                   top + (bottom - top - box[3] + box[1]) / 2 - box[1]),
                  text, font=selected_font, fill=color)

    centered(title, title_font, (padding, 36, image.width - padding, 86), '#213754')
    caption = f"共 {len(rows)} 名成员 · {sum(row['total_blades'] for row in data['ranking'])} 条报刀"
    centered(caption, caption_font, (padding, 90, image.width - padding, 118), '#76849a')
    draw.rounded_rectangle((image.width // 2 - 28, 126, image.width // 2 + 28, 130),
                           radius=2, fill='#6b92d7')
    header_colors = {'#eef5ff': '#dfebff', '#eef8f3': '#def1e7',
                     '#f5f1fc': '#e9e1f8', '#e8effb': '#d9e5f8'}
    visible_rows = rows or [['—', '暂无成员'] + ['—'] * (len(headers) - 2)]
    for row_index, cells in enumerate([headers] + visible_rows):
        x, y = padding, table_top + row_index * row_height
        for column, (cell, width, color) in enumerate(zip(cells, widths, colors)):
            background = header_colors.get(color, '#f0f3f8') if row_index == 0 else color
            draw.rectangle((x, y, x + width - 1, y + row_height - 1), fill=background)
            selected_font = header_font if row_index == 0 else font
            text_color = '#4a607e' if row_index == 0 else '#34445c'
            if row_index and column == 0 and rows:
                badge_colors = [('#fff1cf', '#a77920'), ('#e7eef9', '#52719f'), ('#f8e7df', '#a46c4e')]
                badge, text_color = badge_colors[row_index - 1] if row_index <= 3 else ('#f2f4f8', '#78859a')
                center_x, center_y = x + width // 2, y + row_height // 2
                draw.ellipse((center_x - 17, center_y - 17, center_x + 17, center_y + 17), fill=badge)
            if row_index and column == len(headers) - 1:
                selected_font, text_color = score_font, '#2d548c'
                draw.rounded_rectangle((x + 12, y + 10, x + width - 12, y + row_height - 10),
                                       radius=9, fill='#dce8fb')
            if column == 1:
                text = fit(cell, selected_font, width - 28)
                box = draw.textbbox((0, 0), text, font=selected_font)
                draw.text((x + 16, y + (row_height - box[3] + box[1]) / 2 - box[1]),
                          text, font=selected_font, fill=text_color)
            else:
                centered(cell, selected_font, (x, y, x + width, y + row_height), text_color)
            if row_index:
                draw.line((x, y + row_height - 1, x + width - 1, y + row_height - 1), fill='#e7edf5')
            draw.line((x + width - 1, y, x + width - 1, y + row_height - 1), fill='#ffffff')
            x += width
    footer = '按已保存权重计算' if data.get('saved') else '按当前默认权重计算'
    draw.text((padding, table_top + table_height + 20), footer, font=caption_font, fill='#8693a7')
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
