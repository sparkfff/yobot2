import os
import hashlib
from functools import lru_cache
from pathlib import Path

import jinja2
from quart import session, url_for 

static_folder = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '../public/static'))
template_folder = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '../public/template'))

Ver = 'unknown'


@lru_cache(maxsize=128)
def _asset_digest(path, modified_ns, size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _static_version(filename):
    """Invalidate browser caches when assets change within the same release."""
    try:
        root = Path(static_folder).resolve()
        path = (root / filename).resolve()
        path.relative_to(root)
        stat = path.stat()
        digest = _asset_digest(str(path), stat.st_mtime_ns, stat.st_size)
        return f'{Ver}-{digest}'
    except (OSError, ValueError, TypeError):
        return Ver


def _vertioned_url_for(endpoint, *args, **kwargs):
    if endpoint == 'yobot_static':
        kwargs['v'] = _static_version(kwargs.get('filename'))
    return url_for(endpoint, *args, **kwargs)


_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(template_folder),
    enable_async=True,
)
_env.globals['session'] = session
_env.globals['url_for'] = _vertioned_url_for


async def render_template(template, **context):
    t = _env.get_template(template)
    return await t.render_async(**context)
