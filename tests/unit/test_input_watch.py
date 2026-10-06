from racp_agent.broker.input_watch import InterruptionCounter


def test_hook_self_marker_requires_injection_flag_and_keeps_no_input_history() -> None:
    counter = InterruptionCounter(123456)
    counter.input(0x10, 123456, 0x10)
    counter.input(1, 123456, 1)
    assert counter.sequence == 0 and counter.own_sequence == 2
    counter.input(0, 123456, 0x10)  # A coincidentally equal physical marker is still foreign.
    counter.input(0x10, 0, 0x10)
    counter.input(3, 54321, 1)  # Other injected / lower-integrity input is an interruption too.
    assert counter.sequence == 3 and not counter.windows


def test_window_handle_reuse_and_bounded_eviction_never_revalidate_old_identity() -> None:
    counter = InterruptionCounter(1)
    first = counter.generation(42)
    counter.window_event(0x8001, 42, -4, 0)  # Child accessibility events do not replace HWNDs.
    assert first == counter.generation(42)
    counter.window_event(0x8001, 42, 0, 0)
    assert first != counter.generation(42)
    second = counter.generation(42)
    counter.window_event(0x8000, 42, 0, 0)
    assert second != counter.generation(42)
    for handle in range(5000):
        counter.generation(handle + 10000)
    assert len(counter.windows) <= 4096 and first != counter.generation(42)
    counter.window_event(3, 7, 0, 0)
    counter.window_event(3, 42, 0, 0)
    assert counter.foreground_sequence == 2
