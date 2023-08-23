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
        return self._organizer.pluginSetting(self.name(), "enabled")

    def settings(self):
        return [
        mobase.PluginSetting("enabled", "enable this plugin", True),
        mobase.PluginSetting("ARCTool-path", self.__tr("Path to ARCTool.exe"), ""),
        mobase.PluginSetting("initialised", self.__tr("Settings have been initialised.  Set to False to reinitialise them."), False),
            ]

    def displayName(self):
        return self.__tr("ARC Compress")

    def tooltip(self):
        return self.__tr("Compress files to .arc")

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
            QMessageBox.critical(self.__parentWidget, self.__tr("ARC folder not specified"), self.__tr("A valid folder was not specified. This tool will now exit."))
            return
            
        compressResult = self.__compressARCFile(executable, path)
            
        if compressResult:
            QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("ARC folder compression complete"))

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
                    QMessageBox.information(self.__parentWidget, self.__tr("Not a compatible location..."), self.__tr("ARCTool only works when within the VFS, so must be installed to either the game's data directory or within a mod folder. Please select a different ARC installation."))
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
        gameDataDirectory = self.__organizer.managedGame().dataDirectory().absolutePath()
        path = QFileDialog.getExistingDirectory(self.__parentWidget, self.__tr("Locate folder to compress"), str(modDirectory))
        if path == "":
        # Cancel was pressed
            raise ARCFileMissingException
        relative_path = os.path.relpath(path, modDirectory).split(os.path.sep, 1)[1]
        #check if *.arc exists in game folder
        if pathlib.Path(gameDataDirectory + '/' + str(relative_path) + ".arc").exists():
            pathlibPath = pathlib.Path(path)
            return path
        else:
            QMessageBox.information(self.__parentWidget, self.__tr("Invalid folder..."), self.__tr("File " + relative_path + ".arc not located within the game folder"))

    def __compressARCFile(self, executable, path):
        extract_args = "-x -dd -tex -xfs -gmd -txt -alwayscomp -pc -txt -v 7"
        compress_args = "-c -dd -tex -xfs -gmd -txt -alwayscomp -pc -txt -v 7"
        
        gameDataDirectory = self.__organizer.managedGame().dataDirectory().absolutePath()
        modDirectory = self.__getModDirectory()
        relative_path = os.path.relpath(path, modDirectory).split(os.path.sep, 1)[1]
        relative_path_parent = os.path.dirname(relative_path)

        # create temp and recreate folder structure in ARCTool folder
        executablePath, executableName = os.path.split(executable)
        Path(executablePath + "/rom/").mkdir(parents=True, exist_ok=True)
        tempDirARC = executablePath + '/' + relative_path
        
        #if files don't exist, copy vanilla .arc to temp, extract, then delete
        if not os.path.isdir(tempDirARC):
            Path(executablePath + '/' + relative_path).mkdir(parents=True, exist_ok=True)
            shutil.copy(os.path.normpath(gameDataDirectory + '/' + str(relative_path) + ".arc"), os.path.normpath(executablePath + '/' + relative_path_parent))
            output = os.popen('"' + executable + '" ' + extract_args + ' "' + os.path.normpath(executablePath + '/' + relative_path + '.arc"')).read()
            print(output, file=open(str(tempDirARC) + '_ARCextract.log', 'a'))
            os.remove(os.path.normpath(executablePath + '/' + relative_path + '.arc'))
            
        #create the output folder
        Path(modDirectory + "/Merged ARC/" + relative_path_parent).mkdir(parents=True, exist_ok=True)
        # copy .arc compression order txt
        shutil.copy(os.path.normpath(executablePath + '/' + relative_path + ".arc.txt"), os.path.normpath(modDirectory + '/Merged ARC/' + relative_path_parent))
        
        #get mod priority list
        modPriorityList = []
        with open("C:\MO2 Games\DDDA\profiles\Default\modlist.txt") as file:
            for line in file: 
                line = line.strip()
                if (line.startswith('+')):
                    modPriorityList.append(line.strip('+'))
        #process mods in reverse since highest priority is at top of file
        for entry in reversed(modPriorityList):
            childModARCpath = pathlib.Path(str(modDirectory + '/' + entry) + "/" + relative_path)
            if pathlib.Path(childModARCpath).exists() and not entry == 'Merged ARC':
                print("Merging " + entry, file=open(str(modDirectory) + '/Merged ARC/' + str(relative_path) + '_ARCcompress.log', 'a'))
                shutil.copytree(os.path.normpath(modDirectory + '/' + entry + '/' + relative_path), os.path.normpath(modDirectory + '/Merged ARC/' + relative_path), dirs_exist_ok=True)
                
        #compress
        output = os.popen('"' + executable + '" ' + compress_args + ' "' + str(modDirectory + '/Merged ARC/' + relative_path) + '"').read()
        print(output, file=open(str(modDirectory) + '/Merged ARC/' + str(relative_path) + '_ARCcompress.log', 'a'))
        
        #remove folders and txt
        print("Cleaning up...", file=open(str(modDirectory) + '/Merged ARC/' + str(relative_path) + '_ARCcompress.log', 'a'))
        shutil.rmtree(os.path.normpath(modDirectory + '/Merged ARC/' + relative_path))
        os.remove(os.path.normpath(modDirectory + '/Merged ARC/' + relative_path + '.arc.txt'))
        
        print("ARC compress complete", file=open(str(modDirectory) + '/Merged ARC/' + str(relative_path) + '_ARCcompress.log', 'a'))
        return True

    def __getModDirectory(self):
        return self.__organizer.modsPath()

    @staticmethod
    def __withinDirectory(innerPath, outerDir):
        for path in innerPath.parents:
            if path.samefile(outerDir):
                return True
        return False

def createPlugin():
    return ARCTool()
