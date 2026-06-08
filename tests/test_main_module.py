def test_main_module_delegates_to_cli_main():
    import amap_service.__main__ as entry
    from amap_service.cli import main

    assert entry.main is main
