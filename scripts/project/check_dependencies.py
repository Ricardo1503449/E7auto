"""Check Python source ownership and import boundaries without importing the app."""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath


def imported_modules(source: str, module: str, *, package: bool = False):
    """Include imports inside functions/TYPE_CHECKING and literal dynamic imports."""
    tree = ast.parse(source)
    parent = module if package else module.rpartition('.')[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                yield node.lineno, item.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = parent.split('.')
                if node.level > len(parts):
                    raise ValueError('relative import escapes package')
                prefix = '.'.join(parts[:len(parts) - node.level + 1])
                target = '.'.join(filter(None, (prefix, node.module)))
            else:
                target = node.module or ''
            yield node.lineno, target
            for item in node.names:
                if item.name != '*':
                    yield node.lineno, target + '.' + item.name
        elif isinstance(node, ast.Call) and node.args:
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            if name in {'import_module', '__import__'}:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield node.lineno, arg.value


def check_dependencies(paths: Iterable[str], read: Callable[[str], bytes], policy: dict) -> list[str]:
    rules = policy.get('source_rules')
    if rules is None:
        return []  # Older index trees retain their original layout policy.
    layers = set(rules['layers'])
    compatibility = set(rules['compatibility_modules'])
    legacy = compatibility | set(rules.get('legacy_modules', []))
    root_modules = set(rules['root_modules'])
    errors = []
    def matches(target, prefix):
        return target == prefix or target.startswith(prefix + '.')
    for name in sorted(paths):
        if not name.startswith('src/e7auto/') or not name.endswith('.py'):
            continue
        path = PurePosixPath(name)
        parts = path.parts[2:]
        package = parts[-1] == '__init__.py'
        module = '.'.join(('e7auto', *parts[:-1], *([] if package else [path.stem])))
        layer = parts[0] if len(parts) > 1 else ''
        if module not in compatibility and ((layer and layer not in layers) or (not layer and module not in root_modules)):
            errors.append(f'Unregistered source location: {name}')
        if layer == 'features' and len(parts) > 2 and parts[1] not in policy['features']:
            errors.append(f'Unregistered source feature: {name}')
        try:
            imports = list(imported_modules(read(name).decode('utf-8-sig'), module, package=package))
        except (SyntaxError, UnicodeError, ValueError) as exc:
            errors.append(f'Cannot inspect source: {name}: {exc}')
            continue
        for line, target in imports:
            reason = None
            if any(matches(target, prefix) for prefix in ('scripts', 'tests')):
                reason = 'runtime source cannot depend on development code'
            elif module not in compatibility and any(matches(target, prefix) for prefix in legacy):
                reason = 'new code cannot import legacy compatibility modules'
            elif module not in compatibility:
                target_parts = target.split('.')
                if layer == 'features' and len(parts) > 2 and target.startswith('e7auto.features.') and len(target_parts) > 2 and target_parts[2] != parts[1]:
                    reason = 'features cannot import each other'
                elif layer in {'core', 'runtime', 'vision', 'resources', 'configuration', 'platform', 'logging'} and target.startswith(('e7auto.features', 'e7auto.ui', 'e7auto.bootstrap', 'e7auto.app')):
                    reason = 'shared layers cannot import feature or presentation implementations'
                elif layer in {'features', 'runtime', 'vision', 'resources', 'configuration', 'core'} and (
                    any(matches(target, prefix) for prefix in ('PySide6', 'winrt', 'ctypes', 'pythoncom', 'pywintypes', 'e7auto.platform'))
                    or target.split('.')[0].startswith('win32')
                ):
                    reason = 'business/shared layers cannot import native implementations'
                elif layer == 'core' and target.startswith('e7auto.') and not target.startswith('e7auto.core.'):
                    reason = 'core cannot depend on upper layers'
            if reason:
                errors.append(f'Source dependency: {name}:{line}: {target}: {reason}')
            if matches(target, 'e7auto.platform.wgc_capture'):
                permitted = rules.get('deferred_wgc_imports', {}).get(module, [])
                tree = ast.parse(read(name).decode('utf-8-sig'))
                inside = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and node.name in permitted and node.lineno < line <= node.end_lineno
                             for node in ast.walk(tree))
                if not inside:
                    errors.append(f'WGC import must stay inside an approved worker/self-check factory: {name}:{line}')
            if matches(target, 'winrt') and module != 'e7auto.platform.wgc_capture':
                errors.append(f'WinRT imports belong only to the isolated WGC adapter: {name}:{line}')
    return sorted(set(errors))
