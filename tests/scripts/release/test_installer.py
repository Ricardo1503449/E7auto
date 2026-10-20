from pathlib import Path

import pytest

from scripts.release.installer import MANIFEST, PRODUCT_ID, installed_files, installer_name, manifest_bytes
from tests.helpers.paths import ROOT


@pytest.mark.parametrize('name', ['../other.txt', 'C:/Windows/a', '/outside', r'\\server\share\a',
                                  'logs/a.log', 'state/config.json', 'a\nb', 'unins000.exe'])
def test_payload_manifest_rejects_paths_outside_owned_program_files(name):
    with pytest.raises(ValueError):
        manifest_bytes(['E7auto.exe', name])


def test_manifest_keeps_unicode_and_space_names_and_lists_itself():
    content = manifest_bytes(['E7auto.exe', '使用 说明.txt', 'assets/ui/icon.png']).decode('utf-8-sig')
    assert content.splitlines()[0] == PRODUCT_ID
    assert set(content.splitlines()[1:]) == {'E7auto.exe', '使用 说明.txt', r'assets\ui\icon.png', MANIFEST}
    with pytest.raises(ValueError, match='Case-colliding'):
        manifest_bytes(['App.exe', 'app.exe'])


def test_installed_inventory_preserves_exact_payload_hashes_but_ignores_runtime_data(tmp_path):
    exe = tmp_path / 'E7auto.exe'
    exe.write_bytes(b'MZ payload')
    expected = installed_files(tmp_path)
    for name in ['logs/a.log', 'state/ui_preferences.json', 'unins000.exe', 'unins000.dat']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'runtime')
    assert installed_files(tmp_path) == expected
    exe.write_bytes(b'changed')
    assert installed_files(tmp_path) != expected


def test_installer_contract_retains_identity_path_choice_and_scoped_cleanup():
    from e7auto.platform.installation import INSTALLED_APP_ID, RUNNING_MUTEX
    from e7auto.platform.notifications import _ACTIVATOR_ID
    script = (ROOT / 'scripts/release/e7auto.iss').read_text(encoding='utf-8-sig')
    assert PRODUCT_ID in script and INSTALLED_APP_ID in script and RUNNING_MUTEX in script and _ACTIVATOR_ID in script
    assert 'DisableDirPage=no' in script and 'UsePreviousAppDir=yes' in script
    assert 'Description: "创建桌面快捷方式"; Flags: unchecked' in script
    assert 'CloseApplications=no' in script and 'RestartApplications=no' in script
    assert 'Name: "{app}\\logs"' in script and 'Name: "{app}\\state"' in script
    assert 'Type: filesandordirs; Name: "{app}"' not in script
    assert 'HKEY_CURRENT_USER' not in script and 'Root: HKCU' not in script
    assert 'Environment;' not in script and 'setToolTip' not in script
    assert installer_name('1.4.0') == 'E7auto_v1.4.0_Setup_x64.exe'


def test_update_mode_waits_without_killing_and_restarts_with_cleanup_identity():
    script = (ROOT / 'scripts/release/e7auto.iss').read_text(encoding='utf-8-sig')
    assert 'Check: IsUpdateMode' in script and 'Flags: nowait' in script
    assert '--after-update={param:E7AUTOUPDATEID|}' in script
    assert 'ValidUpdateJob()' in script and "Prefix := 'v{#AppVersion}-'" in script
    assert 'for I := 1 to 600 do' in script and 'Sleep(100)' in script
    assert script.index('for I := 1 to 600 do') < script.index("Result := 'E7auto 正在运行")
    assert 'CloseApplications=no' in script and 'RestartApplications=no' in script
    assert 'TerminateProcess' not in script
