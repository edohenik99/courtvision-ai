"""Exclusive payload writes and durability for artifact directory claims.

Callers validate their artifact domain before entering these primitives. Windows
payload creation opens every component relative to a retained directory handle and refuses
reparse processing. POSIX uses component-relative no-follow directory fds.
POSIX directory claims sync each directory and its parent entry before returning;
Windows directory claims preserve the existing mkdir behavior.
Those fds prevent symlink redirection, but cannot stop another process renaming
an already opened directory. These primitives do not secure unrelated
path-based readers against concurrent mutation.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from functools import lru_cache
import os
from pathlib import Path
import re
import stat


class ArtifactConfinementError(ValueError):
    """Artifact creation cannot prove a plain filesystem path."""


def _components(path: Path) -> tuple[str, tuple[str, ...]]:
    if not path.is_absolute() or not path.name:
        raise ArtifactConfinementError("absolute artifact file path required")
    anchor, parts = path.anchor, path.parts[1:]
    if not parts:
        raise ArtifactConfinementError("artifact leaf required")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
                *(f"lpt{i}" for i in range(10))}
    for part in parts:
        if part in {"", ".", ".."} or "\0" in part:
            raise ArtifactConfinementError("ambiguous artifact path component")
        if os.name == "nt" and (any(ord(char) < 32 or char in '\\/:"<>|?*' for char in part)
                or part[-1] in " ." or part.split(".", 1)[0].casefold() in reserved):
            raise ArtifactConfinementError("unsupported Windows path component")
    if os.name == "nt" and re.fullmatch(r"[A-Za-z]:\\", anchor) is None:
        raise ArtifactConfinementError("local drive artifact path required")
    return anchor, parts


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_uint16), ("MaximumLength", ctypes.c_uint16),
                    ("Buffer", ctypes.c_void_p)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_uint32), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UnicodeString)),
                    ("Attributes", ctypes.c_uint32), ("SecurityDescriptor", ctypes.c_void_p),
                    ("SecurityQualityOfService", ctypes.c_void_p)]

    class StatusUnion(ctypes.Union):
        _fields_ = [("Status", ctypes.c_int32), ("Pointer", ctypes.c_void_p)]

    class IoStatus(ctypes.Structure):
        _fields_ = [("Result", StatusUnion), ("Information", ctypes.c_size_t)]

    class FileInformation(ctypes.Structure):
        _fields_ = [("Attributes", wintypes.DWORD), ("Creation", wintypes.FILETIME),
                    ("Access", wintypes.FILETIME), ("Write", wintypes.FILETIME),
                    ("Volume", wintypes.DWORD), ("SizeHigh", wintypes.DWORD),
                    ("SizeLow", wintypes.DWORD), ("Links", wintypes.DWORD),
                    ("IndexHigh", wintypes.DWORD), ("IndexLow", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    native.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatus), ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD]
    native.NtCreateFile.restype = ctypes.c_int32
    native.RtlNtStatusToDosError.argtypes = [ctypes.c_int32]
    native.RtlNtStatusToDosError.restype = wintypes.DWORD
    return kernel, native, UnicodeString, ObjectAttributes, IoStatus, FileInformation


def _verify_windows_handle(handle: int, *, directory: bool) -> None:
    import ctypes
    kernel, _, _, _, _, FileInformation = _windows_api()
    info = FileInformation()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if (kernel.GetFileType(handle) != 1 or info.Attributes & 0x400
            or bool(info.Attributes & 0x10) != directory):
        raise ArtifactConfinementError("artifact handle is not a plain expected object")


def _open_windows_child(parent: int, name: str, *, directory: bool) -> int:
    import ctypes
    from ctypes import wintypes
    kernel, native, UnicodeString, ObjectAttributes, IoStatus, _ = _windows_api()
    encoded = name.encode("utf-16-le")
    if len(encoded) > 65532:
        raise ArtifactConfinementError("artifact component is too long")
    buffer = ctypes.create_string_buffer(encoded + b"\0\0")
    text = UnicodeString(len(encoded), len(encoded) + 2, ctypes.cast(buffer, ctypes.c_void_p))
    attrs = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent, ctypes.pointer(text),
                             0x40 | 0x1000, None, None)  # CASE_INSENSITIVE | DONT_REPARSE
    result, io_status = wintypes.HANDLE(), IoStatus()
    access = (0x80 | 0x20 | 0x100000) if directory else (0x80000000 | 0x40000000 | 0x100000)
    options = 0x00200000 | 0x20 | (0x1 if directory else 0x40)
    status = native.NtCreateFile(ctypes.byref(result), access, ctypes.byref(attrs),
        ctypes.byref(io_status), None, 0x80, 0x1 if directory else 0,
        0x1 if directory else 0x2, options, None, 0)  # FILE_OPEN / FILE_CREATE
    if status < 0:
        raise ctypes.WinError(native.RtlNtStatusToDosError(status))
    if not result.value:
        raise ArtifactConfinementError("artifact operation returned no handle")
    try:
        _verify_windows_handle(result.value, directory=directory)
    except BaseException:
        kernel.CloseHandle(result.value)
        raise
    return result.value


@contextmanager
def _exclusive_file(path: Path):
    anchor, parts = _components(path)
    if os.name == "nt":
        import ctypes
        import msvcrt
        kernel, _, _, _, _, _ = _windows_api()
        with ExitStack() as cleanup:
            root = kernel.CreateFileW(anchor, 0x80 | 0x20, 0x1, None, 3,
                                      0x02000000 | 0x00200000, None)
            if root == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            cleanup.callback(kernel.CloseHandle, root)
            _verify_windows_handle(root, directory=True)
            parent = root
            for name in parts[:-1]:
                parent = _open_windows_child(parent, name, directory=True)
                cleanup.callback(kernel.CloseHandle, parent)
            leaf = _open_windows_child(parent, parts[-1], directory=False)
            try:
                file_descriptor = msvcrt.open_osfhandle(leaf, os.O_RDWR | os.O_BINARY | os.O_NOINHERIT)
            except BaseException:
                kernel.CloseHandle(leaf)
                raise
            cleanup.callback(os.close, file_descriptor)
            os.set_inheritable(file_descriptor, False)
            yield file_descriptor
    elif os.name == "posix":
        if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
            raise ArtifactConfinementError("no-follow relative file operations required")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        with ExitStack() as cleanup:
            parent = os.open(anchor, flags)
            cleanup.callback(os.close, parent)
            for name in parts[:-1]:
                parent = os.open(name, flags, dir_fd=parent)
                cleanup.callback(os.close, parent)
                if not stat.S_ISDIR(os.fstat(parent).st_mode):
                    raise ArtifactConfinementError("artifact parent is not a directory")
            file_descriptor = os.open(parts[-1], os.O_RDWR | os.O_CREAT | os.O_EXCL
                | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
            cleanup.callback(os.close, file_descriptor)
            if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                raise ArtifactConfinementError("artifact leaf is not a regular file")
            yield file_descriptor
            os.fsync(parent)
    else:
        raise ArtifactConfinementError("unsupported artifact filesystem platform")


def create_once_directory(path: Path) -> None:
    """Claim a new leaf after syncing its POSIX directory ancestry and entry.

    Existing ancestors are permitted; only an existing leaf raises the claim
    collision. Any new directories remain in place if validation or fsync fails.
    """
    anchor, parts = _components(path)
    if os.name == "nt":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
        return
    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd):
        raise ArtifactConfinementError("no-follow relative directory operations required")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    with ExitStack() as cleanup:
        parent = os.open(anchor, flags)
        cleanup.callback(os.close, parent)
        if not stat.S_ISDIR(os.fstat(parent).st_mode):
            raise ArtifactConfinementError("artifact anchor is not a directory")

        def open_child(name: str, descriptor: int) -> int:
            child = os.open(name, flags, dir_fd=descriptor)
            cleanup.callback(os.close, child)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                raise ArtifactConfinementError("artifact claim component is not a directory")
            return child

        for name in parts[:-1]:
            try:
                child = open_child(name, parent)
            except FileNotFoundError:
                try:
                    os.mkdir(name, 0o700, dir_fd=parent)
                except FileExistsError:
                    # An ancestor created concurrently still needs a no-follow fd.
                    pass
                child = open_child(name, parent)
            os.fsync(child)
            os.fsync(parent)
            parent = child
        os.mkdir(parts[-1], 0o700, dir_fd=parent)
        child = open_child(parts[-1], parent)
        os.fsync(child)
        os.fsync(parent)


def create_once_bytes(path: Path, raw: bytes) -> None:
    """Write/fsync/read back a new payload through one exclusively created fd."""
    if not isinstance(raw, bytes):
        raise ArtifactConfinementError("artifact payload must be bytes")
    with _exclusive_file(path) as descriptor:
        view = memoryview(raw)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise ArtifactConfinementError("artifact write did not progress")
            view = view[count:]
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = len(raw)
        while remaining:
            block = os.read(descriptor, min(remaining, 65536))
            if not block or block != raw[len(raw) - remaining:len(raw) - remaining + len(block)]:
                raise ArtifactConfinementError("artifact write/read-back mismatch")
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ArtifactConfinementError("artifact readback has trailing bytes")
