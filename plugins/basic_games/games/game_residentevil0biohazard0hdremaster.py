import os
from pathlib import Path

import mobase
from PyQt6.QtCore import QDir
from ..basic_game import BasicGame
from ..steam_utils import find_steam_path


class ResidentEvilBiohazardModDataChecker(mobase.ModDataChecker):
    def __init__(self):
        super().__init__()

    valid_root = False
    valid_file = False
    
    def checkFiletreeEntry(self, path: str, entry: mobase.FileTreeEntry) -> mobase.IFileTree.WalkReturn:
        VALID_FILE_EXTENSIONS = [".arc", ".wmv", ".stqr"]
        if entry.isFile():
            name, ext = os.path.splitext(entry.name())
            if ext in VALID_FILE_EXTENSIONS:
                self.valid_file = True
                return mobase.IFileTree.WalkReturn.STOP
        return mobase.IFileTree.WalkReturn.CONTINUE
    
    def dataLooksValid(self, filetree: mobase.IFileTree) -> mobase.ModDataChecker.CheckReturn:
        VALID_ROOT_FOLDERS = [ "arc","effect","event","model","motion","movie","sa","scene","scheduler","scr","serial.srt","shader","sound","system","ui", ]
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


class ResidentEvil0Biohazard0(BasicGame):
    Name = "Resident Evil 0 Support Plugin"
    Author = "MikoMuto"
    Version = "1.0.0"

    GameName = "Resident Evil 0"
    GameShortName = "residentevil0biohazard0hdremaster"
    GameNexusName = "residentevil0biohazard0hdremaster"
    GameSteamId = 339340
    GameBinary = "re0hd.exe"
    GameDataPath = "nativePC"
    GameSaveExtension = "bin"

    def __init__(self):
        BasicGame.__init__(self)
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer):
        super().init(organizer)
        self._organizer = organizer
        self._register_feature(ResidentEvilBiohazardModDataChecker())
        return True

    @staticmethod
    def get_cloud_save_directory():
        steam_path = Path(find_steam_path())
        user_data = steam_path.joinpath("userdata")
        for child in user_data.iterdir():
            name = child.name
            try:
                steam_ident = int(name)
            except ValueError:
                steam_ident = -1
            if steam_ident == -1:
                continue
            cloud_saves = child.joinpath("339340", "remote")
            if cloud_saves.exists() and cloud_saves.is_dir():
                return str(cloud_saves)
        return None

    def savesDirectory(self) -> QDir:
        if self.is_steam():
            cloud_saves = self.get_cloud_save_directory()
            if cloud_saves is not None:
                return QDir(cloud_saves)
        return None
