"""Status chips retain long notes and keep following rows below them."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))
from ybplugins.clan_battle.components import image_engine


class ImageNoteTests(unittest.TestCase):
    def test_wrapped_note_preserves_all_characters_and_width(self):
        note = '测试会员@1s,30000w:还有一个ub，先等另一位队员报完伤害再决定是否继续攻击'
        original = image_engine.get_font_image
        seen = []

        def capture(text, size, color):
            seen.append(text)
            return original(text, size, color)

        with patch.object(image_engine, 'get_font_image', side_effect=capture):
            chip = image_engine.user_chips(Image.new('RGBA', (20, 20)), note)
        self.assertEqual(seen[0].replace('\n', ''), note)
        self.assertIn('\n', seen[0])
        self.assertLessEqual(chip.width, 361)
        self.assertGreater(chip.height, 30)
        chip.close()

    def test_long_note_rows_have_enough_vertical_space(self):
        note = '测试会员@1s,30000w:还有一个ub，先等另一位队员报完伤害再决定是否继续攻击'
        rendered = image_engine.chips_list({'10': note, '20': note}, '挑战')
        chip = image_engine.user_chips(Image.new('RGBA', (20, 20)), note)
        self.assertGreaterEqual(rendered.height, chip.height * 2 + 5)
        rendered.close()
        chip.close()
