import re
import os

import mobase

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union, cast

from PyQt6.QtCore import qInfo
from PyQt6.QtWidgets import QApplication

class dddaInstaller(mobase.IPluginInstallerSimple):

    """
    This is the actual plugin. MO2 has two types of installer plugin, this one is
    "simple", i.e., it will work directly on the file-tree contained in the archive.
    The purpose of the installer is to take the file-tree from the archive, check if
    it is valid (for this installer) and then modify it if required before extraction.
    """

    _organizer: mobase.IOrganizer

    def __init__(self):
        super().__init__()

    def init(self, organizer: mobase.IOrganizer):
        self._organizer = organizer
        return True

    def name(self):
        return "DDDA Installer"

    def localizedName(self) -> str:
        return self.tr("DDDA Installer")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.tr("Installer for Dragon's Dogma Dark Arisen mods.")

    def version(self):
        return mobase.VersionInfo(1, 0, 0)

    def isActive(self):
        return self._organizer.pluginSetting(self.name(), "enabled")

    def settings(self):
        return [
            mobase.PluginSetting("enabled", "check to enable this plugin", True),
            mobase.PluginSetting("priority", "priority of this installer", 120),
        ]

    def priority(self) -> int:
        return cast(int, self._organizer.pluginSetting(self.name(), "priority"))

    def isManualInstaller(self) -> bool:
        return False

    def tr(self, value: str) -> str:
        # we need this to translate string in Python. Check the common documentation
        # for more details
        return QApplication.translate("DDDAInstaller", value)

    def isArchiveSupported(self, tree: mobase.IFileTree) -> bool:
        
        game_name = self._organizer.managedGame().gameName()

        if (game_name == "Dragon's Dogma: Dark Arisen"):
            #Filter out correctly structured mods?
            return True
        
        return False

    plugin_debug = False
    fixable_structure = False
    RE_BODYFILE = re.compile(r"[fm]_[aiw]_\w+.arc")
    RE_DL1_BODYFILE = re.compile(r"[fm]_a_\w+820\d.arc")
    RE_HEXEXTENSION = re.compile(r"[\.0-9a-fA-F]{8}")
    VALID_ROOT_FOLDERS = ["rom", "movie", "sound"]
    VALID_CHILD_FOLDERS = [
        "dl1",
        "enemy",
        "eq",
        "etc",
        "event",
        "gui",
        "h_enemy",
        "ingamemanual",
        "item_b",
        "map",
        "message",
        "mnpc",
        "npc",
        "npcfca",
        "npcfsm",
        "om",
        "pwnmsg",
        "quest",
        "shell",
        "sk",
        "sound",
        "stage",
        "voice",
        "wp",
        "bbsrpg_core",
        "bbs_rpg",
        "game_main",
        "Initialize",
        "title",
    ]
    VALID_FILE_EXTENSIONS = [
        ".arc",
        ".pck",
        ".wmv",
        ".sngw",
    ]
    NO_CHILDFOLDERS = ["a_acc", "i_body", "w_leg"]
    MoveList: list[tuple[mobase.FileTreeEntry, str]] = []
    DeleteList: list[tuple[mobase.FileTreeEntry, str]] = []

    def checkFiletreeEntry(self, path: str, entry: mobase.FileTreeEntry) -> mobase.IFileTree.WalkReturn:
        # we check for valid game files within a valid root folder
        path_root = path.split(os.sep)[0]
        entry_name, entry_extension = os.path.splitext(entry.name())

        if self.plugin_debug:
            qInfo(f"checkFiletreeEntry path_root:{path_root} path:{path} entry:{entry.name()}")
        if entry.isDir():
            parent = entry.parent()
            if path_root not in self.VALID_ROOT_FOLDERS:
                if (parent in self.VALID_ROOT_FOLDERS and entry in self.VALID_CHILD_FOLDERS):
                    if self.plugin_debug:
                        qInfo(f"Adding child to move list: {path} {entry.name()}")
                    self.MoveList.append((entry, "rom" + os.sep))
                    self.fixable_structure = True
                    return mobase.IFileTree.WalkReturn.SKIP
        else:
            if path_root in self.VALID_ROOT_FOLDERS:
                name, ext = os.path.splitext(entry.name())
                if ext in self.VALID_FILE_EXTENSIONS:
                    self.valid_structure = True
                    if self.plugin_debug:
                        qInfo("checkFiletreeEntry valid")
                    return mobase.IFileTree.WalkReturn.STOP
            is_body_file = self.RE_BODYFILE.match(entry.name())
            if is_body_file:
                self.fixable_structure = True
                parent_folder = str(entry.name())[0]
                grandparent_folder = re.split(r"_(?=._)|[0-9]", str(entry.name()))[1]
                if self.plugin_debug:
                    qInfo(f"Adding to move list: {path + entry.name()}")
                if grandparent_folder in self.NO_CHILDFOLDERS:
                    target_path = os.path.join("/rom/eq/", grandparent_folder)
                    self.MoveList.append((entry, os.path.normpath(target_path)))
                else:
                    target_path = os.path.join("/rom/eq/", grandparent_folder, parent_folder)
                    self.MoveList.append((entry, os.path.normpath(target_path)))
            has_hex_file_extension = self.RE_HEXEXTENSION.match(entry_extension)
            # ignore item, sound, and game manual files with hex extenstions
            folder_exlusions = ["sound", "ingamemanual", "MatAnim_Burn", "item"]
            if has_hex_file_extension and not any(x in path for x in folder_exlusions):
                qInfo(f"Invalid TEX file found: {path + entry.name()}")
                self.MoveList.append((entry, path + entry_name + ".tex"))
        return mobase.IFileTree.WalkReturn.CONTINUE

    def install(self, name: mobase.GuessedString, filetree: mobase.IFileTree, version: str, nexus_id: int,) -> Union[mobase.InstallResult, mobase.IFileTree]:
        """
        Perform the actual installation.

        Args:
            name: The "name" of the mod. This can be updated to change the name of the
                mod.
            filetree: The original archive tree.
            version: The original version of the mod.
            nexus_id: The original ID of the mod.

        Returns: We either return the modified file-tree (if the installation was
            successful), or a InstallResult otherwise.

        Note: It is also possible to return a tuple (InstallResult, IFileTree, str, int)
            containing where the two last members correspond to the new version and ID
            of the mod, in case those were updated by the installer.
        """
        #we use nexus mod ID and file ID to match with install script
        mod_identifier = str(nexus_id) + "-" + version

        #check for install script
        script_file = os.path.join(self._organizer.basePath(), "/plugins/installer_ddda/scripts/", mod_identifier + ".txt")
        if self.plugin_debug:
            qInfo("script_file: " + script_file)
        if os.path.isfile(script_file):
            qInfo("found installer script")

        # check filetree
        filetree.walk(self.checkFiletreeEntry, os.sep)
        
        if self.plugin_debug:
            qInfo("installer_ddda mod_name: " + name.__str__())
            qInfo("installer_ddda mod_identifier: " + mod_identifier)

        if self.fixable_structure:
            size_delete_list = len(self.DeleteList)
            size_move_list = len(self.MoveList)
       
            if size_delete_list > 0 and size_move_list > 0:
                self.valid_structure = True
                return filetree
            if size_delete_list > 0:
                for entry, path in reversed(self.DeleteList):
                    if self.plugin_debug:
                        qInfo(f"Deleting: {path + entry.name()}")
                    filetree.move(
                        entry, "/delete/" + entry.name(), policy=mobase.IFileTree.MERGE
                    )
            if size_move_list > 0:
                for entry, path in reversed(self.MoveList):
                    entry_path = filetree.pathTo(entry, os.sep)
                    path_root = entry_path.split(os.sep)[0]
                    if self.plugin_debug:
                        qInfo(f"Moving: {entry.name()} to {path} " + os.sep)
                    filetree.move(entry, path + os.sep, policy=mobase.IFileTree.MERGE)
                    filetree.remove(path_root)  # remove empty branch
            # remove invalid root folders
            filetree.remove("delete")
            return filetree

        if self.plugin_debug:
            qInfo("Not attempting install")
        return mobase.InstallResult.NOT_ATTEMPTED