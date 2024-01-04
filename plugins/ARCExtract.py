# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'Run ARCTool' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# asks user for arc file to extract, copies vanilla arc from game folder to a temp folder, extracts this arc in all mods installed, deletes identical to master files, then exits

import os
import re
import shutil
import pathlib
import sys
import filecmp
from collections import defaultdict

from PyQt6.QtCore import QCoreApplication, qCritical, QFileInfo, qInfo
from PyQt6.QtGui import QIcon, QFileSystemModel
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog

if "mobase" not in sys.modules:
    import mock_mobase as mobase

class ARCToolInvalidPathException(Exception):
    """Thrown if ARCTool.exe path can't be found"""
    pass

class ARCToolMissingException(Exception):
    """Thrown if selected ARC file can't be found"""
    pass

class ARCToolInactiveException(Exception):
    """Thrown if ARCTool.exe is installed to an inactive mod"""
    pass

class ARCFileMissingException(Exception):
    """Thrown if selected ARC file can't be found"""
    pass

class ARCExtract(mobase.IPluginTool):

    def __init__(self):
        super(ARCExtract, self).__init__()
        self._organizer = None
        self.__parentWidget = None

    def init(self, organizer):
        self._organizer = organizer
        return True

    def name(self):
        return "ARC Extractor"

    def localizedName(self):
        return self.__tr("ARC Extractor")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.__tr("Runs ARCTool on mods to extract files")

    def version(self):
        return mobase.VersionInfo(1, 0, 0, 0)

    def requirements(self):
        return [
            mobase.PluginRequirementFactory.gameDependency("Dragon's Dogma: Dark Arisen")
        ]

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.name(), "enabled")

    def settings(self):
        return [
        mobase.PluginSetting("enabled", "enable this plugin", True),
        mobase.PluginSetting("ARCTool-path", self.__tr("Path to ARCTool.exe"), ""),
        mobase.PluginSetting("initialised", self.__tr("Settings have been initialised.  Set to False to reinitialise them."), False),
        mobase.PluginSetting("remove-ITM", self.__tr("Remove identical to master files when extracting ARC files"), True),
        mobase.PluginSetting("delete-ARC", self.__tr("Delete .arc file after extracting"), True),
        mobase.PluginSetting("hide-ARC", self.__tr("Hide .arc file after extracting"), False),
        mobase.PluginSetting("remove-temp", self.__tr("Delete temporary files and folders"), True),
        mobase.PluginSetting("log-enabled", self.__tr("Enable logs"), False),
        mobase.PluginSetting("verbose-log", self.__tr("Verbose logs"), False),
        mobase.PluginSetting("dev-option", self.__tr("Extract/Merge textures, gameplay, enemy, and binary text files"), False),
            ]

    def displayName(self):
        return self.__tr("ARC Extract")

    def tooltip(self):
        return self.__tr("Unpacks all ARC files")

    def icon(self):
        ARCToolPath = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        if os.path.exists(ARCToolPath):
            # We can't directly grab the icon from an executable, but this seems like the simplest alternative.
            fin = QFileInfo(ARCToolPath)
            model = QFileSystemModel()
            model.setRootPath(fin.path())
            return model.fileIcon(model.index(fin.filePath()))
        else:
            # Fall back to where the user might have put an icon manually.
            return QIcon("plugins/ARCTool.ico")

    def setParentWidget(self, widget):
        self.__parentWidget = widget

    def display(self):
        args = []

        if not bool(self._organizer.pluginSetting(self.name(), "initialised")):
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")

        try:
            executable = self.get_arctool_path()
        except ARCToolInvalidPathException:
            QMessageBox.critical(self.__parentWidget, self.__tr("ARCTool path not specified"), self.__tr("The path to ARCTool.exe wasn't specified. The tool will now exit."))
            return
        except ARCToolMissingException:
            QMessageBox.critical(self.__parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool.exe not found. Resetting tool."))
            return
        except ARCToolInactiveException:
            # Error has already been displayed, just quit
            return

        self._organizer.setPluginSetting(self.name(), "initialised", True)

        self.processMods(executable)


    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)

    def get_arctool_path(self):
        savedPath = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        # ARCTool must be installed within the game's data directory or a mod folder
        mod_directory = self.__getModDirectory()
        game_directory = pathlib.Path(self._organizer.managedGame().dataDirectory().absolutePath())
        pathlibPath = pathlib.Path(savedPath)
        if not os.path.exists(pathlibPath):
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.name(), "initialised", False)
            raise ARCToolMissingException
        inGoodLocation = self.__withinDirectory(pathlibPath, mod_directory)
        inGoodLocation |= self.__withinDirectory(pathlibPath, game_directory)
        if not pathlibPath.is_file() or not inGoodLocation:
            QMessageBox.information(self.__parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool path invalid or not set. \n\nARCTool must be visible within the VFS, so choose an installation either within the game's data directory or within a mod folder. \n\nThis setting can be updated in the Plugins tab of the Mod Organizer Settings menu."))
            while True:
                path = QFileDialog.getOpenFileName(self.__parentWidget, self.__tr("Locate ARCTool.exe"), str(mod_directory), "ARCTool.exe")[0]
                if path == "":
                    # Cancel was pressed
                    raise ARCToolInvalidPathException
                pathlibPath = pathlib.Path(path)
                inGoodLocation = self.__withinDirectory(pathlibPath, mod_directory)
                inGoodLocation |= self.__withinDirectory(pathlibPath, game_directory)
                if pathlibPath.is_file() and inGoodLocation:
                    self._organizer.setPluginSetting(self.name(), "ARCTool-path", path)
                    savedPath = path
                    break
                else:
                    QMessageBox.information(self.__parentWidget, self.__tr("Not a compatible location..."), self.__tr("ARCTool only works when within the VFS, so must be installed within a mod folder. Please select a different ARC installation"))
        # Check the mod is actually enabled
        if self.__withinDirectory(pathlibPath, mod_directory):
            ARCModName = None
            for path in pathlibPath.parents:
                if path.parent.samefile(mod_directory):
                    ARCModName = path.name
                    break
            if (self._organizer.modList().state(ARCModName) & mobase.ModState.active) == 0:
                # ARC is installed to an inactive mod
                result = QMessageBox.question(self.__parentWidget, self.__tr("ARCTool mod deactivated"), self.__tr("ARCTool is installed to an inactive mod. Press Yes to activate it or Cancel to quit the tool"), QMessageBox.StandardButton(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel))
                if result == QMessageBox.StandardButton.Yes:
                    self._organizer.modList().setActive(ARCModName, True)
                else:
                    raise ARCToolInactiveException
        return savedPath

    def extract_vanilla_arc(self, executable, path):
        args = "-x -pc -dd -alwayscomp -txt -v 7"
        executablePath, executableName = os.path.split(executable)
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        mod_directory = self.__getModDirectory()
        arc_file_relative_path = os.path.relpath(path, mod_directory).split(os.path.sep, 1)[1]
        arc_folder_relative_path = os.path.splitext(arc_file_relative_path)[0]
        arc_file_folder_relative_path = os.path.split(arc_file_relative_path)[0]

        # copy vanilla arc to temp, extract, then delete if not already done
        extractedARCfolder = pathlib.Path(executablePath + os.sep + arc_folder_relative_path)
        if not (os.path.isdir(extractedARCfolder)):
            if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                qInfo("Extracting vanilla ARC: " + arc_file_relative_path)
            if (os.path.isfile(os.path.join(game_directory, arc_file_relative_path))):
                pathlib.Path(executablePath + os.sep +  arc_folder_relative_path).mkdir(parents=True, exist_ok=True)
                shutil.copy(os.path.normpath(os.path.join(game_directory, arc_file_relative_path)), os.path.normpath(executablePath + os.sep + arc_file_folder_relative_path))
                output = os.popen('"' + executable + '" ' + args + ' "' + os.path.normpath(executablePath + os.sep + arc_file_relative_path + '"')).read()
                if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                    qInfo(output)
                # remove .arc file
                os.remove(os.path.normpath(executablePath + os.sep + arc_file_relative_path))
                return True
            else:
                modName = os.path.relpath(path, mod_directory).split(os.path.sep, 1)[0]
                QMessageBox.critical(self.__parentWidget, self.__tr("Invalid ARC file path"), self.__tr("Mod: " + modName + "\nFile: " + arc_file_relative_path))
                if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                    qInfo("Invalid ARC file: " + path)
                return False
        else:
            return True

    def extractARCFile(self, executable, path):
        args = "-x -pc -dd -alwayscomp -txt -v 7"
        mod_directory = self.__getModDirectory()
        executablePath, executableName = os.path.split(executable)
        arc_file_relative_path = os.path.relpath(path, mod_directory).split(os.path.sep, 1)[1]
        arc_folder_relative_path = os.path.splitext(arc_file_relative_path)[0]
        master_arc_path = pathlib.Path(executablePath + os.sep +  arc_folder_relative_path)
        
        if bool(self._organizer.pluginSetting(self.name(), "dev-option")):
            args = args + " -tex -xfs -lot -gmd"
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            qInfo(f'Starting extractARCFile: {path}')
        # extract arc and remove ITM
        arc_file_path = os.path.splitext(path)[0]
        if pathlib.Path(path).exists():
            output = os.popen('"' + executable + '" ' + args + ' "' + str(path) + '"').read()
            if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                qInfo(output)
            # remove ITM
            if bool(self._organizer.pluginSetting(self.name(), "remove-ITM")):
                if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                    qInfo("Deleting duplicate files")
                def delete_same_files(dcmp):
                    for name in dcmp.same_files:
                        if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                            qInfo(f'Deleting duplicate file {os.path.join(dcmp.right, name)}')
                        os.remove(os.path.join(dcmp.right, name))
                    for sub_dcmp in dcmp.subdirs.values():
                        delete_same_files(sub_dcmp)
                dcmp = filecmp.dircmp(master_arc_path, arc_file_path)
                delete_same_files(dcmp)
                # delete empty folders
                for dirpath, dirnames, filenames in os.walk(arc_file_path, topdown=False):
                    for dirname in dirnames:
                        full_path = os.path.join(dirpath, dirname)
                        if not os.listdir(full_path):
                            if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                                qInfo(f'Deleting empty folder {full_path}')
                            os.rmdir(full_path)
            # delete arc
            if bool(self._organizer.pluginSetting(self.name(), "delete-ARC")):
                os.remove(path)
            if bool(self._organizer.pluginSetting(self.name(), "hide-ARC")):
                os.rename(path, str(path) + ".mohidden")

        return True

    def processMods(self, executable):
        arcFilesSeenDict = defaultdict(list)
        duplicateARCFileDict = defaultdict(list)
        mod_directory = self.__getModDirectory()
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        executablePath, executableName = os.path.split(executable)
        arctool_mod = os.path.relpath(executablePath, mod_directory).split(os.path.sep, 1)[0]
        
        # get mod active list
        modActiveList = []
        modlist = self._organizer.modList()
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if mod_name != arctool_mod and 'Merged ARC' not in mod_name:
                    modActiveList.append(mod_name)

        myProgressD = QProgressDialog(self.__tr("Starting extraction..."), self.__tr("Cancel"), 0, 0, self.__parentWidget)
        myProgressD.forceShow()
        myProgressD.setFixedWidth(420)
        QCoreApplication.processEvents()
        
        # build list of active mod duplicate arc files to extract
        for mod_name in modActiveList:
            myProgressD.setLabelText(f'Scanning: {mod_name}')
            QCoreApplication.processEvents()
            if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                qInfo(f'Scanning: {mod_name}')
            for dirpath, dirnames, filenames in os.walk(mod_directory + os.path.sep + mod_name):
                if (myProgressD.wasCanceled()):
                    if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                        qInfo("Extract cancelled")
                    return
                # check for extracted arc folders
                for folder in dirnames:
                    full_path = dirpath + os.path.sep + folder + ".arc"
                    relative_path = os.path.relpath(full_path, mod_directory).split(os.path.sep, 1)[1]
                    if (os.path.isfile(os.path.normpath(game_directory + os.path.sep + relative_path))):
                        if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                            qInfo(f'ARC Folder: {full_path}')                            
                        if any(relative_path in x for x in arcFilesSeenDict):
                            mod_where_first_seen = arcFilesSeenDict[relative_path][0]
                            duplicateARCFileDict[relative_path].append(mod_where_first_seen)
                            if mod_name not in duplicateARCFileDict[relative_path]:
                                duplicateARCFileDict[relative_path].append(mod_name)
                        else:
                            if mod_name not in arcFilesSeenDict[relative_path]:
                                arcFilesSeenDict[relative_path].append(mod_name)
                        # extract vanilla arc file if needed
                        if not self.extract_vanilla_arc(executable, dirpath + os.sep + folder + ".arc"):
                            myProgressD.close()
                            return
                for file in filenames:
                    if file.endswith(".arc"):
                        full_path = dirpath + os.path.sep + file
                        relative_path = os.path.relpath(full_path, mod_directory).split(os.path.sep, 1)[1]
                        if any(relative_path in x for x in arcFilesSeenDict):
                            mod_where_first_seen = arcFilesSeenDict[relative_path][0]
                            duplicateARCFileDict[relative_path].append(mod_where_first_seen)
                            if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                                qInfo(f'Duplicate ARC: {os.path.normpath(dirpath + os.sep + file)}')
                            QCoreApplication.processEvents()
                            if mod_name not in duplicateARCFileDict[relative_path]:
                                duplicateARCFileDict[relative_path].append(mod_name)
                            # extract vanilla arc file if needed
                            if not self.extract_vanilla_arc(executable, dirpath + os.sep + file):
                                myProgressD.close()
                                return
                        else:
                            if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                                qInfo(f'Unique ARC: {relative_path}')
                            if mod_name not in arcFilesSeenDict[relative_path]:
                                arcFilesSeenDict[relative_path].append(mod_name)
       
        # extract based on duplicates found
        for arcFile in duplicateARCFileDict:
            for mod_name in duplicateARCFileDict[arcFile]:
                if (myProgressD.wasCanceled()):
                    if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                        qInfo("Extract cancelled")
                    return
                if (os.path.isfile(mod_directory + os.sep + mod_name + os.sep + arcFile)):
                    self.extractARCFile(executable, mod_directory + os.sep + mod_name + os.sep + arcFile)                        

        myProgressD.close()
        QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("ARC file extraction complete"))
        self._organizer.refresh()

    def __getModDirectory(self):
        return self._organizer.modsPath()

    @staticmethod
    def __withinDirectory(innerPath, outerDir):
        for path in innerPath.parents:
            if path.samefile(outerDir):
                return True
        return False

def createPlugin():
    return ARCExtract()
