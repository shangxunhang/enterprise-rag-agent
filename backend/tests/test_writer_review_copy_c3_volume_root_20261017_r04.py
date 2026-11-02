"""Native root exception negatives; no volume-root ACL or reparse mutation."""
from pathlib import Path

import pytest
import win32api
import win32file
import win32security as security

from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
from test_writer_review_copy_c3_production_20261017_r03 import (
    protected_directory, _acl, _allowed_copy, _changed_dacl, _evidence,
)


def test_c3_r04_real_volume_root_and_ordinary_modify_refused(tmp_path):
    root = Path(tmp_path.anchor)
    before = _acl(root)[1]
    wc._wc_require_trusted_directory(root, output_root=tmp_path,
                                     trusted_sids=wc._wc_trusted_windows_sids())
    ordinary = protected_directory(tmp_path / "ordinary-ancestor")
    modified = _allowed_copy(_acl(ordinary)[0].GetSecurityDescriptorDacl())
    modified.AddAccessAllowedAceEx(security.ACL_REVISION, 0, 0x1301BF,
                                  security.ConvertStringSidToSid("S-1-5-11"))
    with _changed_dacl(ordinary, modified, "C3_R04_ORDINARY_MODIFY_REFUSED"):
        with pytest.raises(wc.WriterReviewCopyUnavailable, match="mutation grant"):
            wc._wc_require_trusted_directory(ordinary, output_root=ordinary / "fixed-child",
                                             trusted_sids=wc._wc_trusted_windows_sids())
    assert _acl(root)[1] == before
    _evidence("C3_R04_REAL_ROOT", {"root": str(root), "root_acl_before": before,
                                  "root_acl_after": _acl(root)[1], "root_mutated": False})


@pytest.mark.parametrize("mutation", [0x40, 0x40000, 0x80000, 0x40000000, 0x10000000])
def test_c3_r04_root_dangerous_grants_refused_without_root_mutation(tmp_path, monkeypatch, mutation):
    root = Path(tmp_path.anchor)
    actual = security.GetNamedSecurityInfo(wc._wc_extended(root), security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
    injected = security.SECURITY_DESCRIPTOR()
    injected.SetSecurityDescriptorOwner(actual.GetSecurityDescriptorOwner(), False)
    acl = security.ACL()
    acl.AddAccessAllowedAce(security.ACL_REVISION, mutation, security.ConvertStringSidToSid("S-1-5-11"))
    injected.SetSecurityDescriptorDacl(True, acl, False)
    # A negative read-API fixture, never a production path guard replacement.
    monkeypatch.setattr(security, "GetNamedSecurityInfo", lambda *args: injected)
    with pytest.raises(wc.WriterReviewCopyUnavailable, match="mutation grant"):
        wc._wc_require_trusted_directory(root, output_root=tmp_path,
                                         trusted_sids=wc._wc_trusted_windows_sids())


def test_c3_r04_nonphysical_or_mismatched_volume_identity_refused(tmp_path, monkeypatch):
    root = Path(tmp_path.anchor)
    with monkeypatch.context() as fault:
        fault.setattr(win32file, "GetDriveType", lambda path: 4)
        with pytest.raises(wc.WriterReviewCopyUnavailable): wc._wc_require_fixed_ntfs_root(root)
    information = win32api.GetVolumeInformation(str(root))
    with monkeypatch.context() as fault:
        fault.setattr(win32api, "GetVolumeInformation", lambda path: (*information[:-1], "FAT32"))
        with pytest.raises(wc.WriterReviewCopyUnavailable): wc._wc_require_fixed_ntfs_root(root)
    query = win32file.QueryDosDevice
    with monkeypatch.context() as fault:
        fault.setattr(win32file, "QueryDosDevice", lambda name: "\\??\\E:\\ordinary" if name == root.drive else query(name))
        with pytest.raises(wc.WriterReviewCopyUnavailable): wc._wc_require_fixed_ntfs_root(root)
    final = win32file.GetFinalPathNameByHandle
    with monkeypatch.context() as fault:
        fault.setattr(win32file, "GetFinalPathNameByHandle", lambda handle, flags: final(handle, flags) + "alias")
        with pytest.raises(wc.WriterReviewCopyUnavailable, match="identity mismatch"):
            wc._wc_require_fixed_ntfs_root(root)
    with monkeypatch.context() as fault:
        fault.setattr(win32file, "QueryDosDevice", lambda name: (_ for _ in ()).throw(OSError("query failure")))
        with pytest.raises(OSError): wc._wc_require_fixed_ntfs_root(root)
