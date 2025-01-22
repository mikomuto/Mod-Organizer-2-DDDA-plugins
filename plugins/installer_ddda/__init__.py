# __init__.py

import mobase

from .installer_ddda import dddaInstaller

def createPlugin() -> dddaInstaller:
    return dddaInstaller()