"""装配应用不等于启动应用：避免测试或工具导入时拉起网关、打开曲库。"""

from app import main


def test_factory_does_not_start_resources(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("装配时不允许启动资源")

    monkeypatch.setattr(main, "ensure_library_schema", forbidden)
    monkeypatch.setattr(main.ncm, "ensure_gateway", forbidden)
    application = main.create_app()
    assert application.router.on_startup == [main._start_ncm_gateway]
    assert application.router.on_shutdown == [main._stop_ncm_gateway, application.state.hotkeys.stop]


def test_factory_has_independent_routes_and_identical_contract():
    first, second = main.create_app(), main.create_app()
    assert first is not second
    assert first.openapi() == second.openapi() == main.app.openapi()
    first.router.routes.clear()
    assert second.router.routes
