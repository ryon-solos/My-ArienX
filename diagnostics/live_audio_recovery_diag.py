"""Fast regression checks for Live microphone recovery decisions.

Run: python3 diagnostics/live_audio_recovery_diag.py
"""

from pathlib import Path
import ast


def _load_helper():
    source = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    wanted = {"INPUT_STALL_GRACE_SECONDS", "live_input_turn_stalled"}
    nodes = [node for node in tree.body if getattr(node, "name", None) in wanted
             or any(getattr(target, "id", None) in wanted
                    for target in getattr(node, "targets", []))]
    namespace: dict = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["live_input_turn_stalled"], namespace["INPUT_STALL_GRACE_SECONDS"]


def main() -> None:
    stalled, grace = _load_helper()
    assert not stalled(20, 10, 15, 9, 10), "a Live response must acknowledge the turn"
    assert not stalled(20, 10, 19, 9, 9), "active speech must not reconnect"
    assert stalled(20, 10, 10, 9, 9), "silent Live after a completed turn must recover"
    assert not stalled(20, 0, 10, 0, 0), "no utterance must not reconnect"
    print(f"PASS: Live audio recovery waits {grace}s and acknowledges any Live response")


if __name__ == "__main__":
    main()
