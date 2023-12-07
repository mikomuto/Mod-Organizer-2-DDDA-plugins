# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'Run ARCTool' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# asks user for 

import os, re
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

class MergeMod(mobase.IPluginTool):

    RE_ROM = re.compile('.*rom.+')
    RE_TEMP = re.compile('.*tmp.+')

    def __init__(self):
        super(MergeMod, self).__init__()
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
        return "ARC Create Merge Mod"

    def localizedName(self):
        return self.__tr("ARC Create Merge Mod")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.__tr("Runs ARCTool on mod to extract all arc files and removes ITM")

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
        mobase.PluginSetting("log-enabled", self.__tr("Enable logs"), False),
        mobase.PluginSetting("verbose-log", self.__tr("Verbose logs"), False),
            ]

    def displayName(self):
        return self.__tr("ARC Merge Mod")

    def tooltip(self):
        return self.__tr("Unpacks all ARC files")

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
            executable = self.getARCToolPath()
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
            path = self.__getModFolderPath()
        except ARCFileMissingException:
            QMessageBox.critical(self.__parentWidget, self.__tr("Mod folder not specified"), self.__tr("A valid folder was not specified. This tool will now exit."))
            return
            
        compressResult = self.processMod(executable, path)
            
        if compressResult:
            QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("Merge mod creation complete"))

    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)
        
    def __getModFolderPath(self):
        modDirectory = self.__getModDirectory()
        gameDataDirectory = self.__organizer.managedGame().dataDirectory().absolutePath()
        path = QFileDialog.getExistingDirectory(self.__parentWidget, self.__tr("Locate mod to clean"), str(modDirectory))
        if path == "":
        # Cancel was pressed
            raise ARCFileMissingException
        return path
        
    def getARCToolPath(self):
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
                result = QMessageBox.question(self.__parentWidget, self.__tr("ARCTool mod deactivated"), self.__tr("ARCTool is installed to an inactive mod. /n/nPress Yes to activate it or Cancel to quit the tool"), QMessageBox.StandardButton(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel))
                if result == QMessageBox.StandardButton.Yes:
                    self.__organizer.modList().setActive(ARCModName, True)
                else:
                    raise ARCToolInactiveException
        return savedPath
        
    def extractVanillaARCfile(self, executable, arcFile):
        args = "-x -pc -dd -alwayscomp -txt -v 7"
        executablePath, executableName = os.path.split(executable)
        gameDataDirectory = self.__organizer.managedGame().dataDirectory().absolutePath()
        modDirectory = self.__getModDirectory()
        mod_name, arc_file_relative_path = os.path.relpath(arcFile, modDirectory).split(os.path.sep, 1)
        arc_folder_relative_path = os.path.splitext(arc_file_relative_path)[0]
        arc_file_folder_relative_path = os.path.split(arc_file_relative_path)[0]
        
        #copy vanilla arc to temp, extract, then delete if not already done
        extractedARCfolder = pathlib.Path(modDirectory + "/" + mod_name + "/tmp/" + arc_folder_relative_path)
        if not (os.path.isdir(extractedARCfolder)):
            if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                qInfo("Extracting vanilla ARC: " + arc_file_relative_path)
            if (os.path.isfile(os.path.join(gameDataDirectory, arc_file_relative_path))):
                pathlib.Path(modDirectory + "/" + mod_name + "/tmp/" + arc_file_folder_relative_path).mkdir(parents=True, exist_ok=True)
                shutil.copy(os.path.normpath(os.path.join(gameDataDirectory, arc_file_relative_path)), os.path.normpath(modDirectory + "/" + mod_name + "/tmp/" + arc_file_folder_relative_path))
                output = os.popen('"' + executable + '" ' + args + ' "' + os.path.normpath(modDirectory + '/' + mod_name + '/tmp/' + arc_file_relative_path + '"')).read()
                if bool(self.__organizer.pluginSetting(self.name(), "verbose-log")):
                    qInfo(output)
                #remove .arc file
                os.remove(os.path.normpath(modDirectory + '/' + mod_name + '/tmp/' + arc_file_relative_path))
            else:
                qInfo("No matching vanilla ARC found")

    def extractARCFile(self, executable, arcFile):
        args = "-x -pc -dd -alwayscomp -txt -v 7"
        
        modDirectory = self.__getModDirectory()
        gameDataDirectory = self.__organizer.managedGame().dataDirectory().absolutePath()
        mod_name, arc_file_relative_path = os.path.relpath(arcFile, modDirectory).split(os.path.sep, 1)
        arc_folder_relative_path = os.path.splitext(arc_file_relative_path)[0]
        master_arc_path = pathlib.Path(modDirectory + '/' + mod_name + '/tmp/' + arc_folder_relative_path)
        
        if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
            qInfo("Starting extractARCFile: " + arcFile)
        if (os.path.isfile(os.path.join(gameDataDirectory, arc_file_relative_path))):        
            #extract arc and remove ITM
            arc_file_path = os.path.splitext(arcFile)[0]
            if pathlib.Path(arcFile).exists():
                output = os.popen('"' + executable + '" ' + args + ' "' + str(arcFile) + '"').read()
                if bool(self.__organizer.pluginSetting(self.name(), "verbose-log")):
                    qInfo(output)
                # delete arc
                os.remove(arcFile)
            # remove ITM
            if bool(self.__organizer.pluginSetting(self.name(), "log-enabled")):
                qInfo("Deleting duplicate files")
            def delete_same_files(dcmp):
                for name in dcmp.same_files:
                    if bool(self.__organizer.pluginSetting(self.name(), "verbose-log")):
                        qInfo("Deleting duplicate file %s" % (os.path.join(dcmp.right, name)))
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
                        if bool(self.__organizer.pluginSetting(self.name(), "verbose-log")):
                            qInfo("Deleting empty folder %s" % (full_path))
                        os.rmdir(full_path)
        else:
            qInfo("No matching vanilla ARC found")
        return True
        
    def processMod(self, executable, path):
        arcFilesSeen = []
        duplicateARCFiles = []
        modDirectory = self.__getModDirectory()
        gameDataDirectory = self.__organizer.managedGame().dataDirectory().absolutePath()
        mod_name = os.path.relpath(path, modDirectory)
        
        QMessageBox.information(self.__parentWidget, self.__tr("Note:"), self.__tr("Starting ARC file extraction. Process will run in the background and may take a long time. Mod manager will appear inactive."))
        for dirpath, dirnames, filenames in os.walk(path):
            for folder in dirnames:
                # check for extracted arc folders
                arcFolder = dirpath + "\\" + folder
                arcFile = arcFolder + ".arc"
                isRomFolder = self.RE_ROM.match(arcFolder)
                isTempFolder = self.RE_TEMP.match(arcFolder)
                qInfo("Processing: " + arcFolder)
                if isRomFolder and not isTempFolder:
                    rootPath, relativePath = arcFolder.split('\\rom\\', 1)
                    if (os.path.isfile(os.path.normpath(gameDataDirectory + "/rom/" +  relativePath + ".arc"))):
                        extractedARCFile = folder + ".arc"
                        # extract vanilla arc file
                        self.extractVanillaARCfile(executable, arcFile)
                        self.extractARCFile(executable, arcFile)
            for file in filenames:
                thisfilename, extension = os.path.splitext(file)
                if extension == ".arc":
                    arcFile = dirpath + "\\" + file
                    isTempFolder = self.RE_TEMP.match(arcFile)
                    if not isTempFolder:
                        self.extractVanillaARCfile(executable, arcFile)
                        self.extractARCFile(executable, arcFile)
            
        # remove tmp folder
        shutil.rmtree(modDirectory + '/' + mod_name + '/tmp/', ignore_errors=False, onerror=None)
                    
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
    return MergeMod()
