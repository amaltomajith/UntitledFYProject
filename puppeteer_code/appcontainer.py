"""
puppeteer_code/appcontainer.py
==============================
Starts a command inside a Windows AppContainer with no capabilities, which has no
network: no TCP, UDP, DNS or loopback, and no way to ask Windows to fetch something
for it. Children stay inside, because the token is inherited.

This file also runs as a script, and that is how it is used:

    python -I appcontainer.py -- "<command line>"

It stays outside the container, starts the command inside, and exits with its code.
So the caller gets an ordinary process: its pipes reach the command directly, and
killing this script kills the command, through a kill-on-close job. It uses only the
standard library, and `-I` keeps a file in the project from being imported here.
"""

import ctypes
import os
import sys

PROFILE = "pcode.private"
PROFILE_LABEL = "pcode private"
EXISTS = -2147024713                # HRESULT 0x800700B7
START_FAILED = 126

if sys.platform == "win32":
    from ctypes import wintypes as w

    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    CREATE_NO_WINDOW = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    CREATE_SUSPENDED = 0x00000004
    ATTR_SECURITY_CAPABILITIES = 0x00020009
    ATTR_HANDLE_LIST = 0x00020002
    STARTF_USESTDHANDLES = 0x100
    HANDLE_FLAG_INHERIT = 1
    INFINITE = 0xFFFFFFFF
    JOB_KILL_ON_CLOSE = 0x2000
    JOB_EXTENDED_LIMITS = 9
    INVALID_HANDLE = w.HANDLE(-1).value

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    class SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [("AppContainerSid", w.LPVOID), ("Capabilities", w.LPVOID),
                    ("CapabilityCount", w.DWORD), ("Reserved", w.DWORD)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                    ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                    ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                    ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                    ("lpReserved2", w.LPVOID), ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                    ("hStdError", w.HANDLE)]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", w.LPVOID)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

    class JOB_BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

    class JOB_EXTENDED(ctypes.Structure):
        _fields_ = [("Basic", JOB_BASIC), ("IoInfo", ctypes.c_ulonglong * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.CreateAppContainerProfile.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.LPVOID, w.DWORD,
                                                  ctypes.POINTER(w.LPVOID)]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [w.LPCWSTR, ctypes.POINTER(w.LPVOID)]
    userenv.GetAppContainerFolderPath.restype = ctypes.c_long
    userenv.GetAppContainerFolderPath.argtypes = [w.LPCWSTR, ctypes.POINTER(w.LPWSTR)]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [w.LPCWSTR]
    adv.ConvertSidToStringSidW.argtypes = [w.LPVOID, ctypes.POINTER(w.LPWSTR)]
    k32.GetStdHandle.restype = w.HANDLE
    k32.GetStdHandle.argtypes = [w.DWORD]
    k32.SetHandleInformation.argtypes = [w.HANDLE, w.DWORD, w.DWORD]
    k32.InitializeProcThreadAttributeList.argtypes = [w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    k32.UpdateProcThreadAttribute.argtypes = [w.LPVOID, w.DWORD, ctypes.c_size_t, w.LPVOID, ctypes.c_size_t,
                                              w.LPVOID, w.LPVOID]
    k32.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR,
                                   ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION)]
    k32.CreateJobObjectW.restype = w.HANDLE
    k32.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
    k32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    k32.ResumeThread.argtypes = [w.HANDLE]
    k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k32.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    k32.GetVolumeInformationW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, w.LPVOID, w.LPVOID, w.LPVOID, w.LPWSTR, w.DWORD]


def profile_sid():
    """The profile's SID, creating the profile the first time."""
    sid = w.LPVOID()
    result = userenv.CreateAppContainerProfile(PROFILE, PROFILE, PROFILE_LABEL, None, 0, ctypes.byref(sid))
    if result == EXISTS:
        result = userenv.DeriveAppContainerSidFromAppContainerName(PROFILE, ctypes.byref(sid))
    if result != 0:
        raise OSError(f"could not create the sandbox profile: {result & 0xFFFFFFFF:#x}")
    return sid


def sid_text() -> str:
    out = w.LPWSTR()
    if not adv.ConvertSidToStringSidW(profile_sid(), ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    return out.value


def container_folder() -> str:
    """The one folder on C: the container owns."""
    out = w.LPWSTR()
    result = userenv.GetAppContainerFolderPath(sid_text(), ctypes.byref(out))
    if result != 0:
        raise OSError(f"could not find the sandbox folder: {result & 0xFFFFFFFF:#x}")
    return out.value


def delete_profile() -> None:
    userenv.DeleteAppContainerProfile(PROFILE)


def filesystem(path: str) -> str:
    """NTFS has permissions a sandbox can rely on. exFAT has none."""
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    name = ctypes.create_unicode_buffer(64)
    if not k32.GetVolumeInformationW(root, None, 0, None, None, None, name, 64):
        return ""
    return name.value


def _job():
    job = k32.CreateJobObjectW(None, None)
    limits = JOB_EXTENDED()
    limits.Basic.LimitFlags = JOB_KILL_ON_CLOSE
    if not job or not k32.SetInformationJobObject(job, JOB_EXTENDED_LIMITS, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    return job


def tie_to_this_process(process):
    """Ends a process and its children when this one ends, even if this one is killed. None if Windows refuses."""
    job = _job()
    return job if k32.AssignProcessToJobObject(job, int(process._handle)) else None


def _environment() -> ctypes.Array:
    text = "\0".join(f"{k}={v}" for k, v in sorted(os.environ.items(), key=lambda kv: kv[0].upper())) + "\0\0"
    return ctypes.create_unicode_buffer(text, len(text))


def launch(command_line: str) -> int:
    """Runs the command inside the container, wired to this process's own stdin, stdout and stderr."""
    capabilities = SECURITY_CAPABILITIES(profile_sid(), None, 0, 0)
    std = [k32.GetStdHandle(n & 0xFFFFFFFF) for n in (-10, -11, -12)]
    handles = list(dict.fromkeys(h for h in std if h and h != INVALID_HANDLE))
    for handle in handles:
        k32.SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)

    size = ctypes.c_size_t()
    k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    if not k32.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    inherited = (w.HANDLE * len(handles))(*handles)
    for attribute, value, length in ((ATTR_SECURITY_CAPABILITIES, ctypes.byref(capabilities), ctypes.sizeof(capabilities)),
                                     (ATTR_HANDLE_LIST, inherited, ctypes.sizeof(inherited))):
        if not k32.UpdateProcThreadAttribute(attributes, 0, attribute, value, length, None, None):
            raise ctypes.WinError(ctypes.get_last_error())

    info = STARTUPINFOEXW()
    info.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
    info.StartupInfo.dwFlags = STARTF_USESTDHANDLES
    info.StartupInfo.hStdInput, info.StartupInfo.hStdOutput, info.StartupInfo.hStdError = std
    info.lpAttributeList = ctypes.cast(attributes, w.LPVOID)

    job = _job()
    process = PROCESS_INFORMATION()
    flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED
    if not k32.CreateProcessW(None, ctypes.create_unicode_buffer(command_line), None, None, True, flags,
                              _environment(), os.getcwd(), ctypes.byref(info), ctypes.byref(process)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not k32.AssignProcessToJobObject(job, process.hProcess):
        raise ctypes.WinError(ctypes.get_last_error())
    k32.ResumeThread(process.hThread)
    k32.WaitForSingleObject(process.hProcess, INFINITE)
    code = w.DWORD()
    k32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
    return code.value


if __name__ == "__main__":
    try:
        sys.exit(launch(sys.argv[sys.argv.index("--") + 1]))
    except OSError as e:
        print(f"The sandbox could not start this command: {e}", file=sys.stderr)
        sys.exit(START_FAILED)
