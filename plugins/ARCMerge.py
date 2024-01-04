# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'Run ARCTool' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# asks user for a folder to compress to arc, copies vanilla arc files from game folder to a temp folder, copies all arc folder files in all mods installed to merge folder, compresses to .arc, then exits

import os
import shutil
import pathlib
import sys
import filecmp
import json
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

class ARCToolCompress(mobase.IPluginTool):

    def __init__(self):
        super(ARCToolCompress, self).__init__()
        self._organizer = None
        self.__parentWidget = None

    def init(self, organizer):
        self._organizer = organizer
        return True

    def name(self):
        return "ARC Merge"

    def localizedName(self):
        return self.__tr("ARC Merge")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.__tr("Runs ARCTool on mods to merge extracted .arc folders from mods")

    def version(self):
        return mobase.VersionInfo(1, 0, 0, 0)

    def requirements(self):
        return [
            mobase.PluginRequirementFactory.gameDependency("Dragon's Dogma: Dark Arisen")
        ]

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.__mainToolName(), "enabled")

    def settings(self):
        return []

    def displayName(self):
        return self.__tr("ARC Merge")

    def tooltip(self):
        return self.__tr("Merge extracted .arc files")

    def icon(self):
        ARCToolPath = self._organizer.pluginSetting(self.__mainToolName(), "ARCTool-path")
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

        if not bool(self._organizer.pluginSetting(self.__mainToolName(), "initialised")):
            self._organizer.setPluginSetting(self.__mainToolName(), "ARCTool-path", "")

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

        self._organizer.setPluginSetting(self.__mainToolName(), "initialised", True)

        self.__process_mods(executable)

    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)

    def get_arctool_path(self):
        savedPath = self._organizer.pluginSetting(self.__mainToolName(), "ARCTool-path")
        # ARCTool must be installed within the game's data directory or a mod folder
        modDirectory = self.__getModDirectory()
        gameDataDirectory = pathlib.Path(self._organizer.managedGame().dataDirectory().absolutePath())
        pathlibPath = pathlib.Path(savedPath)
        if not os.path.exists(pathlibPath):
            self._organizer.setPluginSetting(self.__mainToolName(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.__mainToolName(), "initialised", False)
            raise ARCToolMissingException
        inGoodLocation = self.__withinDirectory(pathlibPath, modDirectory)
        inGoodLocation |= self.__withinDirectory(pathlibPath, gameDataDirectory)
        if not pathlibPath.is_file() or not inGoodLocation:
            QMessageBox.information(self.__parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool path invalid or not set. \n\nARCTool must be visible within the VFS, so choose an installation either within the game's data directory or within a mod folder. \n\nThis setting can be updated in the Plugins tab of the Mod Organizer Settings menu."))
            while True:
                path = QFileDialog.getOpenFileName(self.__parentWidget, self.__tr("Locate ARCTool.exe"), str(modDirectory), "ARCTool.exe")[0]
                if path == "":
                    # Cancel was pressed
                    raise ARCToolInvalidPathException
                pathlibPath = pathlib.Path(path)
                inGoodLocation = self.__withinDirectory(pathlibPath, modDirectory)
                inGoodLocation |= self.__withinDirectory(pathlibPath, gameDataDirectory)
                if pathlibPath.is_file() and inGoodLocation:
                    self._organizer.setPluginSetting(self.__mainToolName(), "ARCTool-path", path)
                    savedPath = path
                    break
                else:
                    QMessageBox.information(self.__parentWidget, self.__tr("Not a compatible location..."), self.__tr("ARCTool only works when within the VFS, so must be installed within a mod folder. Please select a different ARC installation"))
        # Check the mod is actually enabled
        if self.__withinDirectory(pathlibPath, modDirectory):
            ARCModName = None
            for path in pathlibPath.parents:
                if path.parent.samefile(modDirectory):
                    ARCModName = path.name
                    break
        return savedPath

    def _compress_ARC(self, executable, modList, arcPath):
        compress_args = "-c -pc -dd -alwayscomp -txt -v 7"
        modDirectory = self.__getModDirectory()
        arcPath_parent = os.path.dirname(arcPath)
        executablePath, executableName = os.path.split(executable)
        arctool_mod = os.path.relpath(executablePath, modDirectory).split(os.path.sep, 1)[0]
        mergeDirectory = 'Merged ARC - ' + self._organizer.profileName()
        
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "dev-option")):
            compress_args = compress_args + " -tex -xfs -lot -gmd"

        # if vanilla files don't exist, end function
        tempDirARC = executablePath + os.sep + arcPath
        if not os.path.isdir(tempDirARC):
            QMessageBox.critical(self.__parentWidget, self.__tr("ERROR"), self.__tr("folder not found:\n" + executablePath + os.sep  + arcPath +"\nARC extraction incomplete. Please rerun ARC Extact."))
            return False

        # create the output folder
        pathlib.Path(modDirectory + os.sep + mergeDirectory + os.sep + arcPath_parent).mkdir(parents=True, exist_ok=True)

        # copy .arc compression order txt and vanilla files
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo(f'Copying {arcPath}.arc.txt')
                QCoreApplication.processEvents()
        shutil.copy(os.path.normpath(executablePath + os.sep + arcPath + ".arc.txt"), os.path.normpath(modDirectory + os.sep + mergeDirectory + os.sep + arcPath_parent))
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
            qInfo("Merging vanilla files")
        shutil.copytree(os.path.normpath(executablePath + os.sep + arcPath), os.path.normpath(modDirectory + os.sep + mergeDirectory + os.sep + arcPath), dirs_exist_ok=True)

        # copy mod files to merge folder
        for mod_name in modList:
            childModARCpath = pathlib.Path(str(modDirectory + os.sep + mod_name) + os.sep + arcPath)
            if pathlib.Path(childModARCpath).exists() and not mod_name == mergeDirectory:
                if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                    qInfo(f'Merging mod: {mod_name}')
                QCoreApplication.processEvents()
                shutil.copytree(os.path.normpath(modDirectory + os.sep + mod_name + os.sep + arcPath), os.path.normpath(modDirectory + os.sep + mergeDirectory + os.sep + arcPath), dirs_exist_ok=True)
                if mod_name != arctool_mod:
                    # hide arc folder
                    #os.rename(modDirectory + os.sep + mod_name + os.sep + arcPath, modDirectory + os.sep + mod_name + os.sep + arcPath + ".mohidden")
                    # remove .arc.txt
                    pathlib.Path(modDirectory + os.sep + mod_name + os.sep + arcPath + ".arc.txt").unlink(missing_ok=True)

        # compress
        output = os.popen('"' + executable + '" ' + compress_args + ' "' + os.path.normpath(modDirectory + os.sep + mergeDirectory + os.sep + arcPath) + '"').read()
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "verbose-log")):
            qInfo(output)
            QCoreApplication.processEvents()

        # remove folders and txt
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "remove-temp")):
            if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo("Removing temp files")
            shutil.rmtree(os.path.normpath(modDirectory + os.sep + mergeDirectory + os.sep + arcPath))
            os.remove(os.path.normpath(modDirectory + os.sep + mergeDirectory + os.sep + arcPath + '.arc.txt'))

        if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
            qInfo("ARC merge complete")
            QCoreApplication.processEvents()

        return True

    def __process_mods(self, executable):
        executablePath, executableName = os.path.split(executable)
        modDirectory = self.__getModDirectory()
        mergeDirectory = 'Merged ARC - ' + self._organizer.profileName()
        gameDataDirectory = self._organizer.managedGame().dataDirectory().absolutePath()
        arctool_mod = os.path.relpath(executablePath, modDirectory).split(os.path.sep, 1)[0]
        arcFilesPrevBuildDict = defaultdict(list)
        arcFilesCurrentDict = defaultdict(list)

        myProgressD = QProgressDialog(self.__tr("Processing..."), self.__tr("Cancel"), 0, 0, self.__parentWidget)
        myProgressD.forceShow()
        myProgressD.setFixedWidth(320)

        # load previous arc merge info
        try:
            with open(modDirectory + os.sep + mergeDirectory + os.sep + 'arcFileMerge.json', 'r') as file_handle:
                arcFilesPrevBuildDict = json.load(file_handle)
        except FileNotFoundError:
            if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo("arcFileMerge.json not found")

        # build list of current active mod arc folders to merge
        modlist = self._organizer.modList()
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if mod_name != arctool_mod and mod_name != mergeDirectory:
                    for dirpath, dirnames, filenames in os.walk(modDirectory + os.path.sep + mod_name):
                        # check for extracted arc folders
                        for folder in dirnames:
                            arcFolder = dirpath + os.path.sep + folder
                            relative_path = os.path.relpath(arcFolder, modDirectory).split(os.path.sep, 1)[1]
                            if (os.path.isfile(os.path.normpath(gameDataDirectory + os.path.sep + relative_path + ".arc"))):
                                if mod_name not in arcFilesCurrentDict[relative_path]:
                                    arcFilesCurrentDict[relative_path].append(mod_name)

        # process changed merges from dictionary
        for entry in arcFilesCurrentDict:
            if (myProgressD.wasCanceled()):
                if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                        qInfo("Merge cancelled")
                return
            if entry not in arcFilesPrevBuildDict or arcFilesCurrentDict[entry] != arcFilesPrevBuildDict[entry]:
                    myProgressD.setLabelText(f'Merging: {entry}')
                    QCoreApplication.processEvents()
                    if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                        qInfo(f'Starting merge for arc: {entry}')
                        QCoreApplication.processEvents()
                    if not self._compress_ARC(executable, arcFilesCurrentDict[entry], entry):
                        myProgressD.close()
                        return

        # remove stale .arc files from merged folder

        # write arc merge info to json
        with open(modDirectory + os.sep + mergeDirectory + os.sep + 'arcFileMerge.json', 'w') as file_handle:
            json.dump(arcFilesCurrentDict, file_handle)

        # disable arctool mod
        #self._organizer.modList().setActive(arctool_mod, False)

        myProgressD.close()
        QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("Merge complete"))        
        self._organizer.refresh()

    def __getModDirectory(self):
        return self._organizer.modsPath()

    @staticmethod
    def __withinDirectory(innerPath, outerDir):
        for path in innerPath.parents:
            if path.samefile(outerDir):
                return True
        return False

    @staticmethod
    def __mainToolName():
        return "ARC Extract"

def createPlugin():
    return ARCToolCompress()
