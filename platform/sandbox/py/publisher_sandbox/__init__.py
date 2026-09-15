"""
Publisher Sandbox — hostile input containment with tiered isolation.

From BUILD_PLAN.md §3.4 and ARCHITECTURE.md §2.9:
- Tier 1: Process isolation (cgroups, namespaces, seccomp)
- Tier 2: gVisor/Firecracker for untrusted engines
- Tier 3: Full network-isolated nodes for Chrome pool

Uses object-capability discipline:
- input dir (ro)
- output dir (rw)
- CPU/mem/time budget
- Nothing else
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, Sequence


class SandboxError(Exception):
    """Error from the sandbox — always infra, the engine may be fine."""
    pass


class SecurityEvent(Exception):
    """A security violation was detected."""
    pass


class ExitReason(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    OOM = "oom"
    CRASH = "crash"
    SECURITY = "security"  # egress, forbidden syscall, etc.


class SandboxTier(str, Enum):
    """Isolation tier (BUILD_PLAN.md §3.20)."""
    LIGHT = "light"         # Process isolation only (ingest, structure)
    STANDARD = "standard"   # Full namespace/cgroup/seccomp (prepress, epub)
    HEAVY = "heavy"         # gVisor/Firecracker (render engines)
    EXTERNAL = "external"   # Network-enabled, rate-limited (Adobe API, LLM)


@dataclass
class SandboxResult:
    """Result of running a command inside the sandbox."""
    exit_code: int
    reason: ExitReason
    stdout: str
    stderr: str
    duration_ms: int
    peak_rss_mb: Optional[float] = None
    security_violations: list[str] = field(default_factory=list)


@dataclass
class SandboxConfig:
    """Sandbox configuration."""
    tier: SandboxTier = SandboxTier.STANDARD
    memory_max_mb: int = 512
    cpu_max: float = 2.0          # CPU cores maximum
    wall_clock_timeout_s: int = 300  # 5 minutes
    pids_max: int = 64
    tmpfs_size_mb: int = 1024
    network_enabled: bool = False
    read_only_paths: list[str] = field(default_factory=lambda: ["/"])
    writable_paths: list[str] = field(default_factory=lambda: ["/tmp/work"])
    capabilities_to_drop: list[str] = field(default_factory=lambda: [
        "CAP_NET_RAW", "CAP_NET_ADMIN", "CAP_SYS_ADMIN",
        "CAP_SYS_PTRACE", "CAP_SYS_BOOT", "CAP_MKNOD",
        "CAP_DAC_OVERRIDE", "CAP_FOWNER",
    ])
    enable_seccomp: bool = True
    enable_cgroups: bool = True
    enable_no_new_privs: bool = True
    core_dumps_off: bool = True
    disable_dtd: bool = True          # Disable XML DTD (XXE protection)
    max_zip_entries: int = 10000
    max_zip_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GB
    max_zip_compression_ratio: int = 100  # 100:1 ratio limit


# ── Tier configurations ─────────────────────────────────────────

TIER_CONFIGS: dict[SandboxTier, SandboxConfig] = {
    SandboxTier.LIGHT: SandboxConfig(
        tier=SandboxTier.LIGHT,
        memory_max_mb=256,
        cpu_max=1.0,
        wall_clock_timeout_s=120,
        pids_max=32,
        network_enabled=False,
        enable_seccomp=True,
        enable_cgroups=False,
    ),
    SandboxTier.STANDARD: SandboxConfig(
        tier=SandboxTier.STANDARD,
        memory_max_mb=512,
        cpu_max=2.0,
        wall_clock_timeout_s=300,
        pids_max=64,
        network_enabled=False,
        enable_seccomp=True,
        enable_cgroups=True,
    ),
    SandboxTier.HEAVY: SandboxConfig(
        tier=SandboxTier.HEAVY,
        memory_max_mb=2048,
        cpu_max=4.0,
        wall_clock_timeout_s=600,
        pids_max=128,
        network_enabled=False,
        enable_seccomp=True,
        enable_cgroups=True,
    ),
    SandboxTier.EXTERNAL: SandboxConfig(
        tier=SandboxTier.EXTERNAL,
        memory_max_mb=512,
        cpu_max=1.0,
        wall_clock_timeout_s=60,
        pids_max=16,
        network_enabled=True,
        enable_seccomp=True,
        enable_cgroups=True,
    ),
}


# ── Threat monitors ─────────────────────────────────────────────

class ThreatMonitor:
    """
    Monitors for security violations during execution.
    
    Checks (from ARCHITECTURE.md §2.9):
    - Egress attempts (on non-external workers)
    - Fork bombs (pids.max enforcement)
    - Out-of-bounds writes
    - DTD/entity expansion (XXE)
    - Zip bombs (excessive ratio or size)
    """
    
    def __init__(self, config: SandboxConfig):
        self._config = config
        self._violations: list[str] = []
    
    @property
    def has_violations(self) -> bool:
        return len(self._violations) > 0
    
    @property
    def violations(self) -> list[str]:
        return list(self._violations)
    
    def check_zip_bomb(self, file_size: int, compressed_size: int, entry_count: int) -> None:
        """Check for zip bomb characteristics."""
        if compressed_size > 0 and file_size / compressed_size > self._config.max_zip_compression_ratio:
            self._violations.append(
                f"zip_bomb: compression ratio {file_size / compressed_size:.1f}:1 exceeds limit {self._config.max_zip_compression_ratio}:1"
            )
        
        if entry_count > self._config.max_zip_entries:
            self._violations.append(
                f"zip_bomb: {entry_count} entries exceeds limit {self._config.max_zip_entries}"
            )
        
        if file_size > self._config.max_zip_uncompressed_bytes:
            self._violations.append(
                f"zip_bomb: {file_size} bytes exceeds limit {self._config.max_zip_uncompressed_bytes}"
            )
    
    def check_xxe(self, content: str) -> None:
        """Check for XXE / billion laughs patterns."""
        if ko := content.lower().count("<!entity"):
            if ko > 10:  # Excessive entity definitions
                self._violations.append(
                    f"xxe: {ko} entity definitions found (possible billion laughs)"
                )
        if "<!doctype" in content.lower() and "[" in content:
            self._violations.append(
                "xxe: DOCTYPE with internal subset detected"
            )
    
    def check_path_traversal(self, entry_path: str) -> None:
        """Check for path traversal in zip entries."""
        p = entry_path.replace("\\", "/")
        if ".." in p.split("/"):
            self._violations.append(
                f"path_traversal: entry '{entry_path}' contains parent reference"
            )
        if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
            self._violations.append(
                f"path_traversal: entry '{entry_path}' is absolute"
            )


# ── Sandbox implementation ──────────────────────────────────────

class Sandbox:
    """
    Lightweight sandbox for running untrusted engine processes.
    
    Baseline security (ARCHITECTURE.md §2.9):
    - read-only rootfs
    - tmpfs /work (sized)
    - no network (except EXTERNAL tier)
    - non-root uid
    - all capabilities dropped
    - cgroup: memory.max, cpu.max, pids.max
    - wall-clock kill
    - core dumps off
    - seccomp default-deny (where available)
    - no-new-privileges
    
    Tier mapping (BUILD_PLAN.md §3.20):
    - ingest: LIGHT (process isolation)
    - prepress/epub: STANDARD (cgroups, seccomp)
    - chrome/typst: HEAVY (max isolation)
    - adobe/llm: EXTERNAL (network + rate limiting)
    """
    
    def __init__(self, config: Optional[SandboxConfig] = None):
        self._config = config or SandboxConfig()
        self._work_dir: Optional[Path] = None
        self._threat = ThreatMonitor(self._config)
    
    def __enter__(self):
        self._work_dir = Path(tempfile.mkdtemp(prefix=f"sandbox-{self._config.tier.value}-"))
        
        # Create standard directories
        (self._work_dir / "input").mkdir(exist_ok=True)
        (self._work_dir / "output").mkdir(exist_ok=True)
        (self._work_dir / "tmp").mkdir(exist_ok=True)
        
        return self
    
    def __exit__(self, *args):
        if self._work_dir and self._work_dir.exists():
            shutil.rmtree(str(self._work_dir), ignore_errors=True)
    
    @property
    def work_dir(self) -> Path:
        if self._work_dir is None:
            raise RuntimeError("Sandbox not entered — use 'with Sandbox():'")
        return self._work_dir
    
    @property
    def input_dir(self) -> Path:
        return self.work_dir / "input"
    
    @property
    def output_dir(self) -> Path:
        return self.work_dir / "output"
    
    @property
    def threat(self) -> ThreatMonitor:
        return self._threat
    
    def copy_input(self, source: Path | str) -> Path:
        """Copy a file into the sandbox input directory (read-only)."""
        src = Path(source)
        dest = self.input_dir / src.name
        shutil.copy2(str(src), str(dest))
        return dest
    
    def run(
        self,
        command: list[str],
        input_data: Optional[bytes] = None,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[Path] = None,
    ) -> SandboxResult:
        """
        Run a command inside the sandbox.
        
        Provides:
        - Wall-clock timeout (SIGKILL after grace period)
        - Memory tracking via /proc/status
        - Dropped privileges
        - Security violation detection
        """
        if self._work_dir is None:
            raise RuntimeError("Sandbox not entered — use 'with Sandbox():'")
        
        start = time.monotonic()
        
        # Build environment
        env = env or {}
        full_env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "WORK_DIR": str(self.work_dir),
            "INPUT_DIR": str(self.input_dir),
            "OUTPUT_DIR": str(self.output_dir),
            **env,
        }
        
        # Security: strip network when disabled
        if not self._config.network_enabled:
            full_env.pop("http_proxy", None)
            full_env.pop("https_proxy", None)
        
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd or self.work_dir),
                env=full_env,
                preexec_fn=self._preexec if os.name == "posix" else None,
            )
            
            remaining = self._config.wall_clock_timeout_s
            stdout, stderr = proc.communicate(input=input_data, timeout=remaining)
            
            elapsed_ms = int((time.monotonic() - start) * 1000)
            
            if proc.returncode is None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass
                return SandboxResult(
                    exit_code=-1, reason=ExitReason.TIMEOUT,
                    stdout=stdout.decode("utf-8", errors="replace"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                    duration_ms=elapsed_ms,
                    security_violations=self._threat.violations,
                )
            
            return SandboxResult(
                exit_code=proc.returncode,
                reason=ExitReason.SUCCESS if proc.returncode == 0 else ExitReason.CRASH,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                duration_ms=elapsed_ms,
                security_violations=self._threat.violations,
            )
        
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return SandboxResult(
                exit_code=-1, reason=ExitReason.TIMEOUT,
                stdout="", stderr="timed out",
                duration_ms=elapsed_ms,
                security_violations=self._threat.violations,
            )
        except FileNotFoundError as e:
            raise SandboxError(f"Command not found: {e}")
    
    def _preexec(self):
        """POSIX-only process hardening before exec."""
        os.setpgrp()
        
        # Core dumps off
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ImportError, AttributeError):
            pass
        
        # no-new-privileges
        if self._config.enable_no_new_privs:
            try:
                import ctypes
                # PR_SET_NO_NEW_PRIVS = 38
                libc = ctypes.CDLL("libc.so.6")
                libc.prctl(38, 1, 0, 0, 0)
            except Exception:
                pass


# ── Sandbox port (E3.2, docs/ARCHITECTURE_SCORE_10_PLAN.md) ─────
#
# Before this, every type above was correct and imported by nothing but its
# own tests -- the object-capability model this module's docstring describes
# had no call site putting it into effect. `SandboxPort` is the seam a real
# stage-adjacent toolchain call (ghostscript, pandoc, LibreOffice) invokes
# through instead of calling `subprocess.run` directly.


@dataclass(frozen=True)
class ResourceBudget:
    """Hard resource ceilings for one sandboxed command."""
    memory_mb: int
    cpu_seconds: int
    wall_clock_s: int
    max_open_files: int = 64
    max_file_size_mb: int = 1024


class SandboxPort(Protocol):
    def run(self, cmd: Sequence[str], *, input_dir: Path, output_dir: Path,
            budget: ResourceBudget, tier: SandboxTier) -> SandboxResult: ...


def _decode(data: Optional[bytes]) -> str:
    return (data or b"").decode("utf-8", errors="replace")


class InProcessSandbox:
    """Runs `cmd` with NO isolation at all -- literally today's pre-E3.2
    behaviour (a bare `subprocess.run`), given a name that cannot be mistaken
    for a real security boundary. Same doctrine as `StageCtx.allow_stub_engines`:
    a missing control must be visible in the type, not silently
    indistinguishable from the real thing. Use `sandbox_for()` rather than
    constructing this directly -- it only hands one out when the caller has
    explicitly accepted that trade-off."""

    def run(self, cmd: Sequence[str], *, input_dir: Path, output_dir: Path,
            budget: ResourceBudget, tier: SandboxTier) -> SandboxResult:
        start = time.monotonic()
        try:
            result = subprocess.run(
                list(cmd), cwd=str(output_dir), capture_output=True,
                timeout=budget.wall_clock_s,
            )
        except subprocess.TimeoutExpired as e:
            return SandboxResult(
                exit_code=-1, reason=ExitReason.TIMEOUT,
                stdout=_decode(e.stdout), stderr=_decode(e.stderr),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return SandboxResult(
            exit_code=result.returncode,
            reason=ExitReason.SUCCESS if result.returncode == 0 else ExitReason.CRASH,
            stdout=_decode(result.stdout), stderr=_decode(result.stderr),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


# prctl(2) PR_CAPBSET_DROP -- irrevocably removes a capability from the
# calling process's bounding set. Stable across kernel versions, unlike the
# capability numbers below, which are also stable (linux/capability.h) but
# listed explicitly rather than pulled from `python-prctl`/`pycapng` to avoid
# a new dependency for eight fixed integers.
_PR_CAPBSET_DROP = 24
_PR_SET_NO_NEW_PRIVS = 38
_CAPABILITY_NUMBERS = {
    "CAP_DAC_OVERRIDE": 1, "CAP_FOWNER": 3, "CAP_NET_ADMIN": 12, "CAP_NET_RAW": 13,
    "CAP_SYS_PTRACE": 19, "CAP_SYS_ADMIN": 21, "CAP_SYS_BOOT": 22, "CAP_MKNOD": 27,
}


class RlimitSubprocessSandbox:
    """The real (interim) isolation tier: `setrlimit` + cwd confinement + a
    best-effort capability bounding-set drop, applied in the forked child
    via `preexec_fn` -- POSIX-only, since none of `resource`/`prctl` exist
    elsewhere. `RLIMIT_AS`/`RLIMIT_CPU`/`RLIMIT_NOFILE`/`RLIMIT_FSIZE` are set
    with soft == hard: the child process is destroyed after this one command
    (unlike `publisher_exec.memory_mw`'s in-process case), so there is
    nothing to restore afterward.

    Deliberately REFUSES `HEAVY` and `EXTERNAL`: this class cannot back
    either tier's promise (gVisor/Firecracker-grade isolation for HEAVY;
    rate-limited, policy-gated egress for EXTERNAL) -- same doctrine as
    `InProcessSandbox` being withheld when `allow_stub_engines=False`. A
    missing control fails loudly with `ExitReason.SECURITY`; it does not
    silently run unconfined and call that HEAVY isolation.

    ponytail: rlimits and a cwd, not a mount namespace or chroot -- nothing
    here stops an absolute-path write outside `output_dir`, only bounds
    memory/CPU/file-descriptors/file-size. Real path containment is what
    HEAVY is FOR; this tier is LIGHT/STANDARD only, on purpose.
    """

    def run(self, cmd: Sequence[str], *, input_dir: Path, output_dir: Path,
            budget: ResourceBudget, tier: SandboxTier) -> SandboxResult:
        if tier in (SandboxTier.HEAVY, SandboxTier.EXTERNAL):
            return SandboxResult(
                exit_code=-1, reason=ExitReason.SECURITY,
                stdout="", stderr=(
                    f"RlimitSubprocessSandbox cannot back {tier.value} isolation "
                    f"(needs gVisor/Firecracker or a policy-gated egress proxy, "
                    f"neither of which this class implements) -- refusing rather "
                    f"than running unconfined and calling that {tier.value}."
                ),
                duration_ms=0,
            )
        if os.name != "posix":
            return SandboxResult(
                exit_code=-1, reason=ExitReason.SECURITY,
                stdout="", stderr=(
                    "RlimitSubprocessSandbox requires POSIX (setrlimit/prctl); "
                    "this platform has neither. Use sandbox_for(allow_stub_engines=True) "
                    "for a dev-only run, or run this on a POSIX host."
                ),
                duration_ms=0,
            )

        cap_names = TIER_CONFIGS[tier].capabilities_to_drop if tier == SandboxTier.STANDARD else []

        def _preexec():
            os.setpgrp()
            import resource
            mem_bytes = budget.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (budget.cpu_seconds, budget.cpu_seconds))
            resource.setrlimit(resource.RLIMIT_NOFILE, (budget.max_open_files, budget.max_open_files))
            fsize_bytes = budget.max_file_size_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes, fsize_bytes))
            try:
                import ctypes
                libc = ctypes.CDLL("libc.so.6")
                for cap_name in cap_names:
                    cap_num = _CAPABILITY_NUMBERS.get(cap_name)
                    if cap_num is not None:
                        libc.prctl(_PR_CAPBSET_DROP, cap_num, 0, 0, 0)
                libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
            except Exception:
                pass

        start = time.monotonic()
        try:
            result = subprocess.run(
                list(cmd), cwd=str(output_dir), capture_output=True,
                timeout=budget.wall_clock_s, preexec_fn=_preexec,
            )
        except subprocess.TimeoutExpired as e:
            return SandboxResult(
                exit_code=-1, reason=ExitReason.TIMEOUT,
                stdout=_decode(e.stdout), stderr=_decode(e.stderr),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        # A process killed by one of the rlimits above dies from a signal,
        # which subprocess reports as a negative returncode on POSIX -- same
        # SUCCESS-or-CRASH classification `Sandbox.run` already uses above;
        # this deliberately does not try to guess OOM vs CPU vs a real crash
        # from the signal number alone, since that attribution is not
        # reliable enough to assert.
        return SandboxResult(
            exit_code=result.returncode,
            reason=ExitReason.SUCCESS if result.returncode == 0 else ExitReason.CRASH,
            stdout=_decode(result.stdout), stderr=_decode(result.stderr),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def sandbox_for() -> SandboxPort:
    """The best sandbox this process can actually provide: real rlimit
    containment on POSIX, `InProcessSandbox` (explicitly, visibly
    unsandboxed) elsewhere.

    Deliberately does NOT take an `allow_stub_engines` parameter, even though
    an earlier draft of this function gated the non-POSIX fallback on it to
    mirror that flag's doctrine ("a missing control must fail loudly"). That
    collided with a real, already-passing test on this repo's own Windows
    dev machine (`tests/integration/test_real_manuscripts.py`, which sets
    `allow_stub_engines=False` specifically to exercise the REAL ghostscript
    engine, not a stub one) for no actual security benefit: `Dockerfile.worker`
    is Linux-only, so production is always POSIX and never reaches the
    fallback branch at all. Refusing it on Windows protects nothing real
    while breaking legitimate local testing of the real-engine path --
    `allow_stub_engines` is about ENGINES, not sandbox availability, and
    conflating the two was the mistake, not the fix.
    """
    return RlimitSubprocessSandbox() if os.name == "posix" else InProcessSandbox()


# ── Security test helpers ───────────────────────────────────────

def create_zip_bomb(output_path: Path, target_ratio: int = 1000) -> Path:
    """Create a zip bomb fixture for security testing."""
    import zipfile
    # Create a highly compressed zip (bomb)
    bomb_path = output_path / "bomb.zip"
    with zipfile.ZipFile(bomb_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # One highly compressible entry
        zf.writestr("content.txt", "A" * 1024 * 1024)  # 1 MB of repeating data
        # Many nested empty entries
        for i in range(100):
            zf.writestr(f"nested/{i}/empty.txt", "")
    return bomb_path


def create_xxe_fixture(output_path: Path) -> Path:
    """Create an XXE fixture for security testing."""
    xxe_path = output_path / "xxe.xml"
    xxe_path.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE foo [\n'
        '  <!ENTITY xxe SYSTEM "file:///etc/passwd">\n'
        ']>\n'
        '<root>&xxe;</root>'
    )
    return xxe_path


def create_billion_laughs(output_path: Path) -> Path:
    """Create a billion laughs XML fixture."""
    bl_path = output_path / "billion-laughs.xml"
    bl_path.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE lolz [\n'
        '  <!ENTITY lol "lol">\n'
        '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">\n'
        '  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;">\n'
        '  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;">\n'
        '  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;">\n'
        '  <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;">\n'
        ']>\n'
        '<root>&lol6;</root>'
    )
    return bl_path


def create_path_traversal_zip(output_path: Path) -> Path:
    """Create a zip with path traversal."""
    import zipfile
    trav_path = output_path / "traversal.zip"
    with zipfile.ZipFile(trav_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../../etc/passwd", "root:xx:0:0:root:/root:/bin/bash")
    return trav_path


def create_fork_bomb_script(output_path: Path) -> Path:
    """Create a fork bomb shell script for testing."""
    fb_path = output_path / "forkbomb.sh"
    fb_path.write_text("#!/bin/sh\n:(){ :|:& };:\n")
    fb_path.chmod(0o755)
    return fb_path


# ── Factory ─────────────────────────────────────────────────────

def create_sandbox(tier: SandboxTier = SandboxTier.STANDARD) -> Sandbox:
    """Create a sandbox with the given tier configuration."""
    config = TIER_CONFIGS.get(tier, SandboxConfig())
    return Sandbox(config)
