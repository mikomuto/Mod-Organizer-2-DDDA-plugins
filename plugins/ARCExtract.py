# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'Run ARCTool' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# asks user for arc file to extract, copies vanilla arc from game folder to a temp folder, extracts this arc in all mods installed, deletes identical to master files, then exits

import os
import shutil
import pathlib
import sys
import filecmp

from PyQt5.QtCore import QCoreApplication, qCritical, QFileInfo
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QFileDialog, QFileSystemModel, QMessageBox

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

class ARCTool(mobase.IPluginTool):

    def __init__(self):
        super(ARCTool, self).__init__()
        self.__organizer = None
        self.__parentWidget = None

    def init(self, organizer):
        self.__organizer = organizer
        if sys.version_info < (3, 0):
            qCritical(self.__tr("ARC extractor plugin requires a Python 3 interpreter, but is running on a Python 2 interpreter."))
            QMessageBox.critical(self.__parentWidget, self.__tr("Incompatible Python version."), self.__tr("This version of the ARC extractor plugin requires a Python 3 interpreter, but Mod Organizer has provided a Python 2 interpreter. You should check for an updated version, including in the Mod Organizer 2 Development Discord Server."))
            return False
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
        mobase.PluginSetting("repair-TEX", self.__tr("Fix file extensions and extract TEX files"), True),
        mobase.PluginSetting("log-enabled", self.__tr("Enable logs"), False),
            ]

    def displayName(self):
        return self.__tr("ARC Extract")

    def tooltip(self):
        return self.__tr("Unpacks ARC File")

    def icon(self):
        ARCToolPath = self.__organizer.pluginSetting(self.name(), "ARCTool-path")
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
        
        if not bool(self.__organizer.pluginSetting(self.name(), "initialised")):
            self.__organizer.setPluginSetting(self.name(), "ARCTool-path", "")

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

        self.__organizer.setPluginSetting(self.name(), "initialised", True)

        try:
            path = self.__getARCFilePath()
        except ARCFileMissingException:
            QMessageBox.critical(self.__parentWidget, self.__tr("ARC file not specified"), self.__tr("A valid file was not specified. This tool will now exit."))
            return
            
        extractResult = self.__extractARCFile(executable, path)
            
        if extractResult:
            QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("ARC file extraction complete"))

    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)

    def __getARCToolPath(self):
        savedPath = self.__organizer.pluginSetting(self.name(), "ARCTool-path")
        # ARCTool must be installed within the game's data directory or a mod folder
        modDirectory = self.__getModDirectory()
        gameDataDirectory = pathlib.Path(self.__organizer.managedGame().dataDirectory().absolutePath())
        pathlibPath = pathlib.Path(savedPath)
        if not os.path.exists(pathlibPath):
            self.__organizer.setPluginSetting(self.name(), "ARCTool-path", "")
            self.__organizer.setPluginSetting(self.name(), "initialised", False)
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
                    self.__organizer.setPluginSetting(self.name(), "ARCTool-path", path)
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
            if (self.__organizer.modList().state(ARCModName) & mobase.ModState.active) == 0:
                # ARC is installed to an inactive mod
                result = QMessageBox.question(self.__parentWidget, self.__tr("ARCTool mod deactivated"), self.__tr("ARCTool is installed to an inactive mod. /n/nPress OK to activate it or Cancel to quit the tool"), QMessageBox.StandardButtons(QMessageBox.Ok | QMessageBox.Cancel))
                if result == QMessageBox.Ok:
                    self.__organizer.modList().setActive(ARCModName, True)
                else:
                    raise ARCToolInactiveException
        return savedPath

    def __getARCFilePath(self):
        modDirectory = self.__getModDirectory()
        executable = self.__organizer.pluginSetting(self.name(), "ARCTool-path")
        path = QFileDialog.getOpenFileName(self.__parentWidget, self.__tr("Locate ARC file"), str(modDirectory), "*.arc")[0]
        if path == "":
        # Cancel was pressed
            raise ARCFileMissingException

        pathlibPath = pathlib.Path(path)
        inGoodLocation = self.__withinDirectory(pathlibPath, modDirectory)
        if pathlibPath.is_file() and inGoodLocation:
            return path
        else:
            QMessageBox.information(self.__parentWidget, self.__tr("Not a compatible location..."), self.__tr("ARC file must be located within a mod folder"))

    def __extractARCFile(self, executable, path):
        args = "-x -dd -tex -xfs -gmd -txt -alwayscomp -pc -txt -v 7"
        
        gameDataDirectory = self.__organizer.managedGame().dataDirectory().absolutePath()
        modDirectory = self.__getModDirectory()
        relative_path = os.path.relpath(path, modDirectory).split(os.path.sep, 1)[1]

        # create temp and recreate folder structure in ARCTool folder
        executablePath, executableName = os.path.split(executable)
        Path(executablePath + "/rom/").mkdir(parents=True, exist_ok=True)
        tempSubDir, arcFile = os.path.split(relative_path)
        arcName = os.path.splitext(arcFile)[0]
        tempDirARCPath = pathlib.Path(executablePath + '/' +  tempSubDir + '/' + os.path.splitext(arcName)[0])
        
        #copy vanilla arc to temp, extract, then delete
        extractedARCfolder = pathlib.Path(executablePath + "/rom/" + os.path.splitext(arcName)[0])
        if not (os.path.isdir(extractedARCfolder)):
            Path(executablePath + '/' +  tempSubDir).mkdir(parents=True, exist_ok=True)
            shutil.copy(os.path.normpath(os.path.join(gameDataDirectory, relative_path)), os.path.normpath(executablePath + '/' + tempSubDir))
            output = os.popen('"' + executable + '" ' + args + ' "' + os.path.normpath(executablePath + '/' + relative_path + '"')).read()
            if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                print(output, file=open(str(tempDirARCPath) + '_ARCtool.log', 'a'))
            os.remove(os.path.normpath(executablePath + '/' + relative_path))
        
        #extract all matching arc and remove
        modDirPath = pathlib.Path(modDirectory)
        for child in modDirPath.iterdir():
            if bool(self.__organizer.pluginSetting(self.name(), "repair-TEX")):
                self.__fixTEXfiles(child)
            if not pathlib.PurePath(child).match('Merged ARC'):
                childModARC = pathlib.Path(str(child) + '/' + relative_path)
                childModARCPath = os.path.splitext(childModARC)[0]
                if pathlib.Path(childModARC).exists():
                    output = os.popen('"' + executable + '" ' + args + ' "' + str(childModARC) + '"').read()
                    if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                        print(output, file=open(str(childModARCPath) + '_ARCtool.log', 'a'))
                    # remove ITM
                    if bool(self.__organizer.pluginSetting(self.name(), "remove-ITM")):
                        def delete_same_files(dcmp):
                            for name in dcmp.same_files:
                                if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                                    print("Deleting duplicate file %s" % (os.path.join(dcmp.right, name)), file=open(str(childModARCPath) + '_ARCtool.log', 'a'))
                                os.remove(os.path.join(dcmp.right, name))
                            for sub_dcmp in dcmp.subdirs.values():
                                delete_same_files(sub_dcmp)
                        dcmp = filecmp.dircmp(tempDirARCPath, childModARCPath) 
                        delete_same_files(dcmp)
                        # delete empty folders
                        for dirpath, dirnames, filenames in os.walk(childModARCPath, topdown=False):
                            for dirname in dirnames:
                                full_path = os.path.join(dirpath, dirname)
                                if not os.listdir(full_path):
                                    if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                                        print("Deleting empty folder %s" % (full_path), file=open(str(childModARCPath) + '_ARCtool.log', 'a'))
                                    os.rmdir(full_path)
                    # delete arc
                    if bool(self.__organizer.pluginSetting(self.name(), "delete-ARC")):
                        os.remove(childModARC)
                    if bool(self.__organizer.pluginSetting(self.name(), "hide-ARC")):
                        os.rename(childModARC, str(childModARC) + ".mohidden")

        return True
        
    def __fixTEXfiles(self, path):
        executable = self.__organizer.pluginSetting(self.name(), "ARCTool-path")
        for dirpath, dirnames, filenames in os.walk(path):
            for file in filenames:
                thisfilename = os.path.splitext(file)[0]
                extension = os.path.splitext(file)[1]
                if len(extension) == 9 and self.__is_hex(extension):
                    if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                        print("Invalid TEX file found: %s" % (dirpath + '/' + file), file=open(str(path) + '/ARCtool_tex.log', 'a'))
                    os.rename(dirpath + '/' + file, dirpath + '/' + thisfilename + ".tex")
                    if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                        print("File renamed", file=open(str(path) + '/ARCtool_tex.log', 'a'))
                    output = os.popen('"' + executable + '" ' + ' "' + str(dirpath + '/' + thisfilename + ".tex") + '"').read()
                    if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                        print(output, file=open(str(path) + '/ARCtool_tex.log', 'a'))
        return True

    def __getModDirectory(self):
        return self.__organizer.modsPath()

    @staticmethod
    def __is_hex(string):
        return re.fullmatch(r"^[0-9a-fA-F]$", string or "") is not None

    @staticmethod
    def __withinDirectory(innerPath, outerDir):
        for path in innerPath.parents:
            if path.samefile(outerDir):
                return True
        return False

def createPlugin():
    return ARCTool()
