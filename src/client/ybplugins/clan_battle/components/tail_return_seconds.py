"""Resolve omitted return seconds using the stage of the original tail."""


def effective_return_seconds(battle, boss_cycle, game_server, seconds):
    if seconds is not None:
        return seconds
    # Stage indices start at zero: B, C, D, ...
    return 90 if battle._level_by_cycle(boss_cycle, game_server) in (0, 1) else None
