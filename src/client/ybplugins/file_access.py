"""Confine public file reads and cache writes to their configured roots."""
import gzip
import mimetypes
from pathlib import Path, PureWindowsPath
from urllib.parse import urljoin

from quart import abort, request, send_file


def safe_path(directory, filename):
    """Reject absolute names, traversal and symlinks outside directory."""
    try:
        name = Path(filename)
        windows_name = PureWindowsPath(filename)
        if (not filename or name.is_absolute() or windows_name.drive
                or windows_name.root or '..' in windows_name.parts
                or '\x00' in filename):
            raise ValueError('Invalid public file name')
        root = Path(directory).resolve()
        candidate = (root / name).resolve()
        candidate.relative_to(root)
        return candidate
    except (ValueError, OSError, RuntimeError):
        abort(404)


async def send_safe_file(directory, filename):
    path = safe_path(directory, filename)
    if not path.is_file():
        abort(404)
    return await send_file(str(path))


def register_file_routes(app, public_basepath, libs_directory,
                         static_directory, output_directory, gzip_level=0):
    """Register file routes without initializing the bot or QQ connection."""
    Path(output_directory).mkdir(parents=True, exist_ok=True)

    @app.route('/yobot-depencency/<path:filename>')
    async def yobot_js_dependencies(filename):
        origin = safe_path(libs_directory, filename)
        if not origin.is_file():
            abort(404)
        if not gzip_level or 'gzip' not in request.headers.get('Accept-Encoding', '').lower():
            return await send_safe_file(libs_directory, filename)
        compressed = safe_path(libs_directory, filename + '.gz')
        if not compressed.exists():
            with origin.open('rb') as source, compressed.open('wb') as target:
                with gzip.GzipFile(mode='wb', compresslevel=gzip_level, fileobj=target) as stream:
                    stream.write(source.read())
        if not compressed.is_file():
            abort(404)
        response = await send_file(str(compressed))
        response.mimetype = mimetypes.guess_type(origin.name)[0] or 'application/octet-stream'
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Vary'] = 'Accept-Encoding'
        return response

    @app.route(urljoin(public_basepath, 'assets/<path:filename>'))
    async def yobot_static(filename):
        return await send_safe_file(static_directory, filename)

    @app.route(urljoin(public_basepath, 'output/<path:filename>'))
    async def yobot_output(filename):
        return await send_safe_file(output_directory, filename)
