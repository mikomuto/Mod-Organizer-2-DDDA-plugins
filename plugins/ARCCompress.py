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

from PyQt6.QtCore import QCoreApplication, qCritical, QFileInfo, qInfo
from PyQt6.QtGui import QIcon, QFileSystemModel
from PyQt6.QtWidgets import QFileDialog, QMessageBox

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
        return "ARC Compressor"

    def localizedName(self):
        return self.__tr("ARC Compressor")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.__tr("Runs ARCTool on mods to compress folder to .arc")

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
        return self.__tr("ARC Compress")

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
            executable = self.__getARCToolPath()
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

        self.__processMods(executable)

        QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("ARC folder compression complete"))

    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)

    def __getARCToolPath(self):
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
            if (self._organizer.modList().state(ARCModName) & mobase.ModState.active) == 0:
                # ARC is installed to an inactive mod
                result = QMessageBox.question(self.__parentWidget, self.__tr("ARCTool mod deactivated"), self.__tr("ARCTool is installed to an inactive mod. Press Yes to activate it or Cancel to quit the tool"), QMessageBox.StandardButton(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel))
                if result == QMessageBox.StandardButton.Yes:
                    self._organizer.modList().setActive(ARCModName, True)
                else:
                    raise ARCToolInactiveException
        return savedPath

    def __compressARCFile(self, executable, path):
        compress_args = "-c -pc -dd -alwayscomp -txt -v 7"
        extract_args = "-x -pc -dd -alwayscomp -txt -v 7"
        gameDataDirectory = self._organizer.managedGame().dataDirectory().absolutePath()
        modDirectory = self.__getModDirectory()
        modDirectoryPath = pathlib.Path(modDirectory)
        relative_path = os.path.relpath(path, modDirectory).split(os.path.sep, 1)[1]
        relative_path_parent = os.path.dirname(relative_path)
        
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
            qInfo("Compressing ARC file: " + relative_path)

        # create temp and recreate folder structure in ARCTool folder
        executablePath, executableName = os.path.split(executable)
        pathlib.Path(executablePath + "/rom/").mkdir(parents=True, exist_ok=True)
        tempDirARC = executablePath + '/' + relative_path

        #if files don't exist, copy vanilla .arc to temp, extract, then delete
        if not os.path.isdir(tempDirARC):
            if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo("Vanilla arc not extracted. Extracting...")
            pathlib.Path(executablePath + '/' + relative_path).mkdir(parents=True, exist_ok=True)
            shutil.copy(os.path.normpath(gameDataDirectory + '/' + str(relative_path) + ".arc"), os.path.normpath(executablePath + '/' + relative_path_parent))
            output = os.popen('"' + executable + '" ' + extract_args + ' "' + os.path.normpath(executablePath + '/' + relative_path + '.arc"')).read()
            if bool(self._organizer.pluginSetting(self.__mainToolName(), "verbose-log")):
                qInfo(output)
            os.remove(os.path.normpath(executablePath + '/' + relative_path + '.arc'))
        else:
            if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo("Vanilla arc present. Skipping")

        #create the output folder
        pathlib.Path(modDirectory + "/Merged ARC/" + relative_path_parent).mkdir(parents=True, exist_ok=True)
        # copy .arc compression order txt
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo("Copying compression load order .txt")
        shutil.copy(os.path.normpath(executablePath + '/' + relative_path + ".arc.txt"), os.path.normpath(modDirectory + '/Merged ARC/' + relative_path_parent))

        #get mod priority list
        modPriorityList = []
        modlist = self._organizer.modList()
        for mod in modlist.allModsByProfilePriority():
            if modlist.state(mod) & mobase.ModState.ACTIVE:
                modPriorityList.append(mod)
        for entry in (modPriorityList):
            childModARCpath = pathlib.Path(str(modDirectory + '/' + entry) + "/" + relative_path)
            if pathlib.Path(childModARCpath).exists() and not entry == 'Merged ARC':
                if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                    qInfo("Merging " + entry)
                shutil.copytree(os.path.normpath(modDirectory + '/' + entry + '/' + relative_path), os.path.normpath(modDirectory + '/Merged ARC/' + relative_path), dirs_exist_ok=True)

        #compress
        output = os.popen('"' + executable + '" ' + compress_args + ' "' + os.path.normpath(modDirectory + '/Merged ARC/' + relative_path) + '"').read()
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "verbose-log")):
            qInfo(output)

        #remove folders and txt
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "remove-temp")):
            if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo("Cleaning up...")
            shutil.rmtree(os.path.normpath(modDirectory + '/Merged ARC/' + relative_path))
            os.remove(os.path.normpath(modDirectory + '/Merged ARC/' + relative_path + '.arc.txt'))

        if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
            qInfo("ARC compress complete")
        return True
        
    def __processMods(self, executable):
        executablePath, executableName = os.path.split(executable)
        QMessageBox.information(self.__parentWidget, self.__tr("Note:"), self.__tr("Starting ARC file merge. Process will run in the background and may take a long time. Mod manager will appear inactive."))
        for dirpath, dirnames, filenames in os.walk(executablePath + "\\rom"):
            for file in filenames:
                arcfolder, extension = file.split('.', 1)
                if extension == "arc.txt":
                    arcFile = dirpath + "\\" + arcfolder
                    if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                        qInfo("Starting merge for arc: " + arcFile)
                    self.__compressARCFile(executable, arcFile)
                    
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
        return "ARC Extractor"

def createPlugin():
    return ARCToolCompress()
