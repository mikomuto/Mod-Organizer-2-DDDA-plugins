# __init__.py

import mobase

from .installer import dddaInstaller

def createPlugin() -> dddaInstaller:
    return dddaInstaller()