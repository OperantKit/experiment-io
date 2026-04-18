"""Smoke tests: package imports and exposes its public surface."""

from __future__ import annotations


def test_package_imports() -> None:
    import experiment_io

    assert hasattr(experiment_io, "__version__")
    assert isinstance(experiment_io.__version__, str)


def test_hw_public_protocols_importable() -> None:
    from experiment_io.hw import (
        Clock,
        HardwareClock,
        Operandum,
        Reinforcer,
        ResponseSample,
    )

    # HardwareClock is the deprecated alias; should be the same object.
    assert HardwareClock is Clock
    assert ResponseSample(operandum_index=0, timestamp=0.0).operandum_index == 0
    # Operandum / Reinforcer are runtime-checkable Protocols.
    assert isinstance(Operandum, type) or hasattr(Operandum, "__class_getitem__")
    assert isinstance(Reinforcer, type) or hasattr(Reinforcer, "__class_getitem__")


def test_backends_importable_without_optional_deps() -> None:
    # Pulling in the serial / hil_bridge modules must NOT require pyserial
    # or a Rust binary on disk.
    import experiment_io.hw.backends.hil_bridge  # noqa: F401
    import experiment_io.hw.backends.serial  # noqa: F401
    import experiment_io.hw.backends.virtual  # noqa: F401
