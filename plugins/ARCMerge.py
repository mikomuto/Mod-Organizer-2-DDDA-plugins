# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'ARC Merge' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# copies vanilla arc files from game folder to arc tool folder, copies all arc folder files in all mods installed to merge folder, compresses to .arc, then exits

import os
import shutil
import pathlib
import sys
import filecmp
import json
from collections import defaultdict

from PyQt6.QtCore import QCoreApplication, QFileInfo, qInfo, QThreadPool, QRunnable, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QFileSystemModel
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

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
        self.threadpool = QThreadPool()
        self.currentIndex = 0
        self.myProgressD = None
        self.arcFoldersPrevBuildDict = defaultdict(list)
        self.arcFoldersCurrentDict = defaultdict(list)
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
        mod_directory = self.__getModDirectory()
        gameDataDirectory = pathlib.Path(self._organizer.managedGame().dataDirectory().absolutePath())
        pathlibPath = pathlib.Path(savedPath)
        if not os.path.exists(pathlibPath):
            self._organizer.setPluginSetting(self.__mainToolName(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.__mainToolName(), "initialised", False)
            raise ARCToolMissingException
        inGoodLocation = self.__withinDirectory(pathlibPath, mod_directory)
        inGoodLocation |= self.__withinDirectory(pathlibPath, gameDataDirectory)
        if not pathlibPath.is_file() or not inGoodLocation:
            QMessageBox.information(self.__parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool path invalid or not set. \n\nARCTool must be visible within the VFS, so choose an installation either within the game's data directory or within a mod folder. \n\nThis setting can be updated in the Plugins tab of the Mod Organizer Settings menu."))
            while True:
                path = QFileDialog.getOpenFileName(self.__parentWidget, self.__tr("Locate ARCTool.exe"), str(mod_directory), "ARCTool.exe")[0]
                if path == "":
                    # Cancel was pressed
                    raise ARCToolInvalidPathException
                pathlibPath = pathlib.Path(path)
                inGoodLocation = self.__withinDirectory(pathlibPath, mod_directory)
                inGoodLocation |= self.__withinDirectory(pathlibPath, gameDataDirectory)
                if pathlibPath.is_file() and inGoodLocation:
                    self._organizer.setPluginSetting(self.__mainToolName(), "ARCTool-path", path)
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
        return savedPath

    def __process_mods(self, executable):
        self.arcFoldersPrevBuildDict.clear()
        self.arcFoldersCurrentDict.clear()
        executablePath, executableName = os.path.split(executable)
        mod_directory = self.__getModDirectory()
        mo_profile = self._organizer.profileName()
        merge_mod = 'Merged ARC - ' + self._organizer.profileName()
        gameDataDirectory = self._organizer.managedGame().dataDirectory().absolutePath()
        arctool_mod = os.path.relpath(executablePath, mod_directory).split(os.path.sep, 1)[0]        

        self.myProgressD = QProgressDialog(self.__tr("ARC Merge"), self.__tr("Cancel"), 0, 0, self.__parentWidget)
        self.myProgressD.setFixedWidth(320)
        self.myProgressD.forceShow()
        
        # load previous arc merge info
        try:
            with open(mod_directory + os.sep + merge_mod + os.sep + 'arcFileMerge.json', 'r') as file_handle:
                self.arcFoldersPrevBuildDict = json.load(file_handle)
        except:
            if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo("arcFileMerge.json not found or invalid")

        # build list of current active mod arc folders to merge
        modlist = self._organizer.modList()
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if mod_name != arctool_mod and mod_name != merge_mod:
                    for dirpath, dirnames, filenames in os.walk(mod_directory + os.path.sep + mod_name):
                        # check for extracted arc folders
                        for folder in dirnames:
                            arcFolder = dirpath + os.path.sep + folder
                            relative_path = os.path.relpath(arcFolder, mod_directory).split(os.path.sep, 1)[1]
                            if (os.path.isfile(os.path.normpath(gameDataDirectory + os.path.sep + relative_path + ".arc"))):
                                if mod_name not in self.arcFoldersCurrentDict[relative_path]:
                                    self.arcFoldersCurrentDict[relative_path].append(mod_name)
        self.currentIndex = 0
        arcToProcess = 0
        # process changed merges from dictionary
        for entry in self.arcFoldersCurrentDict:
            if entry not in self.arcFoldersPrevBuildDict or self.arcFoldersCurrentDict[entry] != self.arcFoldersPrevBuildDict[entry]:
                # Pass the function to execute
                worker = mergeThreadWorker(executable, mo_profile, gameDataDirectory, mod_directory, self.arcFoldersCurrentDict[entry], entry)
                worker.signals.result.connect(self.mergeThreadWorkerOutput)
                worker.signals.finished.connect(self.mergeThreadWorkerComplete)
                # Execute
                self.threadpool.start(worker)
                arcToProcess += 1
        if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
            qInfo(f'ARC merge count: {arcToProcess}')
        # set file count for progress
        self.myProgressD.setMaximum(arcToProcess)
        
        if arcToProcess == 0:            
            self.modCleanup()
                    
    def modCleanup(self):
        executablePath = self.get_arctool_path()
        mod_directory = self.__getModDirectory()
        merge_mod = 'Merged ARC - ' + self._organizer.profileName()
        arctool_mod = os.path.relpath(executablePath, mod_directory).split(os.path.sep, 1)[0]

        self.myProgressD.setLabelText(f'Cleaning up...')
        qInfo(f'Cleaning up...')
                
        # remove stale .arc files from merged folder
        for entry in self.arcFoldersPrevBuildDict:
            if (self.myProgressD.wasCanceled()):
                if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                        qInfo("Merge cancelled")
                return
            if entry not in self.arcFoldersCurrentDict:
                if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                        qInfo(f'Deleting stale arc: {entry}')
                        # Pass the function to execute
                        worker = cleanupThreadWorker(entry, mod_directory, merge_mod, arctool_mod)
                        # Execute
                        self.threadpool.start(worker)

        # write arc merge info to json
        try:
            with open(mod_directory + os.sep + merge_mod + os.sep + 'arcFileMerge.json', 'w') as file_handle:
                json.dump(self.arcFoldersCurrentDict, file_handle)
        except:
            if bool(self._organizer.pluginSetting(self.__mainToolName(), "log-enabled")):
                qInfo("arcFileMerge.json not found or invalid")
            
        #enable merge mod
        self._organizer.modList().setActive(merge_mod, True)

        self.myProgressD.hide()
        QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("Merge complete"))        
        self._organizer.refresh()
        
    def mergeThreadWorkerComplete(self):        
        self.currentIndex += 1
        qInfo(f'Current index: {self.currentIndex}')
        if self.currentIndex == self.myProgressD.maximum():
            self.modCleanup()
        self.myProgressD.setValue(self.currentIndex)        
    
    def mergeThreadWorkerOutput(self, log_out):        
        qInfo(log_out)

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
        
class cleanupThreadWorker(QRunnable):
    def __init__(self, entry, modDirectory, merge_mod, arctool_mod):
        self.entry = entry
        self.mod_directory = modDirectory
        self.merge_mod = merge_mod
        self.arctool_mod = arctool_mod
        super(cleanupThreadWorker, self).__init__()
    
    @pyqtSlot()
    def run(self):
        # clean arctool
        pathlib.Path(self.mod_directory + os.sep + self.arctool_mod + os.sep + self.entry + ".arc.txt").unlink(missing_ok=True)
        pathlib.Path(self.mod_directory + os.sep + self.arctool_mod + os.sep + self.entry + ".arc").unlink(missing_ok=True)
        if os.path.exists(self.mod_directory + os.sep + self.arctool_mod + os.sep + self.entry):
            shutil.rmtree(os.path.normpath(self.mod_directory + os.sep + self.arctool_mod + os.sep + self.entry))
        # clean merge
        pathlib.Path(self.mod_directory + os.sep + self.merge_mod + os.sep + self.entry + ".arc.txt").unlink(missing_ok=True)
        pathlib.Path(self.mod_directory + os.sep + self.merge_mod + os.sep + self.entry + ".arc").unlink(missing_ok=True)
        if os.path.exists(self.mod_directory + os.sep + self.merge_mod + os.sep + self.entry):
            shutil.rmtree(os.path.normpath(self.mod_directory + os.sep + self.merge_mod + os.sep + self.entry))
        return

class mergeThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(str)
        
class mergeThreadWorker(QRunnable):
    def __init__(self, executable, currentProfile, gameDataDirectory, modDirectory, modList, arcFolderPath):
        self.executable = executable
        self.profileName = currentProfile
        self.game_directory = gameDataDirectory
        self.mod_directory = modDirectory
        self.modList = modList
        self.arcFolderPath = arcFolderPath
        self.signals = mergeThreadWorkerSignals()
        super(mergeThreadWorker, self).__init__()
    
    @pyqtSlot()
    def run(self):
        extract_args = "-x -pc -dd -alwayscomp -txt -v 7"
        compress_args = "-c -pc -dd -alwayscomp -txt -v 7"
        arcFolderPath_parent = os.path.dirname(self.arcFolderPath)
        executablePath, executableName = os.path.split(self.executable)
        arctool_mod = os.path.relpath(executablePath, self.mod_directory).split(os.path.sep, 1)[0]
        merge_mod = 'Merged ARC - ' + self.profileName
        log_out = ""
        
        # copy vanilla arc to temp, extract, then delete if not already done
        extractedARCfolder = pathlib.Path(executablePath + os.sep + self.arcFolderPath)
        if not (os.path.isdir(extractedARCfolder)):
            log_out += f'Extracting vanilla ARC: {self.arcFolderPath + ".arc"}'
            if (os.path.isfile(os.path.join(self.game_directory, self.arcFolderPath + ".arc"))):
                pathlib.Path(executablePath + os.sep +  arcFolderPath_parent).mkdir(parents=True, exist_ok=True)
                shutil.copy(self.game_directory + os.sep + self.arcFolderPath + ".arc", executablePath + os.sep + arcFolderPath_parent)
                output = os.popen('"' + self.executable + '" ' + extract_args + ' "' + os.path.normpath(executablePath + os.sep + self.arcFolderPath + ".arc" + '"')).read()
                #log_out += output + '\n'
                # remove .arc file
                os.remove(os.path.normpath(executablePath + os.sep + self.arcFolderPath + ".arc"))

        # # create the output folder
        pathlib.Path(self.mod_directory + os.sep + merge_mod + os.sep + arcFolderPath_parent).mkdir(parents=True, exist_ok=True)

        # # copy .arc compression order txt and vanilla files
        log_out += f'\nCopying {self.arcFolderPath}.arc.txt\n'
                
        shutil.copy(os.path.normpath(executablePath + os.sep + self.arcFolderPath + ".arc.txt"), os.path.normpath(self.mod_directory + os.sep + merge_mod + os.sep + arcFolderPath_parent))
        log_out += "Merging vanilla files\n"
        shutil.copytree(os.path.normpath(executablePath + os.sep + self.arcFolderPath), os.path.normpath(self.mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath), dirs_exist_ok=True)

        # # copy mod files to merge folder
        for mod_name in self.modList:
            childModARCpath = pathlib.Path(str(self.mod_directory + os.sep + mod_name) + os.sep + self.arcFolderPath)
            if pathlib.Path(childModARCpath).exists() and not mod_name == merge_mod:
                log_out += f'Merging mod: {mod_name}\n'
                
                shutil.copytree(os.path.normpath(self.mod_directory + os.sep + mod_name + os.sep + self.arcFolderPath), os.path.normpath(self.mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath), dirs_exist_ok=True)
                if mod_name != arctool_mod:
                    # remove .arc.txt
                    pathlib.Path(self.mod_directory + os.sep + mod_name + os.sep + self.arcFolderPath + ".arc.txt").unlink(missing_ok=True)

        # compress
        output = os.popen('"' + self.executable + '" ' + compress_args + ' "' + os.path.normpath(self.mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath) + '"').read()
        #log_out += output + '\n'
            
        # remove folders and txt
        log_out += "Removing temp files\n"
        shutil.rmtree(os.path.normpath(self.mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath))
        os.remove(os.path.normpath(self.mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath + '.arc.txt'))

        log_out += "ARC merge complete"        
        self.signals.result.emit(log_out)  # Return logs                
        self.signals.finished.emit()  # Done        
        return

def createPlugin():
    return ARCToolCompress()
