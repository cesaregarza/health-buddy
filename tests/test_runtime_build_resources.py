"""The image assembler refuses absent, unlimited or excessive RUN limits."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "runtime_install",
    Path(__file__).resolve().parents[1] / "packaging/install_runtime.py",
)
assert SPEC is not None and SPEC.loader is not None
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


class BuildResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="hb-build-resources-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def limits(self, version: int, memory: str, quota: str, period: str) -> None:
        values = (
            {"memory.max": memory, "cpu.max": f"{quota} {period}"}
            if version == 2
            else {
                "memory/memory.limit_in_bytes": memory,
                "cpu/cpu.cfs_quota_us": quota,
                "cpu/cpu.cfs_period_us": period,
            }
        )
        for name, value in values.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value + "\n")

    def test_both_versions_admit_the_same_caps(self) -> None:
        for version in (1, 2):
            with self.subTest(version=version):
                self.limits(version, str(2 * 1024**3), "100000", "100000")
                result = INSTALL.build_resources(self.root)
                self.assertEqual(result["cgroupVersion"], version)
                self.assertEqual(result["memoryBytes"], 2 * 1024**3)
                self.assertEqual(result["cpuQuota"], result["cpuPeriod"])

    def test_v1_unlimited_cpu_and_memory_are_refused(self) -> None:
        for memory, quota in (
            (str(2 * 1024**3), "-1"),
            (str(2**63 - 4096), "100000"),
        ):
            with self.subTest(memory=memory, quota=quota):
                self.limits(1, memory, quota, "100000")
                with self.assertRaises(ValueError):
                    INSTALL.build_resources(self.root)

    def test_v2_unlimited_limits_are_refused_without_v1_fallback(self) -> None:
        self.limits(1, str(2 * 1024**3), "100000", "100000")
        for memory, quota in (("max", "100000"), (str(2 * 1024**3), "max")):
            with self.subTest(memory=memory, quota=quota):
                self.limits(2, memory, quota, "100000")
                with self.assertRaises(ValueError):
                    INSTALL.build_resources(self.root)

    def test_excessive_zero_and_malformed_limits_are_refused(self) -> None:
        for version in (1, 2):
            for memory, quota, period in (
                (str(2 * 1024**3 + 1), "100000", "100000"),
                ("0", "100000", "100000"),
                ("1024", "100001", "100000"),
                ("1024", "0", "100000"),
                ("1024", "100000", "0"),
                ("1024", "100000", "1000001"),
                ("1024", "not-a-number", "100000"),
            ):
                with self.subTest(
                    version=version, memory=memory, quota=quota, period=period
                ):
                    self.limits(version, memory, quota, period)
                    with self.assertRaises(ValueError):
                        INSTALL.build_resources(self.root)

    def test_incomplete_v2_does_not_use_v1_limits(self) -> None:
        self.limits(1, str(2 * 1024**3), "100000", "100000")
        (self.root / "cgroup.controllers").write_text("cpu memory\n")
        with self.assertRaises(FileNotFoundError):
            INSTALL.build_resources(self.root)

    def test_missing_controller_is_refused(self) -> None:
        self.limits(1, str(2 * 1024**3), "100000", "100000")
        (self.root / "cpu/cpu.cfs_quota_us").unlink()
        with self.assertRaises(FileNotFoundError):
            INSTALL.build_resources(self.root)


if __name__ == "__main__":
    unittest.main()
