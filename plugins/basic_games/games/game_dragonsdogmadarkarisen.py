import os
from pathlib import Path

import mobase
from PyQt6.QtCore import QDir
from ..basic_game import BasicGame
from ..steam_utils import find_steam_path


class DragonsDogmaDarkArisenModDataChecker(mobase.ModDataChecker):
    def __init__(self):
        super().__init__()

    valid_root = False
    valid_file = False
    
    def checkFiletreeEntry(self, path: str, entry: mobase.FileTreeEntry) -> mobase.IFileTree.WalkReturn:
        VALID_FILE_EXTENSIONS = [".arc", ".pck", ".wmv", ".sngw"]
        if entry.isFile():
            name, ext = os.path.splitext(entry.name())
            if ext in VALID_FILE_EXTENSIONS:
                self.valid_file = True
                return mobase.IFileTree.WalkReturn.STOP
        return mobase.IFileTree.WalkReturn.CONTINUE
    
    def dataLooksValid(self, filetree: mobase.IFileTree) -> mobase.ModDataChecker.CheckReturn:
        VALID_ROOT_FOLDERS = ["rom", "movie", "sound"]
        self.valid_file = False
        self.valid_root = False       

        #check for valid root folder
        for entry in filetree:
            if isinstance(entry, mobase.IFileTree):
                if entry.name().lower() in VALID_ROOT_FOLDERS:
                    self.valid_root = True
                #always keep rootbuilder mods active
                if entry.name().lower() == "root":
                    return mobase.ModDataChecker.VALID

        #check for valid file
        filetree.walk(self.checkFiletreeEntry, os.sep)

        if (self.valid_root and self.valid_file):
            return mobase.ModDataChecker.VALID

        return mobase.ModDataChecker.INVALID


class DragonsDogmaDarkArisen(BasicGame):
    Name = "Dragon's Dogma: Dark Arisen Support Plugin"
    Author = "Luca/EzioTheDeadPoet/MikoMuto"
    Version = "1.2.2"

    GameName = "Dragon's Dogma: Dark Arisen"
    GameShortName = "dragonsdogma"
    GameNexusName = "dragonsdogma"
    GameSteamId = 367500
    GameGogId = 1242384383
    GameBinary = "DDDA.exe"
    GameDataPath = "nativePC"
    GameSupportURL = (
        "https://github.com/ModOrganizer2/modorganizer-basic_games/wiki/"
        + "Game:-Dragon's-Dogma:-Dark-Arisen"
    )
    GameSaveExtension = "sav"

    def __init__(self):
        BasicGame.__init__(self)
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer):
        super().init(organizer)
        self._organizer = organizer
        self._register_feature(DragonsDogmaDarkArisenModDataChecker())
        return True

    @staticmethod
    def get_cloud_save_directory():
        steam_path = Path(find_steam_path())
        user_data = steam_path.joinpath("user_data")
        for child in user_data.iterdir():
            name = child.name
            try:
                steam_ident = int(name)
            except ValueError:
                steam_ident = -1
            if steam_ident == -1:
                continue
            cloud_saves = child.joinpath("367500", "remote")
            if cloud_saves.exists() and cloud_saves.is_dir():
                return str(cloud_saves)
        return None

    def savesDirectory(self) -> QDir:
        documents_saves = QDir(
            str(os.getenv("LOCALAPPDATA"))
            + "\\GOG.com\\Galaxy\\Applications\\49987265717041704"
            + "\\Storage\\Shared\\Files"
        )
        if self.is_steam():
            cloud_saves = self.get_cloud_save_directory()
            if cloud_saves is not None:
                return QDir(cloud_saves)
        return documents_saves
