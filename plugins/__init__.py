# __init__.py

from typing import List

import mobase

from .arctool_extract_integration import ARCExtract
from .arctool_merge_integration import ARCMerge

def createPlugins() -> List[mobase.IPlugin]:
    return [ARCExtract(), ARCMerge()]