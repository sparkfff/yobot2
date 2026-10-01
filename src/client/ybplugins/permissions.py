"""Shared permission checks; clan privileges belong to a membership."""


def is_global_owner(user):
    return bool(user and not user.deleted and user.authority_group == 1)


def can_view_clan(user, membership):
    return bool(user and not user.deleted and
                (is_global_owner(user) or membership is not None))


def can_manage_clan(user, membership):
    return bool(user and not user.deleted and
                (is_global_owner(user) or
                 (membership is not None and membership.role in (1, 10))))


def can_manage_group_command(ctx, settings):
    """Use the actor's role in the current QQ group, never a delegated target."""
    return (ctx.get('user_id') in settings.get('super-admin', []) or
            ctx.get('sender', {}).get('role') in ('owner', 'admin'))
