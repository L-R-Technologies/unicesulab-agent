"""Ícone na bandeja do sistema (suportado apenas no Windows).

Em outras plataformas (dev em macOS/Linux) o agente roda como janela
comum e fechar a janela encerra o processo.
"""

import logging
import sys
from collections.abc import Callable

logger = logging.getLogger(__name__)

_ICON_SIZE = 64
_ICON_BACKGROUND = (13, 148, 136)
_ICON_FOREGROUND = (255, 255, 255)


def is_tray_supported() -> bool:
    return sys.platform == "win32"


def _draw_icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (_ICON_SIZE, _ICON_SIZE), _ICON_BACKGROUND)
    draw = ImageDraw.Draw(image)
    # Silhueta de conector serial: trapézio com dois "pinos", sem depender de fontes
    draw.polygon([(12, 20), (52, 20), (46, 44), (18, 44)], fill=_ICON_FOREGROUND)
    draw.rectangle((22, 28, 28, 36), fill=_ICON_BACKGROUND)
    draw.rectangle((36, 28, 42, 36), fill=_ICON_BACKGROUND)
    return image


def create_tray_icon(on_open: Callable[[], None], on_quit: Callable[[], None]):
    """Cria e inicia o ícone da bandeja. Retorna None se indisponível."""
    if not is_tray_supported():
        return None

    try:
        import pystray
    except ImportError:
        logger.warning("pystray indisponível; agente rodará sem bandeja")
        return None

    menu = pystray.Menu(
        pystray.MenuItem("Abrir", lambda icon, item: on_open(), default=True),
        pystray.MenuItem("Encerrar", lambda icon, item: on_quit()),
    )
    icon = pystray.Icon(
        "unicesulab-agent",
        icon=_draw_icon_image(),
        title="UNICESULAB — Agente de captura",
        menu=menu,
    )
    icon.run_detached()
    return icon


def stop_tray_icon(icon: object | None) -> None:
    if icon is not None:
        icon.stop()
