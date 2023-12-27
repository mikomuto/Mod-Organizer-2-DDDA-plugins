# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'Run ARCTool' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# asks user for arc file to extract, copies vanilla arc from game folder to a temp folder, extracts this arc in all mods installed, deletes identical to master files, then exits

import os, re
import shutil
import pathlib
import sys
import filecmp

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
        
    RE_HEXEXTENSION = re.compile('\.[0-9a-fA-F]{8}')
    
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

        self._organizer.setPluginSetting(self.name(), "initialised", True)
        
        self.processMods(executable)
       

    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)

    def getARCToolPath(self):
        savedPath = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        # ARCTool must be installed within the game's data directory or a mod folder
        modDirectory = self.__getModDirectory()
        gameDataDirectory = pathlib.Path(self._organizer.managedGame().dataDirectory().absolutePath())
        pathlibPath = pathlib.Path(savedPath)
        if not os.path.exists(pathlibPath):
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.name(), "initialised", False)
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
                    self._organizer.setPluginSetting(self.name(), "ARCTool-path", path)
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
        
    def extractVanillaARCfile(self, executable, path):
        args = "-x -pc -dd -alwayscomp -txt -v 7"
        executablePath, executableName = os.path.split(executable)
        gameDataDirectory = self._organizer.managedGame().dataDirectory().absolutePath()
        modDirectory = self.__getModDirectory()
        arc_file_relative_path = os.path.relpath(path, modDirectory).split(os.path.sep, 1)[1]
        arc_folder_relative_path = os.path.splitext(arc_file_relative_path)[0]
        arc_file_folder_relative_path = os.path.split(arc_file_relative_path)[0]
        
        #copy vanilla arc to temp, extract, then delete if not already done
        extractedARCfolder = pathlib.Path(executablePath + "/" + arc_folder_relative_path)
        if not (os.path.isdir(extractedARCfolder)):
            if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                qInfo("Extracting vanilla ARC: " + arc_file_relative_path)
            if (os.path.isfile(os.path.join(gameDataDirectory, arc_file_relative_path))):
                pathlib.Path(executablePath + '/' +  arc_folder_relative_path).mkdir(parents=True, exist_ok=True)
                shutil.copy(os.path.normpath(os.path.join(gameDataDirectory, arc_file_relative_path)), os.path.normpath(executablePath + '/' + arc_file_folder_relative_path))
                output = os.popen('"' + executable + '" ' + args + ' "' + os.path.normpath(executablePath + '/' + arc_file_relative_path + '"')).read()
                if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                    qInfo(output)
                #remove .arc file
                os.remove(os.path.normpath(executablePath + '/' + arc_file_relative_path))
                return True
            else:
                modName = os.path.relpath(path, modDirectory).split(os.path.sep, 1)[0]
                QMessageBox.critical(self.__parentWidget, self.__tr("Invalid ARC file path"), self.__tr("Mod: " + modName + "\nFile: " + arc_file_relative_path))
                if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                    qInfo("Invalid ARC file: " + path)
                return False
        else:
            return True

    def extractARCFile(self, executable, path):
        args = "-x -pc -dd -alwayscomp -txt -v 7"
        
        modDirectory = self.__getModDirectory()
        executablePath, executableName = os.path.split(executable)
        arc_file_relative_path = os.path.relpath(path, modDirectory).split(os.path.sep, 1)[1]
        arc_folder_relative_path = os.path.splitext(arc_file_relative_path)[0]
        master_arc_path = pathlib.Path(executablePath + '/' +  arc_folder_relative_path)
        
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            qInfo("Starting extractARCFile: " + path)
        #extract arc and remove ITM
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
                            if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                                qInfo("Deleting empty folder %s" % (full_path))
                            os.rmdir(full_path)
            # delete arc
            if bool(self._organizer.pluginSetting(self.name(), "delete-ARC")):
                os.remove(path)
            if bool(self._organizer.pluginSetting(self.name(), "hide-ARC")):
                os.rename(path, str(path) + ".mohidden")

        return True
        
    def processMods(self, executable):
        arcFilesSeen = []
        duplicateARCFiles = []
        modDirectory = self.__getModDirectory()
        gameDataDirectory = self._organizer.managedGame().dataDirectory().absolutePath()
        executablePath, executableName = os.path.split(executable)
        executableFolder = os.path.relpath(executablePath, modDirectory).split(os.path.sep, 1)[0]
        
        #get mod active list
        modActiveList = []
        modlist = self._organizer.modList()
        for mod in modlist.allModsByProfilePriority():
            if modlist.state(mod) & mobase.ModState.ACTIVE:
                modActiveList.append(mod)
        
        myProgressD = QProgressDialog(self.__tr("Unhiding files and folders"), self.__tr("Cancel"), 0, 0, self.__parentWidget)
        myProgressD.forceShow()
        myProgressD.setFixedWidth(420)
        QCoreApplication.processEvents()
        
        for dirpath, dirnames, filenames in os.walk(modDirectory):
            mod_name = os.path.relpath(dirpath, modDirectory).split(os.path.sep, 1)[0]
            for folder in dirnames:
                #unhide arc folders
                if 'mohidden' in folder:
                    new_name = os.path.splitext(folder)[0]
                    os.rename(dirpath + os.path.sep + folder, dirpath + os.path.sep + new_name)
                if mod_name in modActiveList and 'rom' in dirpath and mod_name != 'Merged ARC' and mod_name != executableFolder:
                    arcFolder = dirpath + "\\" + folder
                    rootPath, relativePath = arcFolder.split('\\rom\\', 1)
                    if (os.path.isfile(os.path.normpath(gameDataDirectory + "/rom/" +  relativePath + ".arc"))):
                        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                                qInfo("ARC Folder: " + os.path.normpath(arcFolder))
                        extractedARCFile = folder + ".arc"
                        if extractedARCFile not in arcFilesSeen:
                            arcFilesSeen.append(extractedARCFile)
                        if extractedARCFile not in duplicateARCFiles:
                            duplicateARCFiles.append(extractedARCFile)
                        # extract vanilla arc file if needed
                        myProgressD.setLabelText("Extracting: " + mod_name + " : " + folder + ".arc")
                        QCoreApplication.processEvents()
                        if not self.extractVanillaARCfile(executable, dirpath + "\\" + folder + ".arc"):
                            myProgressD.close()
                            return
            for file in filenames:
                thisfilename, extension = os.path.splitext(file)
                if mod_name in modActiveList and extension == ".arc" and 'Merged ARC' not in dirpath:
                    if file in arcFilesSeen:
                        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                            qInfo("Duplicate ARC: " + os.path.normpath(dirpath + "/" + file))
                            QCoreApplication.processEvents()
                        duplicateARCFiles.append(file)
                    else:
                        arcFilesSeen.append(file)
 
        # restart
        for dirpath, dirnames, filenames in os.walk(modDirectory):
            mod_name = os.path.relpath(dirpath, modDirectory).split(os.path.sep, 1)[0]
            myProgressD.setLabelText("Scanning mod: " + mod_name)
            QCoreApplication.processEvents()
                            
            # extract duplicate .arc files 
            for file in filenames:
                thisfilename, extension = os.path.splitext(file)
                if mod_name in modActiveList and extension == ".arc" and 'Merged ARC' not in dirpath:
                    if file in duplicateARCFiles:
                        # extract vanilla arc file if needed
                        myProgressD.setLabelText("Extracting: " + mod_name + " : " + file)
                        QCoreApplication.processEvents()
                        if self.extractVanillaARCfile(executable, dirpath + "\\" + file):
                            if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                                qInfo("Extracting: " + mod_name + " : " + file)
                        else:
                            myProgressD.close()
                            return
                        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                            qInfo("Duplicate ARC: " + os.path.normpath(dirpath + "/" + file))
                            QCoreApplication.processEvents()
                        myProgressD.setLabelText("Extracting vanilla arc: " + file)
                        QCoreApplication.processEvents()
                        self.extractARCFile(executable, os.path.normpath(dirpath + "/" + file))
                        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                            qInfo("Extracting mod arc: " + file)
                            QCoreApplication.processEvents()

                # repair old tex files            
                hasHexFileExtension = self.RE_HEXEXTENSION.match(extension)
                
                # we have sound and game manual files with hex extenstions too
                if hasHexFileExtension and not ('sound' in dirpath or 'ingamemanual' in dirpath):
                    if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                        qInfo("Invalid TEX file found: %s" % (dirpath + '/' + file))
                        QCoreApplication.processEvents()
                    try:
                        os.rename(dirpath + '/' + file, dirpath + '/' + thisfilename + ".tex")
                    except WindowsError:
                            os.remove(dirpath + '/' + file)
                    if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
                        qInfo("File renamed")
                        QCoreApplication.processEvents()
                    output = os.popen('"' + executable + '" ' + ' "' + str(dirpath + '\\' + thisfilename + ".tex") + '"').read()
                    if bool(self._organizer.pluginSetting(self.name(), "verbose-log")):
                        qInfo(output)
        myProgressD.close()
        QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("ARC file extraction complete"))

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
