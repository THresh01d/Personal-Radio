"""按业务组织HTTP入口，不让路由反向导入应用装配模块。"""

from app.api import audio_observations, chat, library, online_catalog, pages, player, settings, taste

ROUTERS = (settings.router, library.router, chat.router, taste.router, player.router,
           audio_observations.router, online_catalog.router, pages.router)
