"""Schema initialization and migration only ever touch temporary SQLite files."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))
from ybplugins import ybdata


LEGACY_MODELS = [
    ybdata.Admin_key, ybdata.User, ybdata.User_login, ybdata.Clan_group,
    ybdata.Clan_member, ybdata.Clan_group_backups, ybdata.Clan_challenge,
    ybdata.Character,
]


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.filename = str(Path(self.tmp.name) / 'migration.sqlite')
        self.previous_filename = ybdata._db.database
        self.binding = ybdata._db.bind_ctx(
            LEGACY_MODELS + [ybdata.DB_schema, ybdata.Clan_challenge_undo])
        self.binding.__enter__()
        ybdata._db.init(self.filename)
        ybdata._db.connect()

    def tearDown(self):
        ybdata._db.close()
        self.binding.__exit__(None, None, None)
        # Restore configuration without opening or querying the previous file.
        ybdata._db.init(self.previous_filename)
        self.tmp.cleanup()

    def legacy_database(self, version=2):
        ybdata._db.create_tables(
            [model for model in LEGACY_MODELS if model != ybdata.Clan_challenge]
            + [ybdata.DB_schema])
        # Real pre-v4 layout: the current model must not create the new column.
        ybdata._db.execute_sql('''CREATE TABLE clan_challenge (
            cid INTEGER PRIMARY KEY, bid INTEGER NOT NULL, gid INTEGER NOT NULL,
            qqid INTEGER NOT NULL, challenge_pcrdate INTEGER NOT NULL,
            challenge_pcrtime INTEGER NOT NULL, boss_cycle INTEGER NOT NULL,
            boss_num INTEGER NOT NULL, boss_health_remain INTEGER NOT NULL,
            challenge_damage INTEGER NOT NULL, is_continue INTEGER NOT NULL,
            message TEXT, behalf INTEGER)''')
        if version >= 3:
            ybdata.Clan_challenge_undo.create_table()
        ybdata.DB_schema.create(key='version', value=str(version))
        ybdata.User.create(qqid=123, nickname='legacy member')
        ybdata.Clan_group.create(group_id=456, group_name='legacy clan')
        ybdata.Clan_member.create(group_id=456, qqid=123, role=10)
        record = dict(
            cid=1,
            gid=456, bid=2, qqid=123, challenge_pcrdate=10000,
            challenge_pcrtime=999, boss_cycle=4, boss_num=2,
            boss_health_remain=400, challenge_damage=100,
            is_continue=True, message='legacy report', behalf=789,
        )
        columns = ', '.join(record)
        placeholders = ', '.join('?' for _ in record)
        ybdata._db.execute_sql(
            f'INSERT INTO clan_challenge ({columns}) VALUES ({placeholders})',
            tuple(record.values()))
        return record

    def report_data(self):
        # This also works before migration, when return_seconds is absent.
        cursor = ybdata._db.execute_sql('SELECT * FROM clan_challenge')
        return dict(zip([column[0] for column in cursor.description], cursor.fetchone()))

    def assert_migrated_report(self, original):
        self.assertEqual(ybdata.Clan_challenge.get().__data__,
                         dict(original, return_seconds=None))

    def version(self):
        return ybdata.DB_schema.get(key='version').value

    def test_version_two_append_only_preserves_every_report_field(self):
        original = self.legacy_database()
        original_tables = set(ybdata._db.get_tables())
        ybdata.init(self.filename)
        self.assertEqual(self.version(), '4')
        self.assert_migrated_report(original)
        self.assertEqual(ybdata.User.get().nickname, 'legacy member')
        self.assertEqual(ybdata.Clan_member.get().role, 10)
        self.assertEqual(ybdata.Clan_challenge_undo.select().count(), 0)
        self.assertEqual(set(ybdata._db.get_tables()) - original_tables,
                         {ybdata.Clan_challenge_undo._meta.table_name})

    def test_repeated_initialization_preserves_undo_logs_and_records(self):
        original = self.legacy_database()
        ybdata.init(self.filename)
        log = ybdata.Clan_challenge_undo.create(
            cid=original['cid'], gid=456, bid=2,
            before_state='{"health":500}', after_state='{"health":400}')
        ybdata.init(self.filename)
        ybdata.init(self.filename)
        self.assertEqual(self.version(), '4')
        self.assert_migrated_report(original)
        self.assertEqual(ybdata.Clan_challenge_undo.get().__data__, log.__data__)
        self.assertEqual(ybdata.Clan_challenge_undo.select().count(), 1)

    def test_new_database_creates_schema_and_expected_undo_index(self):
        ybdata.init(self.filename)
        self.assertEqual(self.version(), '4')
        for model in LEGACY_MODELS + [ybdata.Clan_challenge_undo]:
            self.assertTrue(model.table_exists())
        table = ybdata.Clan_challenge_undo._meta.table_name
        columns = {column.name: column for column in ybdata._db.get_columns(table)}
        self.assertEqual(set(columns), {'cid', 'gid', 'bid', 'before_state', 'after_state'})
        self.assertTrue(columns['cid'].primary_key)
        self.assertTrue(any(index.columns == ['gid', 'bid']
                            for index in ybdata._db.get_indexes(table)))
        self.assertEqual(ybdata._db.get_foreign_keys(table), [])
        columns = {column.name: column for column in ybdata._db.get_columns('clan_challenge')}
        self.assertTrue(columns['return_seconds'].null)

    def test_migration_failure_rolls_back_table_and_version(self):
        original = self.legacy_database()
        original_tables = set(ybdata._db.get_tables())
        with patch.object(ybdata.DB_schema, 'replace', side_effect=RuntimeError('injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                ybdata.init(self.filename)
        self.assertEqual(self.version(), '2')
        self.assertEqual(set(ybdata._db.get_tables()), original_tables)
        self.assertFalse(ybdata.Clan_challenge_undo.table_exists())
        self.assertEqual(self.report_data(), original)
        self.assertNotIn('return_seconds', self.report_data())
        ybdata.init(self.filename)
        self.assertEqual(self.version(), '4')

    def test_newer_version_rejected_before_any_missing_table_is_created(self):
        ybdata.DB_schema.create_table()
        ybdata.DB_schema.create(key='version', value='5')
        original_tables = set(ybdata._db.get_tables())
        self.assertFalse(ybdata.User.table_exists())
        with self.assertRaises(SystemExit):
            ybdata.init(self.filename)
        self.assertEqual(set(ybdata._db.get_tables()), original_tables)
        self.assertEqual(self.version(), '5')
        self.assertFalse(ybdata.Clan_challenge_undo.table_exists())

    def test_version_one_subscription_conversion_and_log_upgrade_are_atomic(self):
        original = self.legacy_database(version=1)
        ybdata.Clan_group.update(subscribe_list='{"1":[123,789]}').execute()
        with patch.object(ybdata.Clan_challenge_undo, 'create_table', side_effect=RuntimeError('ddl failure')):
            with self.assertRaisesRegex(RuntimeError, 'ddl failure'):
                ybdata.init(self.filename)
        self.assertEqual(self.version(), '1')
        self.assertEqual(ybdata.Clan_group.get().subscribe_list, '{"1":[123,789]}')
        ybdata.init(self.filename)
        self.assertEqual(self.version(), '4')
        self.assertEqual(json.loads(ybdata.Clan_group.get().subscribe_list),
                         {'1': {'123': None, '789': None}})
        self.assert_migrated_report(original)

    def test_version_three_preserves_reports_and_undo_logs(self):
        original = self.legacy_database(version=3)
        log = ybdata.Clan_challenge_undo.create(
            cid=1, gid=456, bid=2, before_state='before', after_state='after')
        ybdata.init(self.filename)
        self.assertEqual(self.version(), '4')
        self.assert_migrated_report(original)
        self.assertEqual(ybdata.Clan_challenge_undo.get().__data__, log.__data__)
        ybdata.Clan_challenge.update(return_seconds=41).execute()
        ybdata.init(self.filename)
        self.assertEqual(ybdata.Clan_challenge.get().return_seconds, 41)


if __name__ == '__main__':
    unittest.main()
