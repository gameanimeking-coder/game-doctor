"""Read text aloud with the voices built into Windows (System.Speech, via PowerShell)."""
import subprocess

_proc = None
_test_hook = None   # the self-test replaces this to avoid making noise


def speak(text):
    global _proc
    stop()
    if _test_hook:
        _test_hook(text)
        return True
    script = ('[Console]::InputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; '
              '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $t = [Console]::In.ReadToEnd(); $s.Speak($t)')
    try:
        _proc = subprocess.Popen(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _proc.stdin.write(text.encode('utf-8', 'replace'))
        _proc.stdin.close()
        return True
    except OSError:
        _proc = None
        return False


def speaking():
    return _proc is not None and _proc.poll() is None


def stop():
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
    _proc = None
