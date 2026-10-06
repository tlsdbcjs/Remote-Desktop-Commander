import asyncio


def create_loop() -> asyncio.AbstractEventLoop:
    # Gateway performs network I/O only. Windows Agent subprocesses/ConPTY run
    # independently on Proactor. Selector avoids CPython's RST shutdown leak.
    return asyncio.SelectorEventLoop()
