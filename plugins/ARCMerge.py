# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'ARC Merge' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# copies vanilla arc files from game folder to arc tool folder, copies all arc folder files in all mods installed to merge folder, compresses to .arc, then exits

import os
import sys
import json
import shutil
import filecmp
import logging
import pathlib
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
    
    arcFoldersPrevBuildDict = defaultdict(list)
    arcFoldersCurrentDict = defaultdict(list)
    threadCancel = False
    
    def __init__(self):
        super(ARCToolCompress, self).__init__()
        self._organizer = None
        self.__parentWidget = None

    def init(self, organizer):
        self._organizer = organizer
        self.threadpool = QThreadPool()
        self.currentIndex = 0
        self.myProgressD = None
        self.logger = None        
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
        return mobase.VersionInfo(2, 0, 0, 0)

    def requirements(self):
        return [
            mobase.PluginRequirementFactory.gameDependency("Dragon's Dogma: Dark Arisen")
        ]

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.mainToolName(), "enabled")

    def settings(self):
        return []

    def displayName(self):
        return self.__tr("ARC Merge")

    def tooltip(self):
        return self.__tr("Merge extracted .arc files")

    def icon(self):
        ARCToolPath = self._organizer.pluginSetting(self.mainToolName(), "ARCTool-path")
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
        if not bool(self._organizer.pluginSetting(self.mainToolName(), "initialised")):
            # reset all            
            self._organizer.setPluginSetting(self.mainToolName(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.mainToolName(), "remove-ITM", True)
            self._organizer.setPluginSetting(self.mainToolName(), "delete-ARC", True)
            self._organizer.setPluginSetting(self.mainToolName(), "log-enabled", False)
            self._organizer.setPluginSetting(self.mainToolName(), "verbose-log", False)
            self._organizer.setPluginSetting(self.mainToolName(), "max-threads", self.threadpool.maxThreadCount())
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
        self._organizer.setPluginSetting(self.mainToolName(), "initialised", True)
        
        # logger setup
        arctool_path = self._organizer.pluginSetting(self.mainToolName(), "ARCTool-path")
        log_file = os.path.dirname(arctool_path) + "\\ARCMerge.log"
        self.logger = logging.getLogger('ae_logger')
        f_handler = logging.FileHandler(log_file, 'w+')
        f_handler.setLevel(logging.DEBUG)
        f_format = logging.Formatter('%(asctime)s %(message)s')
        f_handler.setFormatter(f_format)
        self.logger.addHandler(f_handler)
        self.logger.propagate = False
        
        # run the stuff
        self.__process_mods(executable)

    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)

    def get_arctool_path(self):
        savedPath = self._organizer.pluginSetting(self.mainToolName(), "ARCTool-path")
        # ARCTool must be installed within the game's data directory or a mod folder
        mod_directory = self.__getModDirectory()
        gameDataDirectory = pathlib.Path(self._organizer.managedGame().dataDirectory().absolutePath())
        pathlibPath = pathlib.Path(savedPath)
        
        if not os.path.exists(pathlibPath):
            self._organizer.setPluginSetting(self.mainToolName(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.mainToolName(), "initialised", False)
            raise ARCToolMissingException
        inGoodLocation = self.__withinDirectory(pathlibPath, mod_directory)
        inGoodLocation |= self.__withinDirectory(pathlibPath, gameDataDirectory)
        if not pathlibPath.is_file() or not inGoodLocation:
            QMessageBox.information(self.__parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool path invalid or not set. \n\nARCTool must be visible within the VFS, so choose an installation within a mod folder. \n\nThis setting can be updated in the Plugins tab of the Mod Organizer Settings menu."))
            while True:
                path = QFileDialog.getOpenFileName(self.__parentWidget, self.__tr("Locate ARCTool.exe"), str(mod_directory), "ARCTool.exe")[0]
                if path == "":
                    # Cancel was pressed
                    raise ARCToolInvalidPathException
                pathlibPath = pathlib.Path(path)
                inGoodLocation = self.__withinDirectory(pathlibPath, mod_directory)
                inGoodLocation |= self.__withinDirectory(pathlibPath, gameDataDirectory)
                if pathlibPath.is_file() and inGoodLocation:
                    self._organizer.setPluginSetting(self.mainToolName(), "ARCTool-path", path)
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

    def __process_mods(self, executable): # called from display()
        self.arcFoldersPrevBuildDict.clear()
        self.arcFoldersCurrentDict.clear()
        mod_directory = self.__getModDirectory()
        executable_path = self.get_arctool_path()
        arctool_mod = os.path.relpath(executable_path, mod_directory).split(os.path.sep, 1)[0]

        # reset cancelled flag
        ARCToolCompress.threadCancel = False
        # get mod active list
        active_mod_list = []
        modlist = self._organizer.modList()
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if mod_name != arctool_mod and 'Merged ARC' not in mod_name:
                    active_mod_list.append(mod_name)
         # set mod count for progress
        self.myProgressD = QProgressDialog(self.__tr("Scanning..."), self.__tr("Cancel"), 0, 0, self.__parentWidget)
        self.myProgressD.setFixedWidth(300)
        self.myProgressD.setMaximum(len(active_mod_list))
        self.myProgressD.forceShow()
        # set max thread count
        self.threadpool.setMaxThreadCount(self._organizer.pluginSetting(self.mainToolName(), "max-threads"))
        # start single scan thread
        worker = scanThreadWorker(self._organizer, active_mod_list)
        worker.signals.progress.connect(self.scanThreadWorkerProgress)
        worker.signals.result.connect(self.scanThreadWorkerOutput)
        worker.signals.finished.connect(self.scanThreadWorkerComplete)
        # Execute
        self.threadpool.start(worker)        
        
    def mergeArcFiles(self):
        merge_needed_count = 0
        # process changed merges from dictionary
        for entry in self.arcFoldersCurrentDict:
            if entry not in self.arcFoldersPrevBuildDict or self.arcFoldersCurrentDict[entry] != self.arcFoldersPrevBuildDict[entry]:
                # Pass the function to execute
                worker = mergeThreadWorker(self._organizer, self.arcFoldersCurrentDict[entry], entry)
                worker.signals.result.connect(self.mergeThreadWorkerOutput)
                worker.signals.finished.connect(self.mergeThreadWorkerComplete)
                # Execute
                self.threadpool.start(worker)
                merge_needed_count += 1
        if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
            self.logger.debug(f'ARC merge count: {merge_needed_count}')
        # progress reinit
        self.myProgressD.setLabelText(f'Merging...')
        self.myProgressD.setValue(0)
        self.myProgressD.setMaximum(merge_needed_count)
        self.myProgressD.forceShow()

        if merge_needed_count == 0:
            self.modCleanup()

    def modCleanup(self):
        executable_path = self.get_arctool_path()
        mod_directory = self.__getModDirectory()
        merge_mod = 'Merged ARC - ' + self._organizer.profileName()
        arctool_mod = os.path.relpath(executable_path, mod_directory).split(os.path.sep, 1)[0]
        self.myProgressD.setLabelText(self.__tr("Cleaning up..."))
        
        if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
            self.logger.debug(f'Cleaning up...')
        # remove stale .arc files from merged folder
        for entry in self.arcFoldersPrevBuildDict:
            if (self.myProgressD.wasCanceled()):
                if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
                        self.logger.debug("Merge cancelled")
                return
            if entry not in self.arcFoldersCurrentDict:
                if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
                        self.logger.debug(f'Deleting stale arc: {entry}')
                        # Pass the function to execute
                        worker = cleanupThreadWorker(self._organizer, entry)
                        # Execute
                        self.threadpool.start(worker)
        # write arc merge info to json
        try:
            with open(mod_directory + os.sep + merge_mod + os.sep + 'arcFileMerge.json', 'w') as file_handle:
                json.dump(self.arcFoldersCurrentDict, file_handle)
        except:
            if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
                self.logger.debug("arcFileMerge.json not found or invalid")

        #enable merge mod
        self._organizer.modList().setActive(merge_mod, True)
        self.myProgressD.hide()
        QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr("Merge complete"))
    
    def scanThreadWorkerProgress(self, progress): # called after each mod is scanned in scanThreadWorker()
        if (self.myProgressD.wasCanceled()):
            ARCToolCompress.threadCancel = True
        else:
            self.myProgressD.setValue(progress)

    def scanThreadWorkerComplete(self): # called after completion of scanThreadWorker()
        if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
            self.logger.debug(f'Scan complete')
            self.logger.debug(f'Previous count: {len(self.arcFoldersPrevBuildDict)}')
            self.logger.debug(f'Current count: {len(self.arcFoldersCurrentDict)}')
        # start merge
        self.mergeArcFiles()

    def scanThreadWorkerOutput(self, log_out):
        if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
            self.logger.debug(log_out)

    def mergeThreadWorkerComplete(self):
        self.currentIndex += 1
        if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
            self.logger.debug(f'Merge index: {self.currentIndex} : {self.myProgressD.maximum()}')
        if self.currentIndex == self.myProgressD.maximum():
            self.currentIndex = 0
            self.modCleanup()
        if (self.myProgressD.wasCanceled()):
            self.currentIndex = 0
            ARCToolCompress.threadCancel = True
        else:
            self.myProgressD.setValue(self.currentIndex)

    def mergeThreadWorkerOutput(self, log_out):
        if bool(self._organizer.pluginSetting(self.mainToolName(), "log-enabled")):
            self.logger.debug(log_out)

    def __getModDirectory(self):
        return self._organizer.modsPath()

    @staticmethod
    def __withinDirectory(innerPath, outerDir):
        for path in innerPath.parents:
            if path.samefile(outerDir):
                return True
        return False

    @staticmethod
    def mainToolName():
        return "ARC Extract"
        
class scanThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    progress = pyqtSignal(int)
    result = pyqtSignal(str)

class scanThreadWorker(QRunnable):
    def __init__(self, organizer, active_mod_list):
        self._organizer = organizer
        self.active_mod_list = active_mod_list
        self.signals = scanThreadWorkerSignals()
        super(scanThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        mod_directory = self._organizer.modsPath()
        modlist = self._organizer.modList()
        merge_mod = 'Merged ARC - ' + self._organizer.profileName()
        executable = self._organizer.pluginSetting(ARCToolCompress.mainToolName(), "ARCTool-path")
        executable_path, executableName = os.path.split(executable)
        arctool_mod = os.path.relpath(executable_path, mod_directory).split(os.path.sep, 1)[0]
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        
        # load previous arc merge info
        try:
            with open(mod_directory + os.sep + merge_mod + os.sep + 'arcFileMerge.json', 'r') as file_handle:
                ARCToolCompress.arcFoldersPrevBuildDict = json.load(file_handle)
        except:
            if bool(self._organizer.pluginSetting(ARCToolCompress.mainToolName(), "log-enabled")):
                self.logger.debug("arcFileMerge.json not found or invalid")
        log_out = "\n"
        mods_scanned = 0        
        # build list of current active mod arc folders to merge        
        for mod_name in self.active_mod_list:
            # check for cancellation
            if (ARCToolCompress.threadCancel):
                return
            mods_scanned += 1
            self.signals.progress.emit(mods_scanned) # update progress
            log_out += f'Scanning: {mod_name}\n'
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if mod_name != arctool_mod and mod_name != merge_mod:
                    for dirpath, dirnames, filenames in os.walk(mod_directory + os.path.sep + mod_name):
                        # check for extracted arc folders
                        for folder in dirnames:
                            arcFolder = dirpath + os.path.sep + folder
                            relative_path = os.path.relpath(arcFolder, mod_directory).split(os.path.sep, 1)[1]
                            if (os.path.isfile(os.path.normpath(game_directory + os.path.sep + relative_path + ".arc"))):
                                if bool(self._organizer.pluginSetting(ARCToolCompress.mainToolName(), "verbose-log")):
                                    log_out += f'ARC Folder: {relative_path}\n'
                                if mod_name not in ARCToolCompress.arcFoldersCurrentDict[relative_path]:
                                    ARCToolCompress.arcFoldersCurrentDict[relative_path].append(mod_name)
        self.signals.result.emit(log_out)  # Return log
        self.signals.finished.emit()  # Done                            
        return
        
class cleanupThreadWorker(QRunnable):
    def __init__(self, organizer, entry):
        self._organizer = organizer
        self.entry = entry
        super(cleanupThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        mod_directory = self._organizer.modsPath()
        merge_mod = 'Merged ARC - ' + self._organizer.profileName()
        executable = self._organizer.pluginSetting(ARCToolCompress.mainToolName(), "ARCTool-path")
        executable_path, executableName = os.path.split(executable)
        arctool_mod = os.path.relpath(executable_path, mod_directory).split(os.path.sep, 1)[0]
        # clean arctool
        pathlib.Path(mod_directory + os.sep + arctool_mod + os.sep + self.entry + ".arc.txt").unlink(missing_ok=True)
        pathlib.Path(mod_directory + os.sep + arctool_mod + os.sep + self.entry + ".arc").unlink(missing_ok=True)
        if os.path.exists(mod_directory + os.sep + arctool_mod + os.sep + self.entry):
            shutil.rmtree(os.path.normpath(mod_directory + os.sep + arctool_mod + os.sep + self.entry))
        # clean merge
        pathlib.Path(mod_directory + os.sep + merge_mod + os.sep + self.entry + ".arc.txt").unlink(missing_ok=True)
        pathlib.Path(mod_directory + os.sep + merge_mod + os.sep + self.entry + ".arc").unlink(missing_ok=True)
        if os.path.exists(mod_directory + os.sep + merge_mod + os.sep + self.entry):
            shutil.rmtree(os.path.normpath(mod_directory + os.sep + merge_mod + os.sep + self.entry))
        return

class mergeThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(str)

class mergeThreadWorker(QRunnable):
    def __init__(self, organizer, mods_to_merge, arcFolderPath):
        self._organizer = organizer
        self.mods_to_merge = mods_to_merge
        self.arcFolderPath = arcFolderPath
        self.signals = mergeThreadWorkerSignals()
        super(mergeThreadWorker, self).__init__()
        
    @pyqtSlot()
    def run(self):
        if (ARCToolCompress.threadCancel):
            log_out += f'Merge cancelled\n'
            return
        extract_args = "-x -pc -dd -alwayscomp -txt -v 7"
        compress_args = "-c -pc -dd -alwayscomp -txt -v 7"
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        mod_directory = self._organizer.modsPath()
        arcFolderPath_parent = os.path.dirname(self.arcFolderPath)
        executable = self._organizer.pluginSetting(ARCToolCompress.mainToolName(), "ARCTool-path")
        executable_path, executableName = os.path.split(executable)
        arctool_mod = os.path.relpath(executable_path, mod_directory).split(os.path.sep, 1)[0]
        merge_mod = 'Merged ARC - ' + self._organizer.profileName()
        log_out = ""
        # copy vanilla arc to temp, extract, then delete if not already done
        extractedARCfolder = pathlib.Path(executable_path + os.sep + self.arcFolderPath)
        if not (os.path.isdir(extractedARCfolder)):
            log_out += f'Extracting vanilla ARC: {self.arcFolderPath + ".arc"}'
            if (os.path.isfile(os.path.join(game_directory, self.arcFolderPath + ".arc"))):
                pathlib.Path(executable_path + os.sep +  arcFolderPath_parent).mkdir(parents=True, exist_ok=True)
                shutil.copy(game_directory + os.sep + self.arcFolderPath + ".arc", executable_path + os.sep + arcFolderPath_parent)
                output = os.popen('"' + executable + '" ' + extract_args + ' "' + os.path.normpath(executable_path + os.sep + self.arcFolderPath + ".arc" + '"')).read()
                if bool(self._organizer.pluginSetting(ARCToolCompress.mainToolName(), "verbose-log")):
                    log_out += "------ start arctool output ------\n"
                    log_out += output + "------ end arctool output ------\n"
                # remove .arc file
                os.remove(os.path.normpath(executable_path + os.sep + self.arcFolderPath + ".arc"))
        # create the output folder
        pathlib.Path(mod_directory + os.sep + merge_mod + os.sep + arcFolderPath_parent).mkdir(parents=True, exist_ok=True)
        # copy .arc compression order txt
        log_out += f'\nCopying {self.arcFolderPath}.arc.txt\n'
        # copy vanilla files
        shutil.copy(os.path.normpath(executable_path + os.sep + self.arcFolderPath + ".arc.txt"), os.path.normpath(mod_directory + os.sep + merge_mod + os.sep + arcFolderPath_parent))
        log_out += "Merging vanilla files\n"
        shutil.copytree(os.path.normpath(executable_path + os.sep + self.arcFolderPath), os.path.normpath(mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath), dirs_exist_ok=True)
        # copy mod files to merge folder
        for mod_name in self.mods_to_merge:
            childModARCpath = pathlib.Path(str(mod_directory + os.sep + mod_name) + os.sep + self.arcFolderPath)
            if pathlib.Path(childModARCpath).exists() and not mod_name == merge_mod:
                log_out += f'Merging mod: {mod_name}\n'
                shutil.copytree(os.path.normpath(mod_directory + os.sep + mod_name + os.sep + self.arcFolderPath), os.path.normpath(mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath), dirs_exist_ok=True)
            if os.path.isfile(mod_directory + os.sep + mod_name + os.sep + self.arcFolderPath + ".arc.txt"):
                log_out += f'Copying {mod_name} {self.arcFolderPath}.arc.txt\n'
                shutil.copy(os.path.normpath(mod_directory + os.sep + mod_name + os.sep + self.arcFolderPath + ".arc.txt"), os.path.normpath(mod_directory + os.sep + merge_mod + os.sep + arcFolderPath_parent))
        # compress
        output = os.popen('"' + executable + '" ' + compress_args + ' "' + os.path.normpath(mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath) + '"').read()
        if bool(self._organizer.pluginSetting(ARCToolCompress.mainToolName(), "verbose-log")):
            log_out += "------ start arctool output ------\n"
            log_out += output + "------ end arctool output ------\n"
        # remove folders and txt
        log_out += "Removing temp files\n"
        shutil.rmtree(os.path.normpath(mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath))
        os.remove(os.path.normpath(mod_directory + os.sep + merge_mod + os.sep + self.arcFolderPath + '.arc.txt'))
        # finished
        log_out += "ARC merge complete"
        self.signals.result.emit(log_out)  # Return logs
        self.signals.finished.emit()  # Done
        return

def createPlugin():
    return ARCToolCompress()
