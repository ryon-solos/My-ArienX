"""Guard the primary HUD geometry while status indicators evolve."""

from ui import _DEFAULT_W, _DEFAULT_H, _MIN_W, _MIN_H, _LEFT_W, _RIGHT_W


def run() -> None:
    assert (_DEFAULT_W, _DEFAULT_H) == (980, 700)
    assert (_MIN_W, _MIN_H) == (820, 580)
    assert (_LEFT_W, _RIGHT_W) == (270, 340)
    print("UI geometry diagnostics passed")


if __name__ == "__main__":
    run()
